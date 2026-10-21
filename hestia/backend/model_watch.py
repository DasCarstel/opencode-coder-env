#!/usr/bin/env python3
"""model_watch.py — Modell-Wächter: echten Hermes-Verkehr bewerten, schlechte
Modelle sperren und (nach einem Dry-Run-Fenster) die Modellzuweisung umstellen.

Quelle ist das LOG des Hermes-Containers (agent.log), kein Probe-Call: Der
Wächter parst inkrementell API-Calls (in/out/Latenz), Stream-Stalls ("Stream
stale for …"), Turn-Enden und ERROR-Zeilen. Alles landet in einer SQLite-DB
unter /app/data (nur Metriken/Refs/Zeiten — nie Nachrichteninhalte, nie
Secrets) und wird dort pro Modell aggregiert.

Policy: Fenster (Standard 24 h), Schwellen für p95/p50, Stalls und
Error-Rate, Quarantäne-/Cooldown-Dauern und eine Preferred-Liste für
Ersatzmodelle. Defaults in DEFAULT_POLICY, überschreibbar über
/app/data/model-watch-policy.json und Env (HESTIA_MODEL_WATCH_*).

Degraded ist ein Modell (mit min_calls je Kanal und p50-Guard gegen
Ausreißer-Trigger), wenn:
  - p95 UND p50 über den Latenz-Schwellen, oder
  - >= stall_limit Stalls im Fenster, oder
  - Error-Rate > error_rate (Provider-Auth-Fehler zählen NICHT als
    Modellfehler — sie sind ein Konto-/Provider-Problem).

Sperrliste: /app/data/model-watch.json mit
  {"blocked": [{model, provider, reason, blocked_at, until, snapshot}]}.
Abgelaufene Einträge (until < now) fliegen automatisch raus; beim erneuten
Verstoß wird neu gesperrt.

Entscheidung & Durchsetzung: Der Ist-Zustand kommt ausschließlich aus
hermes._public(hermes.config(force=True)) — es gibt keine zweite Version der
Wahrheit. Blockierte Modelle werden aus primary/fallback/profiles/voice
entfernt (Voice-Route → erster nicht blockierter Preferred-Kandidat; Primary
→ erster nicht blockierter Fallback). Die Änderungen landen als
`pending`-Liste im State; angewendet wird NUR per hermes.apply() und nur,
wenn (a) das Dry-Run-Fenster vorbei ist (enforce_from = erster Start +
HESTIA_MODEL_WATCH_DRY_RUN_HOURS, Standard 48 h), (b) der Cooldown seit dem
letzten Apply verstrichen ist und (c) pending NICHT leer ist. Sonst wird nur
geplant (Dry-Run) bzw. gewartet.

Es wird nie an platform_toolsets/skills/agent/providers gearbeitet — das
Payload entsteht 1:1 aus der _public()-Form, nur die Modellfelder wandern
rein.
"""
import copy
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

# Europe/Berlin-Zeitstempel der Logs → UTC. Fehlt tzdata (kein ZoneInfo),
# bleibt es Best-Effort (Container-Lokalzeit) — für das 24-h-Fenster ist der
# ggf. kleine Drift verständlicher als ein Totalausfall der Statistik.
try:
    from zoneinfo import ZoneInfo
    _BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # noqa: BLE001
    _BERLIN = None

FMT = "%Y-%m-%dT%H:%M:%S"             # gespeicherte (UTC-)Zeiten in der DB


def _log(msg):
    print(f"[hestia-model-watch] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}",
          flush=True)


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def _now_ts(ts: float | None = None) -> str:
    """Gespeichertes Zeitformat (UTC, sekundengenau)."""
    return time.strftime(FMT, time.gmtime(time.time() if ts is None else ts))


# ── Konfiguration (Env mit Defaults) ────────────────────────────────────────

ENABLED = os.environ.get("HESTIA_MODEL_WATCH", "1") == "1"
INTERVAL = _int_env("HESTIA_MODEL_WATCH_INTERVAL", 300)
# agent.log des Hermes-Containers (docker-compose: /etc/docker/hermes/data/logs
# read-only unter /hermes-logs).
LOG_DIR = os.environ.get("HESTIA_MODEL_WATCH_LOG_DIR", "/hermes-logs")
LOG_FILE = os.path.join(LOG_DIR, "agent.log")
FIRST_READ_BYTES = _int_env("HESTIA_MODEL_WATCH_FIRST_READ", 5 * 1024 * 1024)
MAX_READ_BYTES = 32 * 1024 * 1024
DRY_RUN_HOURS = _int_env("HESTIA_MODEL_WATCH_DRY_RUN_HOURS", 48)
PRUNE_DAYS = 14
# ntfy: gleicher Kanal wie Hermes (platforms.ntfy in der Live-Config). Bewusst
# nur eine URL (kein Secret) — ohne gesetzte URL bleibt es beim Log-Eintrag.
NTFY_URL = os.environ.get("HESTIA_NTFY_URL", "").strip()

# SQLite (Metriken, nie Inhalte) + Sperrliste/Policy/State im /app/data-Mount.
DB_PATH = os.environ.get("HESTIA_MODEL_WATCH_DB", "/app/data/model-watch.db")
BLOCK_PATH = os.environ.get("HESTIA_MODEL_WATCH_PATH",
                            "/app/data/model-watch.json")
POLICY_PATH = os.environ.get("HESTIA_MODEL_WATCH_POLICY_PATH",
                             "/app/data/model-watch-policy.json")
STATE_PATH = os.environ.get("HESTIA_MODEL_WATCH_STATE_PATH",
                            "/app/data/model-watch-state.json")

# Policy-Defaults (überschreibbar per model-watch-policy.json und Env).
DEFAULT_POLICY = {
    "window_hours": _int_env("HESTIA_MODEL_WATCH_WINDOW_HOURS", 24),
    "min_calls_latency": _int_env("HESTIA_MODEL_WATCH_MIN_CALLS_LATENCY", 20),
    "min_calls_stall": _int_env("HESTIA_MODEL_WATCH_MIN_CALLS_STALL", 5),
    "min_calls_error": _int_env("HESTIA_MODEL_WATCH_MIN_CALLS_ERROR", 10),
    "p95_ms": _int_env("HESTIA_MODEL_WATCH_P95_MS", 6000),
    "p50_ms": _int_env("HESTIA_MODEL_WATCH_P50_MS", 4000),
    "stall_limit": _int_env("HESTIA_MODEL_WATCH_STALL_LIMIT", 2),
    "error_rate": _float_env("HESTIA_MODEL_WATCH_ERROR_RATE", 0.10),
    "quarantine_hours": _int_env("HESTIA_MODEL_WATCH_QUARANTINE_HOURS", 24),
    "cooldown_hours": _int_env("HESTIA_MODEL_WATCH_COOLDOWN_HOURS", 6),
    "dry_run_hours": DRY_RUN_HOURS,
    "preferred": [
        p.strip() for p in os.environ.get(
            "HESTIA_MODEL_WATCH_PREFERRED",
            "opencode-go/glm-5.3-flash,opencode-go/glm-5.3").split(",")
        if p.strip()
    ],
}


def load_policy() -> dict:
    """Policy = Defaults < Datei < Env. Unbekannte Keys fliegen raus."""
    pol = dict(DEFAULT_POLICY)
    try:
        with open(POLICY_PATH, encoding="utf-8") as f:
            src = json.load(f)
        if isinstance(src, dict):
            for key in DEFAULT_POLICY:
                if key in src:
                    pol[key] = src[key]
    except (OSError, ValueError):
        pass
    pref = pol.get("preferred")
    if isinstance(pref, str):
        pref = [p.strip() for p in pref.split(",") if p.strip()]
    pol["preferred"] = [str(p) for p in (pref or []) if str(p).strip()]
    for key in ("window_hours", "min_calls_latency", "min_calls_stall",
                "min_calls_error", "p95_ms", "p50_ms", "stall_limit",
                "quarantine_hours", "cooldown_hours", "dry_run_hours"):
        try:
            pol[key] = int(pol[key])
        except (TypeError, ValueError):
            pol[key] = DEFAULT_POLICY[key]
    try:
        pol["error_rate"] = float(pol["error_rate"])
    except (TypeError, ValueError):
        pol["error_rate"] = DEFAULT_POLICY["error_rate"]
    # Env gewinnt schlussendlich (DEFAULT_POLICY liest es schon; hier schützt
    # es vor versehentlichen Gegenläufen über die Policy-Datei).
    for key in DEFAULT_POLICY:
        if key == "preferred":
            continue
        env_key = f"HESTIA_MODEL_WATCH_{key.upper()}"
        if env_key in os.environ:
            try:
                pol[key] = (float(os.environ[env_key]) if key == "error_rate"
                            else int(os.environ[env_key]))
            except (TypeError, ValueError):
                pass
    return pol


# ── Zeitstempel: Berlin-Logs → gespeichertes UTC-Format ─────────────────────

_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})")


def line_ts(raw: str) -> str | None:
    """Hermes-Log-Zeitstempel (Europe/Berlin) → gespeichertes UTC-FMT."""
    m = _TS_RE.match(str(raw or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    try:
        if _BERLIN is not None:
            epoch = datetime(y, mo, d, h, mi, s, tzinfo=_BERLIN).timestamp()
        else:
            epoch = time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
        return time.strftime(FMT, time.gmtime(epoch))
    except (OverflowError, OSError, ValueError):
        return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}"


def ts_epoch(stored: str | None) -> float | None:
    """Epoch-Sekunden aus dem gespeicherten FMT-String (UTC); None bei Müll."""
    if not stored:
        return None
    try:
        dt = datetime.strptime(str(stored), FMT)
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


# ── Log-Parser (echte Hermes-Zeilen) ────────────────────────────────────────

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<level>[A-Z]+) \[(?P<sid>[^\]]*)\] "
    r"(?P<logger>[\w.]+): (?P<msg>.*)$")

RE_MODEL = re.compile(r"\bmodel=([^\s,]+)")
RE_PROVIDER = re.compile(r"\bprovider=([^\s,]+)")
RE_LATENCY = re.compile(r"\blatency=(\d+(?:\.\d+)?)s\b")
RE_IN = re.compile(r"\bin=(\d+)")
RE_OUT = re.compile(r"\bout=(\d+)")
RE_STALL = re.compile(r"Stream stale for (\d+(?:\.\d+)?)s")
RE_API_CALLS = re.compile(r"\bapi_calls=(\d+)(?:/(\d+))?")
RE_TOOL_TURNS = re.compile(r"\btool_turns=(\d+)")
RE_SESSION = re.compile(r"\bsession=(\S+)")
# Provider-Auth-Fehler (z. B. opencode-zen) sind Konto-/Provider-Probleme,
# KEINE Modellfehler — sie kippen die Error-Rate eines Modells nie.
AUTH_PROVIDERS = {"opencode-zen"}


def parse_line(line: str) -> list:
    """Eine Hermes-Zeile in Events (call/stall/error/turn) überführen.

    `model` ist immer `provider/model`, wenn die Zeile beides nennt; fehlt
    der Provider (Stall-/Turn-Zeilen nennen nur model=), bleibt der nackte
    Name — die Aggregation matcht Suffixe (s. _model_match).
    """
    m = LINE_RE.match(str(line or "").rstrip("\n"))
    if not m:
        return []
    ts = line_ts(m.group("ts"))
    level, msg = m.group("level"), m.group("msg")
    pm, mm = RE_PROVIDER.search(msg), RE_MODEL.search(msg)
    provider = pm.group(1) if pm else None
    model = mm.group(1) if mm else None
    if not model or not ts:
        return []
    ref = f"{provider}/{model}" if provider else model
    out = []

    # 1) API-Call: "API call #2: model=… provider=… in=… out=… latency=1.0s …"
    if "API call #" in msg:
        lm = RE_LATENCY.search(msg)
        lat_ms = int(round(float(lm.group(1)) * 1000)) if lm else None
        if lat_ms is not None:
            in_m, out_m = RE_IN.search(msg), RE_OUT.search(msg)
            out.append({
                "kind": "call", "ts": ts, "model": ref, "provider": provider,
                "latency_ms": lat_ms,
                "in_tokens": int(in_m.group(1)) if in_m else None,
                "out_tokens": int(out_m.group(1)) if out_m else None,
                "ok": 1,
            })
            return out

    # 2) Stream-Stall: "Stream stale for 8s … model=…"
    stm = RE_STALL.search(msg)
    if stm:
        out.append({"kind": "stall", "ts": ts, "model": ref,
                    "detail": f"Stream stale for {stm.group(1)}s"})

    # 3) ERROR-Zeilen: als Modellfehler werten (außer Provider-Auth)
    if level == "ERROR" and provider not in AUTH_PROVIDERS:
        out.append({"kind": "error", "ts": ts, "model": ref,
                    "detail": msg[:300]})

    # 4) Turn-Ende: "Turn ended: … api_calls=8/… tool_turns=7 session=…"
    if "Turn ended:" in msg:
        api = RE_API_CALLS.search(msg)
        tool = RE_TOOL_TURNS.search(msg)
        sess = RE_SESSION.search(msg)
        out.append({
            "kind": "turn", "ts": ts, "model": ref,
            "api_calls": int(api.group(1)) if api else None,
            "tool_turns": int(tool.group(1)) if tool else None,
            "session": sess.group(1) if sess else None,
        })
    return out


def parse_lines(lines) -> list:
    """Mehrere Zeilen → flache Event-Liste (pars: jede Zeile ergibt ≥0 Events)."""
    out = []
    for ln in lines or []:
        out.extend(parse_line(ln))
    return out


# ── SQLite: Metriken (nie Inhalte) ──────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT,
    latency_ms INTEGER,
    in_tokens INTEGER,
    out_tokens INTEGER,
    ok INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    model TEXT,
    api_calls INTEGER,
    tool_turns INTEGER,
    session TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_model ON events(model, kind);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);
"""

FILE_LOCK = threading.Lock()          # schützt State-/Block-/Policy-Dateien


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=5)
    try:
        con.executescript(_SCHEMA)
    except Exception:  # noqa: BLE001
        con.close()
        raise
    return con


def _db_write(rows: list) -> int:
    """Ein Commit pro Parse-Lauf; Zeilen älter als PRUNE_DAYS fliegen raus."""
    if not rows:
        return 0
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = _connect()
    try:
        for row in rows:
            kind = row.get("kind")
            if kind == "call":
                con.execute(
                    "INSERT INTO calls (ts, model, provider, latency_ms, "
                    "in_tokens, out_tokens, ok) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row["ts"], row["model"], row.get("provider"),
                     row.get("latency_ms"), row.get("in_tokens"),
                     row.get("out_tokens"), int(row.get("ok") or 0)))
            elif kind in ("stall", "error"):
                con.execute(
                    "INSERT INTO events (ts, kind, model, detail) "
                    "VALUES (?, ?, ?, ?)",
                    (row["ts"], kind, row["model"], row.get("detail")))
            elif kind == "turn":
                con.execute(
                    "INSERT INTO turns (ts, model, api_calls, tool_turns, "
                    "session) VALUES (?, ?, ?, ?, ?)",
                    (row["ts"], row["model"], row.get("api_calls"),
                     row.get("tool_turns"), row.get("session")))
        if PRUNE_DAYS:
            cutoff = _now_ts(time.time() - PRUNE_DAYS * 86400)
            for table in ("calls", "events", "turns"):
                con.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
        con.commit()
        return len(rows)
    finally:
        con.close()


# ── Statistik: p50/p95 (lineare Interpolation, kein numpy) + Degraded ───────

def _pctl(values, pct: int):
    """Perzentil (lineare Interpolation) über gültige Zahlen; None ohne Werte."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return int(round(vals[0]))
    k = (len(vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return int(round(vals[f] + (vals[c] - vals[f]) * (k - f)))


def _model_match(ev_model, ref) -> bool:
    """Event-Modell passend? (Stall-/Turn-Zeilen nennen nur model=.)"""
    if not ev_model or not ref:
        return False
    ev, tgt = str(ev_model), str(ref)
    if ev == tgt:
        return True
    return tgt.endswith("/" + ev) or ev.endswith("/" + tgt)


def _aggregate(call_rows, event_rows, cutoff: str | None = None) -> dict:
    """Pures Aggregat: [(ts, model, latency_ms, ok)] + [(ts, model, kind)]."""
    lat = defaultdict(list)
    models = {}                          # Reihenfolge der ersten Nennung
    last_seen = {}
    for ts, model, latency_ms, _ok in call_rows:
        if not model or (cutoff and (ts or "") < cutoff):
            continue
        lat[model].append(latency_ms if latency_ms is not None else 0)
        models[model] = True
        if (ts or "") > (last_seen.get(model) or ""):
            last_seen[model] = ts
    stalls_by = defaultdict(int)
    errors_by = defaultdict(int)
    for ts, model, kind in event_rows:
        if cutoff and (ts or "") < cutoff:
            continue
        # Suffix-Match: Event "glm-5.3-flash" gehört zu Call-Ref
        # "opencode-go/glm-5.3-flash" (und umgekehrt). Ohne Calls bekommt das
        # Event-Modell einen eigenen Eintrag (calls=0).
        matched = [ref for ref in models if _model_match(model, ref)]
        if not matched and model:
            matched = [model]
            models[model] = True
        for ref in matched:
            if kind == "stall":
                stalls_by[ref] += 1
            elif kind == "error":
                errors_by[ref] += 1
            if (ts or "") > (last_seen.get(ref) or ""):
                last_seen[ref] = ts
    out = {}
    for ref in models:
        vals = lat.get(ref) or []
        calls = len(vals)
        out[ref] = {
            "calls": calls,
            "p50Ms": _pctl(vals, 50),
            "p95Ms": _pctl(vals, 95),
            "stalls": stalls_by.get(ref, 0),
            "errors": errors_by.get(ref, 0),
            "errorRate": round(errors_by.get(ref, 0) / calls, 4) if calls else 0.0,
            "lastSeen": last_seen.get(ref),
        }
    return out


def model_stats(window_hours: int = 24) -> dict:
    """Pro Modell aus der DB: calls/p50/p95/stalls/errors/errorRate/lastSeen."""
    try:
        if not os.path.isfile(DB_PATH):
            return {}
        cutoff = _now_ts(time.time() - int(window_hours) * 3600)
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        try:
            calls = con.execute(
                "SELECT ts, model, latency_ms, ok FROM calls WHERE ts >= ?",
                (cutoff,)).fetchall()
            events = con.execute(
                "SELECT ts, model, kind FROM events WHERE ts >= ?",
                (cutoff,)).fetchall()
        finally:
            con.close()
        return _aggregate(calls, events, cutoff)
    except Exception as e:  # noqa: BLE001 — Statistik darf nie die Panel-API reißen
        _log(f"model_stats fehlgeschlagen: {type(e).__name__}: {e}")
        return {}


def _degraded_reasons(m: dict, pol: dict) -> list:
    """Trigger-Gründe für ein Stats-Modell (leer = ok)."""
    reasons = []
    calls = int(m.get("calls") or 0)
    p50, p95 = m.get("p50Ms"), m.get("p95Ms")
    if (calls >= pol["min_calls_latency"] and p95 is not None and p50 is not None
            and p95 > pol["p95_ms"] and p50 > pol["p50_ms"]):
        # p50-Guard: ein einzelner Ausreißer triggert nicht
        reasons.append(
            f"p95 {p95}ms > {pol['p95_ms']}ms UND p50 {p50}ms > "
            f"{pol['p50_ms']}ms")
    if (calls >= pol["min_calls_stall"]
            and (m.get("stalls") or 0) >= pol["stall_limit"]):
        reasons.append(
            f"{m.get('stalls')} Stalls (Limit {pol['stall_limit']}) "
            f"in {pol['window_hours']}h")
    if (calls >= pol["min_calls_error"]
            and (m.get("errorRate") or 0) > pol["error_rate"]):
        reasons.append(
            f"Error-Rate {float(m.get('errorRate') or 0) * 100:.0f}% > "
            f"{pol['error_rate'] * 100:.0f}% "
            f"({m.get('errors')}/{calls})")
    return reasons


def _degraded(m: dict, pol: dict) -> bool:
    return bool(_degraded_reasons(m, pol))


# ── Zustand/Sperrlisten: atomare JSON-Dateien ───────────────────────────────

def _load_json(path: str, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(fallback)) else fallback
    except Exception:  # noqa: BLE001 — fehlende Datei ist kein Fehlerfall
        return fallback


def _atomic_write(path: str, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)             # atomar: Reader sehen nie halbe Dateien


def _state_default_log() -> dict:
    return {"inode": None, "offset": 0, "lastLineAt": None}


def _load_state() -> dict:
    with FILE_LOCK:
        st = _load_json(STATE_PATH, {})
    if not isinstance(st, dict):
        st = {}
    st.setdefault("enforceFrom", None)
    st.setdefault("pending", [])
    st.setdefault("actions", [])
    st.setdefault("lastApplyAt", None)
    if not isinstance(st.get("log"), dict):
        st["log"] = _state_default_log()
    for key in ("pending", "actions"):
        if not isinstance(st[key], list):
            st[key] = []
    return st


def _save_state(st: dict):
    with FILE_LOCK:
        st["actions"] = (st.get("actions") or [])[-20:]
        _atomic_write(STATE_PATH, st)


def _load_blocks() -> dict:
    doc = _load_json(BLOCK_PATH, {})
    if not isinstance(doc, dict):
        doc = {}
    if not isinstance(doc.get("blocked"), list):
        doc["blocked"] = []
    return doc


def _save_blocks(doc: dict):
    _atomic_write(BLOCK_PATH, doc)


def _entry_ref(entry):
    """Modell-Ref aus einem Eintrag: {"provider","model"} oder "provider/model"."""
    if isinstance(entry, str):
        s = entry.strip()
        return s or None
    if isinstance(entry, dict):
        provider, model = entry.get("provider"), entry.get("model")
        if not provider and isinstance(model, str) and "/" in model:
            provider, _, model = model.partition("/")
        if provider and model:
            return f"{provider}/{model}"
    return None


def _split_ref(ref):
    if not ref:
        return None, None
    provider, _, model = str(ref).partition("/")
    if provider and model:
        return provider, model
    return None, str(ref)


def _prune_blocks() -> dict:
    """Abgelaufene Sperrungen (until < now) automatisch entfernen."""
    doc = _load_blocks()
    now = _now_ts()
    keep = [b for b in doc["blocked"]
            if isinstance(b, dict) and str(b.get("until") or "") >= now]
    if len(keep) != len(doc["blocked"]):
        doc["blocked"] = keep
        _save_blocks(doc)
    return doc


# ── State-Helfer: Dry-Run-Fenster + Cooldown ────────────────────────────────

def _ensure_enforce_from(st: dict, now_epoch: float | None = None):
    """Dry-Run-Fenster: ab erstem Start = now + dry_run_hours (einmalig)."""
    ef = ts_epoch(st.get("enforceFrom"))
    if ef is not None:
        return ef
    pol = load_policy()
    now_epoch = time.time() if now_epoch is None else now_epoch
    st["enforceFrom"] = _now_ts(now_epoch + int(pol["dry_run_hours"]) * 3600)
    return now_epoch + int(pol["dry_run_hours"]) * 3600


def _cooldown_active(now_epoch: float, pol: dict, last_apply_at) -> bool:
    epoch = ts_epoch(last_apply_at)
    if epoch is None:
        return False
    return (now_epoch - epoch) < int(pol["cooldown_hours"]) * 3600


# ── Log-Lesen: inkrementell, rotierfest, erster Lauf letzte 5 MB ────────────

def _parse_positions(log: dict, sinfo) -> tuple:
    """Start-Byte + Inode für den nächsten Leseatz (Rotation/Schrumpf-Check)."""
    inode, size = sinfo.st_ino, sinfo.st_size
    prev_inode = log.get("inode")
    prev_off = int(log.get("offset") or 0)
    if inode != prev_inode:
        start = max(0, size - FIRST_READ_BYTES)     # Rotation/erster Lauf
    elif size < prev_off:
        start = 0                                   # geschrumpft → neu lesen
    else:
        start = min(prev_off, size)
    return start, inode


def _read_lines(path: str, start: int, log: dict, inode: int) -> list:
    """Ab Byte-Offset lesen; Teilzeilen (Start/Ende) werden übersprungen."""
    lines = []
    new_off = start
    with open(path, "rb") as f:
        if start > 0:
            f.seek(start - 1)
            prev_byte = f.read(1)
            if prev_byte != b"\n":
                # Offset lag mitten in einer (Teil-)Zeile → bis zum nächsten
                # Newline überspringen, den Rest normal lesen.
                rest = f.read(MAX_READ_BYTES)
                nl = rest.find(b"\n")
                if nl < 0:
                    log["inode"] = inode
                    log["offset"] = start + len(rest)   # nur Teilzeile
                    return []
                start = start + nl + 1
        f.seek(start)
        blob = f.read(MAX_READ_BYTES)
        if not blob:
            log["inode"] = inode
            log["offset"] = start
            return []
        text = blob.decode("utf-8", "replace")
        if text.endswith("\n"):
            lines = text.splitlines()
            new_off = start + len(blob)
        else:
            # Zeile (Ende) unvollständig dem nächsten Lauf überlassen:
            idx = text.rfind("\n")
            if idx >= 0:
                lines = text[:idx].splitlines()
                new_off = start + idx + 1
    log["inode"] = inode
    log["offset"] = new_off
    return lines


def _ingest_logs(st: dict) -> int:
    """agent.log inkrementell parsen und einmalig in die DB committen."""
    log = st["log"]
    try:
        sinfo = os.stat(LOG_FILE)
    except OSError:
        log["missing"] = True
        return 0
    log.pop("missing", None)
    start, inode = _parse_positions(log, sinfo)
    lines = _read_lines(LOG_FILE, start, log, inode)
    rows = parse_lines(lines)
    if rows:
        log["lastLineAt"] = max(row["ts"] for row in rows)
        _db_write(rows)               # ein Commit pro Parse-Lauf
    return len(rows)


# ── Hermes: Ist-Zustand + Apply (einzige Schreib-API, lazy import) ──────────

def _current_public() -> dict:
    """Ist-Zustand: hermes._public(hermes.config(force=True))."""
    import hermes
    return hermes._public(hermes.config(force=True))


def _apply(payload: dict) -> dict:
    """Apply-Wrapper (Tests ersetzen dies); direkt hermes.apply()."""
    import hermes
    return hermes.apply(payload)


# ── Planung: Voice-Route, Primary/Fallback, Profile ─────────────────────────

def plan_changes(public: dict, blocked_refs, policy: dict | None = None):
    """Plan + pending-Liste für (blockierte) Modelle — reine Funktion.

    Liefert (pending, alarms, plan). plan enthält die von _build_payload
    erwarteten Ziel-Formen (None = unverändert). Blockierte Modelle dürfen
    nirgends stehen: primary, fallback, profiles(.primary/.fallback), voice.
    Gibt es unter "preferred" keinen Kandidaten: Alarm, keine Aktion.
    """
    pol = policy or load_policy()
    blocked = {str(r) for r in (blocked_refs or []) if r}
    pending, alarms = [], []
    plan = {"primary": None, "fallback": None, "profiles": {}, "voice": None}

    def _ref_dict(ref):
        p, m = _split_ref(ref)
        return {"provider": p, "model": m}

    def _fb_refs(fb_list):
        return [r for r in (_entry_ref(f) for f in fb_list if isinstance(f, dict))
                if r]

    # 1) Voice-Route aus platforms.api_server.extra.model_routes
    platforms = (public.get("platforms")
                 if isinstance(public.get("platforms"), dict) else {})
    api_server = (platforms.get("api_server")
                  if isinstance(platforms.get("api_server"), dict) else {})
    extra = (api_server.get("extra")
             if isinstance(api_server.get("extra"), dict) else {})
    routes = (extra.get("model_routes")
              if isinstance(extra.get("model_routes"), dict) else {})
    vref = _entry_ref(routes.get("hermes-voice"))
    if vref and vref in blocked:
        cand = next((c for c in pol.get("preferred") or [] if c not in blocked),
                    None)
        if cand:
            provider, _, model = cand.partition("/")
            plan["voice"] = {"provider": provider, "model": model}
            pending.append({"kind": "voice", "from": vref, "to": cand,
                            "reason": "Voice-Route blockiert"})
        else:
            alarms.append(
                "Voice-Route blockiert und kein nicht-blockierter "
                "Preferred-Kandidat — keine Aktion")

    # 2) Primary / Fallback (Top-Level)
    pref_ref = _entry_ref(public.get("primary") or {})
    fb_list = (public.get("fallback")
               if isinstance(public.get("fallback"), list) else [])
    fb_refs = _fb_refs(fb_list)
    fb_bad = [r for r in fb_refs if r in blocked]
    fb_good = [r for r in fb_refs if r not in blocked]
    if pref_ref and pref_ref in blocked:
        if fb_good:
            new_primary = fb_good[0]
            rest = fb_good[1:]
            plan["primary"] = _ref_dict(new_primary)
            plan["fallback"] = rest
            pending.append({"kind": "primary", "from": pref_ref,
                            "to": new_primary,
                            "reason": "Primary blockiert"})
            pending.append({"kind": "fallback",
                            "from": ",".join(fb_refs) or "—",
                            "to": ",".join(rest) or "—",
                            "reason": "Fallbacks ohne blockierte Einträge "
                                      "neu geschrieben"})
        else:
            alarms.append(
                "Primary blockiert, aber kein sauberer Fallback übrig — "
                "keine Aktion")
    elif fb_bad:
        plan["fallback"] = fb_good
        pending.append({"kind": "fallback", "from": ",".join(fb_refs),
                        "to": ",".join(fb_good) or "—",
                        "reason": "Blockierte Fallbacks entfernt"})

    # 3) Profile: gleiches Muster je eigenem primary/fallback
    profiles = (public.get("profiles")
                if isinstance(public.get("profiles"), dict) else {})
    for name, prof in profiles.items():
        if not isinstance(prof, dict):
            continue
        prof_primary = _entry_ref(prof.get("primary") or {})
        pfb = (prof.get("fallback")
               if isinstance(prof.get("fallback"), list) else [])
        pfb_refs = _fb_refs(pfb)
        pfb_bad = [r for r in pfb_refs if r in blocked]
        pfb_good = [r for r in pfb_refs if r not in blocked]
        if prof_primary and prof_primary in blocked:
            if pfb_good:
                plan["profiles"][name] = {
                    "primary": _ref_dict(pfb_good[0]),
                    "fallback": pfb_good[1:],
                }
                pending.append({"kind": f"profile:{name}:primary",
                                "from": prof_primary, "to": pfb_good[0],
                                "reason": "Profil-Primary blockiert"})
                pending.append({"kind": f"profile:{name}:fallback",
                                "from": ",".join(pfb_refs) or "—",
                                "to": ",".join(pfb_good[1:]) or "—",
                                "reason": "Profil-Fallbacks bereinigt"})
            else:
                # Kein Kandidat: Profil-Primary abgeben (erbt vom Top-Level —
                # das ist garantiert nicht blockiert, sonst Alarm oben).
                plan["profiles"][name] = {"primary": None, "fallback": []}
                pending.append({
                    "kind": f"profile:{name}:primary", "from": prof_primary,
                    "to": None,
                    "reason": "Profil-Primary blockiert und kein Ersatz — "
                              "Profil erbt vom Top-Level"})
        elif pfb_bad:
            plan["profiles"][name] = {
                "primary": (_ref_dict(prof_primary) if prof_primary else None),
                "fallback": pfb_good,
            }
            pending.append({"kind": f"profile:{name}:fallback",
                            "from": ",".join(pfb_refs),
                            "to": ",".join(pfb_good) or "—",
                            "reason": "Blockierte Profil-Fallbacks entfernt"})

    return pending, alarms, plan


def _build_payload(public: dict, plan: dict) -> dict:
    """Payload = 1:1 die _public()-Form, angereichert um geplante Changes.

    Bewusst KEINE weitere Änderung an platform_toolsets/skills/agent/
    providers/api_server/pool/restart — die kommen unverändert aus der
    Ist-Form (hermes.apply übernimmt/validiert sie).
    """
    payload = copy.deepcopy(public)
    if plan.get("primary"):
        payload["primary"] = plan["primary"]
    if plan.get("fallback") is not None:
        payload["fallback"] = [
            {"provider": p, "model": m}
            for p, m in (_split_ref(f) for f in plan["fallback"]) if p and m]
    for name, prof_plan in (plan.get("profiles") or {}).items():
        prof = (payload.get("profiles") or {}).get(name)
        if not isinstance(prof, dict):
            continue
        if prof_plan.get("primary") is None:
            prof.pop("primary", None)       # Profil erbt vom Top-Level
        else:
            prof["primary"] = prof_plan["primary"]
        prof["fallback"] = [
            {"provider": p, "model": m}
            for p, m in (_split_ref(f) for f in prof_plan.get("fallback", []))
            if p and m]
    if plan.get("voice"):
        payload.setdefault("platforms", {}).setdefault(
            "api_server", {}).setdefault("extra", {}).setdefault(
            "model_routes", {})["hermes-voice"] = plan["voice"]
    return payload


# ── ntfy (best-effort, ohne neuen Secret-Pfad) ──────────────────────────────

def _notify(msg: str):
    """Best-effort ntfy an den Hermes-Kanal; ohne HESTIA_NTFY_URL nur Log.

    Kein Token/Secret im Container: der Kanal ist derselbe, den Hermes über
    platforms.ntfy bereits nutzt. Fehler (401, Timeout) dürfen den Lauf nie
    stoppen.
    """
    _log(f"[ntfy] {msg}")
    if not NTFY_URL:
        return
    try:
        req = urllib.request.Request(
            NTFY_URL, data=msg.encode("utf-8"),
            headers={"Title": "Hestia Modell-Wächter", "Tags": "warning"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:  # noqa: BLE001 — Benachrichtigung ist best-effort
        _log(f"ntfy fehlgeschlagen: {type(e).__name__}: {e}")


def _notify_block(ref: str, entry: dict, pol: dict, dry_run: bool):
    snap = entry.get("snapshot") or {}
    reason = (entry.get("reason") or "").split("; ")[-1]
    _notify(
        f"Model gesperrt: {ref} — p50={snap.get('p50Ms')}ms "
        f"p95={snap.get('p95Ms')}ms stalls={snap.get('stalls')} "
        f"errors={snap.get('errors')} rate={snap.get('errorRate')} — "
        f"Grund: {reason} | Quarantäne {pol.get('quarantine_hours')}h bis "
        f"{entry.get('until')}"
        + (" | Dry-Run: Änderungen nur als pending" if dry_run else ""))


# ── Der Lauf: parsen → blockieren → planen → (evtl.) anwenden ───────────────

def run_once(apply_fn=None) -> dict:
    """Ein voller Wächter-Refresh. Niemals werfen; Ergebnis fürs Log."""
    out = {"applied": False}
    apply_fn = apply_fn or _apply
    st = {"enforceFrom": None, "pending": [], "actions": [],
          "lastApplyAt": None, "log": _state_default_log()}
    try:
        pol = load_policy()
        now_epoch = time.time()
        now = _now_ts(now_epoch)
        st = _load_state()
        enforce_epoch = _ensure_enforce_from(st, now_epoch)
        out["ingested"] = _ingest_logs(st)
        stats = model_stats(int(pol["window_hours"]))

        # Sperrliste pflegen (abgelaufene raus) und neue Degraded-Modelle sperren
        doc = _prune_blocks()
        blocked_by = {}
        for b in doc["blocked"]:
            ref = _entry_ref(b)
            if ref and ref not in blocked_by:
                blocked_by[ref] = b
        new_blocks = []
        for ref, m in sorted(stats.items()):
            if ref in blocked_by:
                continue
            reasons = _degraded_reasons(m, pol)
            if not reasons:
                continue
            provider, model = _split_ref(ref)
            entry = {
                "model": model, "provider": provider,
                "reason": "; ".join(reasons),
                "blocked_at": now,
                "until": _now_ts(now_epoch + int(pol["quarantine_hours"]) * 3600),
                "snapshot": {"calls": m.get("calls"), "p50Ms": m.get("p50Ms"),
                             "p95Ms": m.get("p95Ms"), "stalls": m.get("stalls"),
                             "errors": m.get("errors"),
                             "errorRate": m.get("errorRate")},
            }
            blocked_by[ref] = entry
            new_blocks.append(entry)
        if new_blocks:
            doc["blocked"] = list(doc["blocked"]) + new_blocks
            doc["generatedAt"] = now
            _save_blocks(doc)
            out["newBlocks"] = [_entry_ref(e) for e in new_blocks]
        dry_run = enforce_epoch is None or now_epoch < enforce_epoch

        try:
            pub = _current_public()
        except Exception as e:  # noqa: BLE001 — nur planen, nicht anwenden
            _log(f"hermes config nicht lesbar: {e}")
            out["error"] = f"hermes config: {e}"
            pub = None
        pending, alarms, plan = (plan_changes(pub, set(blocked_by.keys()), pol)
                                 if pub is not None else ([], [], {}))
        for alarm in alarms:
            _notify(alarm)
        st["pending"] = pending

        cooldown = _cooldown_active(now_epoch, pol, st.get("lastApplyAt"))
        if pub is not None and pending and not dry_run and not cooldown:
            payload = _build_payload(pub, plan)
            try:
                res = apply_fn(payload)
                st["lastApplyAt"] = _now_ts(now_epoch)
                out["applied"] = True
                out["applyResult"] = {"unchanged": bool((res or {}).get("unchanged"))}
                for p in pending:
                    st["actions"].append({
                        "at": now, "kind": p["kind"], "from": p.get("from"),
                        "to": p.get("to"), "reason": p.get("reason"),
                        "result": "ok"})
                _log("Modellzuweisung angepasst: " + "; ".join(
                    f"{p['kind']} {p.get('from')} → {p.get('to')}"
                    for p in pending))
            except Exception as e:  # noqa: BLE001 — Fehler loggen, nichts weiter
                _log(f"apply fehlgeschlagen: {type(e).__name__}: {e}")
                for p in pending:
                    st["actions"].append({
                        "at": now, "kind": p["kind"], "from": p.get("from"),
                        "to": p.get("to"), "reason": p.get("reason"),
                        "result": "failed", "error": str(e)[:200]})
                out["error"] = f"apply: {e}"
        elif pub is not None and pending and dry_run:
            _log(f"Dry-Run (bis {st.get('enforceFrom')}): {len(pending)} "
                 f"geplante Änderungen nur als pending")
        elif pending and cooldown:
            _log(f"Cooldown aktiv — {len(pending)} Änderungen warten")
        # Neue Sperren einmalig melden (Dry-Run-Kennzeichnung inklusive)
        for entry in new_blocks:
            _notify_block(_entry_ref(entry), entry, pol, dry_run)
        out["mode"] = "dry-run" if dry_run else "auto"
        out["pending"] = pending
        out["blocked"] = sorted(blocked_by.keys())
    except Exception as e:  # noqa: BLE001 — der Lauf darf nie werfen
        _log(f"run_once fehlgeschlagen: {type(e).__name__}: {e}")
        out["error"] = f"{type(e).__name__}: {e}"
    try:
        _save_state(st)
    except Exception as e:  # noqa: BLE001
        _log(f"state-write fehlgeschlagen: {type(e).__name__}: {e}")
    return out


# ── Panel-Zustand (state() wirft nie) ───────────────────────────────────────

def state() -> dict:
    """JSON für das Panel — bei fehlendem Log/DB leere Struktur + error."""
    out = {
        "enabled": ENABLED,
        "mode": "dry-run",
        "enforceFrom": None,
        "windowHours": 24,
        "thresholds": {},
        "models": [],
        "blocked": [],
        "pending": [],
        "actions": [],
        "lastApplyAt": None,
        "turns": {"maxToolTurns": None, "turnsOver12": 0},
        "log": {"offset": 0, "lastLineAt": None},
        "error": None,
    }
    try:
        pol = load_policy()
        st = _load_state()
        now_epoch = time.time()
        enforce = ts_epoch(st.get("enforceFrom"))
        if enforce is not None:
            out["mode"] = "auto" if now_epoch >= enforce else "dry-run"
            out["enforceFrom"] = st.get("enforceFrom")
        out["windowHours"] = int(pol["window_hours"])
        out["thresholds"] = {
            "minCallsLatency": pol["min_calls_latency"],
            "minCallsStall": pol["min_calls_stall"],
            "minCallsError": pol["min_calls_error"],
            "p95Ms": pol["p95_ms"], "p50Ms": pol["p50_ms"],
            "stallLimit": pol["stall_limit"], "errorRate": pol["error_rate"],
            "quarantineHours": pol["quarantine_hours"],
            "cooldownHours": pol["cooldown_hours"],
            "dryRunHours": pol["dry_run_hours"],
            "preferred": pol["preferred"],
        }
        cutoff = _now_ts(now_epoch - int(pol["window_hours"]) * 3600)
        blocked_by = {}
        for b in _prune_blocks()["blocked"]:
            ref = _entry_ref(b)
            if ref:
                blocked_by[ref] = b
        for ref, m in sorted(model_stats(int(pol["window_hours"])).items()):
            reasons = _degraded_reasons(m, pol)
            out["models"].append({
                "ref": ref, "calls": m.get("calls"), "p50Ms": m.get("p50Ms"),
                "p95Ms": m.get("p95Ms"), "stalls": m.get("stalls"),
                "errors": m.get("errors"),
                "status": "blocked" if ref in blocked_by
                else ("degraded" if reasons else "ok"),
                "blockedUntil": (blocked_by.get(ref) or {}).get("until"),
            })
        out["blocked"] = list(blocked_by.values())
        out["pending"] = list(st.get("pending") or [])
        out["actions"] = list(st.get("actions") or [])[-20:]
        out["lastApplyAt"] = st.get("lastApplyAt")
        log = st.get("log") or {}
        out["log"] = {"offset": int(log.get("offset") or 0),
                      "lastLineAt": log.get("lastLineAt")}
        # Turns: maximale Tool-Turns im Fenster + wie viele über 12 liegen
        if os.path.isfile(DB_PATH):
            try:
                con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True,
                                      timeout=5)
                try:
                    max_row = con.execute(
                        "SELECT MAX(tool_turns) FROM turns WHERE ts >= ?",
                        (cutoff,)).fetchone()
                    over_row = con.execute(
                        "SELECT COUNT(*) FROM turns WHERE ts >= ? "
                        "AND tool_turns > 12", (cutoff,)).fetchone()
                    out["turns"] = {
                        "maxToolTurns": max_row[0],
                        "turnsOver12": int(over_row[0] or 0),
                    }
                finally:
                    con.close()
            except Exception:  # noqa: BLE001 — Turns sind Statistik
                pass
    except Exception as e:  # noqa: BLE001 — state() wirft nie
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ── Hintergrund-Thread ──────────────────────────────────────────────────────

_LOOP_THREAD = None
_STOP = None


def loop(stop_event: threading.Event):
    """Wächter-Loop: erster Lauf nach 10 s, dann alle INTERVAL Sekunden."""
    stop_event.wait(10.0)
    while not stop_event.is_set():
        t0 = time.time()
        try:
            run_once()
        except Exception as e:  # noqa: BLE001 — Loop darf nie abbrechen
            _log(f"loop-Durchlauf fehlgeschlagen: {type(e).__name__}: {e}")
        stop_event.wait(max(10.0, INTERVAL - (time.time() - t0)))


def start():
    """Daemon-Thread nur bei HESTIA_MODEL_WATCH=1 (Default 1) — nie beim
    Import."""

    global _LOOP_THREAD, _STOP
    if not ENABLED:
        _log("Model-Watch inaktiv (HESTIA_MODEL_WATCH=0) — nur Panel-Status")
        return
    if _LOOP_THREAD and _LOOP_THREAD.is_alive():
        return
    # Dry-Run-Fenster ab dem ERSTEN Start im State verankern (kein Reset bei
    # jedem Deployment; run_once füllt nach, falls die Datei entfernt wurde).
    try:
        st = _load_state()
        _ensure_enforce_from(st)
        _save_state(st)
    except Exception as e:  # noqa: BLE001
        _log(f"enforceFrom-Vermerk fehlgeschlagen: {type(e).__name__}: {e}")
    _STOP = threading.Event()
    _LOOP_THREAD = threading.Thread(target=loop, args=(_STOP,), daemon=True)
    _LOOP_THREAD.start()
    _log(f"Model-Watch gestartet (Intervall {INTERVAL}s, Dry-Run bis "
         f"{_load_state().get('enforceFrom')})")
