#!/usr/bin/env python3
"""hermes_speed.py — Hermes Voice Phase 2: Modell-Speed-Scan + O19-Telemetrie.

Zwei Aufgaben in einer Datei:

  1. SPEED-SCAN: Neue/veränderte Modelle aus dem Live-Katalog werden mit einer
     winzigen Test-Session ("Antworte nur mit OK") auf Time-to-First-Token und
     Gesamtzeit vermessen — exakt das Probe-Muster aus failover.probe (Session
     anlegen → Modell wechseln → Prompt feuern → _event_watch über den
     SSE-Stream, danach Session löschen). Gute Gratis-Modelle werden als
     Empfehlung ins Panel gestellt; angewendet wird NUR per Klick
     (apply_recommendation → hermes.apply — die SOT .opencode/hestia-hermes.jsonc
     bleibt Quelle der Wahrheit). Es gibt hier bewusst KEINEN Auto-Apply.

  2. O19-TELEMETRIE: Der Hermes-Container schreibt /opt/data/state.db (SQLite:
     sessions/messages/session_model_usage). Das Panel bekommt die Live-DB nie
     zu sehen — ein Seitenwagen (hestia/telemetry_snapshot.py, siehe
     docker-compose.yml) kopiert sie per sqlite3-Backup-API in einen
     Read-only-Mount. Diese Datei liest NUR den Snapshot (mode=ro) und
     aggregiert Verbrauch je Modell (Anfragen, Tokens, Kosten). Es werden nie
     Nachrichteninhalte gelesen und nie Secrets ausgegeben.

Zustand (Report §2.1): {checkedAt, candidates, archive} in HESTIA_SPEED_PATH —
Kandidatenfelder ref/free/provider/model/runs/ttfbMs{p50,p95}/totalMs{p50,p95}/
toolOk/errors/score/status/lastProbe. Daneben eine „known“-Map in
HESTIA_SPEED_KNOWN_PATH (Katalog-Hash + firstSeen/lastSeen je Ref): Discovery
vergleicht den Hash, um wirklich Neue bzw. Geänderte zu erkennen; geschrieben
wird known erst NACH der Probe (nie Blindvermerke für ungeprobte Modelle).

Eine Probe ist ein echter API-Aufruf — deshalb: Warm-up (infließend, nicht in
der Statistik) + N Samples je Kandidat, Tageslimit für neue Kandidaten
(HESTIA_SPEED_MAX_NEW_PER_DAY) und Archiv für Ausfälle/Langsame (recheckAfter =
archivedAt + HESTIA_SPEED_ARCHIVE_DAYS). Beide JSON-Dateien werden atomar
geschrieben (tmp + os.replace).

Adaptiv (je Kandidat ein „schedule“): Nach jeder erfolgreichen Probe wird die
Gesamtzeit (p50 UND p95) mit der vorherigen Messung verglichen — relative
Änderungen ≤ HESTIA_SPEED_STABLE_TOLERANCE gelten als stabil (die erste
Messung zählt als stabile Basislinie). Nach HESTIA_SPEED_STABLE_RUNS
stabilen Proben in Folge verdoppelt sich das Intervall (24 → 48 → 96 h …
bis HESTIA_SPEED_MAX_INTERVAL_HOURS), Drift/Fehler setzen auf das
Basisintervall zurück. run_monitoring probt nur FÄLLIGE Kandidaten
(schedule.nextProbeAt); daneben schreibt ein Best-Effort-SQLite
(HESTIA_SPEED_HISTORY_DB) Katalog-, Probe- und Schedule-Daten in eine
Historie — nur refs/Metriken/Zeiten/Kosten, nie Nachrichteninhalte/Secrets.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time

import catalog
import hermes
import suggest

SPEED_PATH = os.environ.get("HESTIA_SPEED_PATH", "/app/data/hermes-speed.json")
KNOWN_PATH = os.environ.get("HESTIA_SPEED_KNOWN_PATH", "/app/data/hermes-speed-known.json")
STATE_DB = os.environ.get("HERMES_STATE_DB", "/hermes-data/state.db")
AUTO = os.environ.get("HESTIA_SPEED_AUTO", "1") == "1"
INTERVAL_HOURS = int(os.environ.get("HESTIA_SPEED_INTERVAL_HOURS", "24"))
SAMPLES = int(os.environ.get("HESTIA_SPEED_SAMPLES", "3"))
PROBE_TIMEOUT = int(os.environ.get("HESTIA_SPEED_PROBE_TIMEOUT", "35"))
MAX_NEW_PER_DAY = int(os.environ.get("HESTIA_SPEED_MAX_NEW_PER_DAY", "5"))
ARCHIVE_DAYS = int(os.environ.get("HESTIA_SPEED_ARCHIVE_DAYS", "90"))
FREE_ONLY = os.environ.get("HESTIA_SPEED_FREE_ONLY", "1") == "1"
# Adaptiver Schedule: Stabil = keine Fehler und beide rel. Änderungen
# (total p50 UND p95) ≤ Toleranz; nach STABLE_RUNS stabilen Proben in Folge
# verdoppelt sich das Intervall (24 → 48 → 96 h … bis MAX_INTERVAL_HOURS),
# Drift/Fehler setzen auf das Basisintervall (INTERVAL_HOURS) zurück.
STABILITY_SAMPLES = int(os.environ.get("HESTIA_SPEED_STABILITY_SAMPLES", "5"))
STABLE_RUNS = int(os.environ.get("HESTIA_SPEED_STABLE_RUNS", "3"))
STABLE_TOLERANCE = float(os.environ.get("HESTIA_SPEED_STABLE_TOLERANCE", "0.20"))
MAX_INTERVAL_HOURS = int(os.environ.get("HESTIA_SPEED_MAX_INTERVAL_HOURS", "168"))
DISCOVERY_HOURS = int(os.environ.get("HESTIA_SPEED_DISCOVERY_HOURS", "24"))
# SQLite-Historie (best-effort): nur refs/Metriken/Zeiten/Kosten — nie Secrets.
HISTORY_DB = os.environ.get("HESTIA_SPEED_HISTORY_DB", "/app/data/hermes-speed-history.db")
# Kostenbremse: bezahlte Modelle werden im Automatik-/Discovery-Lauf höchstens
# so oft pro UTC-Tag geprobt. Manuelle Probes bleiben bewusst möglich.
MAX_PAID_PER_DAY = int(os.environ.get("HESTIA_SPEED_MAX_PAID_PER_DAY", "5"))
# Probe-Sessions nicht sofort löschen: Das ntfy-Plugin lädt parentID/title per
# session.get nach; nach einem sofortigen DELETE wäre die Session weg und die
# Probe würde wie eine normale Session gemeldet. 30 s überbrücken Debounce und
# Meta-Abruf; der Timer ist daemonisiert.
SESSION_DELETE_DELAY = max(0.0, float(os.environ.get(
    "HESTIA_SPEED_SESSION_DELETE_DELAY", "30")))

SAMPLES_RANGE = (1, 10)

# Erlaubte Provider (Komma-getrennt) — identisch zu hermes.py/failover.py.
ALLOWED_PROVIDERS = [p.strip() for p in
                     os.environ.get("HESTIA_PROVIDERS", "opencode-go,opencode").split(",") if p.strip()]
# Trainings-Klausel-Suffix — bewusst gespiegelt zu catalog.TRAINING_SUFFIX (die
# Tests fälschen das catalog-Modul und kämen sonst nicht an die Konstante heran).
TRAINING_SUFFIX = "-contributor"
# p95-Grenze (Gesamtzeit in ms): drüber wird ein Kandidat archiviert ("zu langsam").
SLOW_P95_MS = 4000.0
PROMPT_TEXT = "Antworte nur mit OK"      # identisch zu failover.probe


def _log(msg):
    print(f"[hestia-hermes-speed] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _now_iso(ts: float | None = None) -> str:
    """UTC-Zeitstempel (ISO-8601, sekundengenau) — wie failover._now_iso."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if ts is None else ts))


def _fnum(v):
    """float-Koercion für die Historie (None bleibt None, Müll wird None)."""
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── Zustand: atomares JSON (State + known) ──────────────────────────────────

FILE_LOCK = threading.Lock()          # schützt die beiden JSON-Dateien
RUN_LOCK = threading.Lock()           # schützt den Laufzeit-Status (running/…)
RUN = {"running": False, "mode": None, "startedAt": None, "finishedAt": None, "error": None}


def _atomic_write(path: str, data: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)             # atomar: Reader sehen nie halbe Dateien


def _load_json(path: str, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(fallback)) else fallback
    except Exception:
        return fallback


def _load_state() -> dict:
    st = _load_json(SPEED_PATH, {})
    st.setdefault("checkedAt", None)
    st.setdefault("candidates", [])
    st.setdefault("archive", [])
    st.setdefault("newToday", {"date": _now_iso()[:10], "count": 0})
    st.setdefault("paidToday", {"date": _now_iso()[:10], "count": 0})
    st.setdefault("lastApplied", None)
    if not isinstance(st["candidates"], list):
        st["candidates"] = []
    if not isinstance(st["archive"], list):
        st["archive"] = []
    # Migration: alte States ohne schedule bekommen einen — erst in-memory,
    # der nächste Save schreibt die Migration automatisch in die JSON-Datei.
    for entry in st["candidates"]:
        _ensure_schedule(entry, archived=False, checked_at=st.get("checkedAt"))
    for entry in st["archive"]:
        _ensure_schedule(entry, archived=True, checked_at=st.get("checkedAt"))
    return st


def _load_known() -> dict:
    known = _load_json(KNOWN_PATH, {})
    return known if isinstance(known, dict) else {}


def _save_state(st: dict):
    _atomic_write(SPEED_PATH, st)


def _save_known(known: dict):
    _atomic_write(KNOWN_PATH, known)


def _save_all(st: dict, known: dict):
    _save_state(st)
    _save_known(known)


# ── Adaptiver Schedule + SQLite-Historie (best-effort) ──────────────────────

def _new_schedule(interval_hours: int | None = None) -> dict:
    """Frischer Schedule: Basisintervall, noch keine Stabilitäts-Serie."""
    return {
        "intervalHours": int(interval_hours) if interval_hours is not None
        else INTERVAL_HOURS,
        "stableStreak": 0,
        "nextProbeAt": None,
        "lastProbeAt": None,
        "lastP50Ms": None,
        "lastP95Ms": None,
        "lastErrors": None,
    }


def _ensure_schedule(entry: dict, archived: bool = False,
                     checked_at: str | None = None) -> dict:
    """Schedule je Eintrag ergänzen — Migration alter States ohne schedule.

    Kandidaten: nextProbeAt = lastProbe + Basisintervall (ohne lastProbe:
    checkedAt + Basisintervall). Archiv: recheckAfter, sonst lastProbe +
    Archiv-Frist. So re-probt ein Deploy mit altem State NICHT sofort; fehlt
    jede Basis, bleibt der Termin offen (None = sofort fällig, wie die erste
    Installation). Reine Ergänzung — bestehende Werte bleiben stehen.
    """
    raw = entry.get("schedule") if isinstance(entry.get("schedule"), dict) else {}
    try:
        interval = max(1, int(raw.get("intervalHours")))
    except (TypeError, ValueError):
        interval = ARCHIVE_DAYS * 24 if archived else INTERVAL_HOURS
    if archived:
        # Archivierte Modelle werden nicht im Monitoring getaktet; der
        # nächste Termin ist die recheckAfter-Frist (Archiv-Intervall).
        interval = ARCHIVE_DAYS * 24
    try:
        streak = int(raw.get("stableStreak") or 0)
    except (TypeError, ValueError):
        streak = 0
    sch = {
        "intervalHours": interval,
        "stableStreak": streak,
        "nextProbeAt": raw.get("nextProbeAt"),
        "lastProbeAt": raw.get("lastProbeAt"),
        "lastP50Ms": raw.get("lastP50Ms"),
        "lastP95Ms": raw.get("lastP95Ms"),
        "lastErrors": raw.get("lastErrors"),
    }
    if sch["nextProbeAt"] is None:
        if archived:
            base_iso = entry.get("recheckAfter")
            if not base_iso:
                base_ts = _to_epoch(entry.get("lastProbe") or checked_at)
                base_iso = _now_iso((base_ts or time.time()) + ARCHIVE_DAYS * 86400)
            sch["nextProbeAt"] = base_iso
        else:
            base_ts = _to_epoch(entry.get("lastProbe") or checked_at)
            sch["nextProbeAt"] = (_now_iso(base_ts + INTERVAL_HOURS * 3600)
                                  if base_ts is not None else None)
    entry["schedule"] = sch
    return sch


def _schedule_due(cand: dict, now_iso: str) -> bool:
    """Fällig? Offener Termin (None) oder nextProbeAt ≤ jetzt → fällig."""
    nxt = (cand.get("schedule") or {}).get("nextProbeAt")
    return not nxt or str(nxt) <= now_iso


def _today_utc() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _paid_today(st: dict) -> int:
    """Anzahl bereits gestarteter bezahlter Auto-Probes am heutigen UTC-Tag."""
    pt = st.get("paidToday") if isinstance(st.get("paidToday"), dict) else {}
    if pt.get("date") != _today_utc():
        return 0
    try:
        return max(0, int(pt.get("count") or 0))
    except (TypeError, ValueError):
        return 0


def _bump_paid(st: dict):
    """Bezahlten Auto-Probe zählen (nur im laufenden State; Save macht der Lauf)."""
    today = _today_utc()
    pt = st.get("paidToday") if isinstance(st.get("paidToday"), dict) else {}
    if pt.get("date") != today:
        pt = {"date": today, "count": 0}
    try:
        pt["count"] = max(0, int(pt.get("count") or 0)) + 1
    except (TypeError, ValueError):
        pt["count"] = 1
    st["paidToday"] = pt


_HIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    ref TEXT PRIMARY KEY,
    provider TEXT,
    name TEXT,
    free INTEGER,
    context INTEGER,
    input_cost REAL,
    output_cost REAL,
    cache_cost REAL,
    first_seen TEXT,
    last_seen TEXT,
    catalog_hash TEXT
);
CREATE TABLE IF NOT EXISTS probe_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    ref TEXT,
    mode TEXT,
    samples INTEGER,
    ttfb_p50 REAL,
    ttfb_p95 REAL,
    total_p50 REAL,
    total_p95 REAL,
    errors INTEGER,
    ok INTEGER,
    score REAL
);
CREATE TABLE IF NOT EXISTS schedule (
    ref TEXT PRIMARY KEY,
    interval_hours REAL,
    stable_streak INTEGER,
    next_probe_at TEXT,
    last_probe_at TEXT,
    last_p50_ms REAL,
    last_p95_ms REAL,
    last_errors INTEGER
);
"""


def _hist_connect():
    """Kurzlebige Verbindung (je Aufruf, im aufrufenden Thread); WAL + busy_timeout."""
    os.makedirs(os.path.dirname(HISTORY_DB) or ".", exist_ok=True)
    con = sqlite3.connect(HISTORY_DB, timeout=5)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.executescript(_HIST_SCHEMA)
    except Exception:
        con.close()
        raise
    return con


def _history(label: str, fn):
    """Best-Effort-Schreibzugriff: Fehler werden geloggt und geschluckt —
    die Historie darf nie eine Probe oder einen Lauf brechen."""
    if not HISTORY_DB:
        return
    try:
        con = _hist_connect()
        try:
            fn(con)
            con.commit()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log(f"history-db {label} ignoriert: {type(e).__name__}: {e}")


def _hist_upsert_models(models: list):
    """ALLE Katalogmodelle in die Historie (auch nicht eingereihte)."""
    def _run(con):
        now = _now_iso()
        for m in models:
            ref = m.get("ref") or ""
            if not ref:
                continue
            cost = m.get("cost") or {}
            try:
                free = 1 if catalog.is_free(ref) else 0
            except Exception:  # noqa: BLE001
                free = None
            con.execute(
                "INSERT INTO models (ref, provider, name, free, context, input_cost, "
                "output_cost, cache_cost, first_seen, last_seen, catalog_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ref) DO UPDATE SET provider=excluded.provider, "
                "name=excluded.name, free=excluded.free, context=excluded.context, "
                "input_cost=excluded.input_cost, output_cost=excluded.output_cost, "
                "cache_cost=excluded.cache_cost, last_seen=excluded.last_seen, "
                "catalog_hash=excluded.catalog_hash",
                (ref, m.get("providerID"), m.get("name") or m.get("modelID"), free,
                 _fnum((m.get("limit") or {}).get("context")),
                 _fnum(cost.get("input")), _fnum(cost.get("output")),
                 _fnum(cost.get("cacheRead")), now, now, _metadata_hash(m)))
    _history("models", _run)


def _hist_backfill_state(st: dict):
    """Bestehenden JSON-Zustand einmalig in die Historie übernehmen.

    Die JSON-Datei enthält nur die letzte Messung je Kandidat, keine
    Einzelproben. Für den Start der Historie wird daher pro Kandidat eine
    Baseline-Zeile (mode=state-migration) geschrieben; echte Proben kommen
    danach aus _hist_record_probe. Idempotent: die Baseline wird nicht doppelt
    angelegt. Schedule-Zeilen werden bei jedem Aufruf aktualisiert.
    """
    def _run(con):
        for entry in (list(st.get("candidates") or []) +
                      list(st.get("archive") or [])):
            ref = entry.get("ref") or ""
            if not ref:
                continue
            sch = entry.get("schedule") or {}
            if sch:
                con.execute(
                    "INSERT INTO schedule (ref, interval_hours, stable_streak, "
                    "next_probe_at, last_probe_at, last_p50_ms, last_p95_ms, last_errors) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(ref) DO UPDATE SET interval_hours=excluded.interval_hours, "
                    "stable_streak=excluded.stable_streak, "
                    "next_probe_at=excluded.next_probe_at, "
                    "last_probe_at=excluded.last_probe_at, "
                    "last_p50_ms=excluded.last_p50_ms, "
                    "last_p95_ms=excluded.last_p95_ms, "
                    "last_errors=excluded.last_errors",
                    (ref, _fnum(sch.get("intervalHours")),
                     int(sch.get("stableStreak") or 0), sch.get("nextProbeAt"),
                     sch.get("lastProbeAt"), _fnum(sch.get("lastP50Ms")),
                     _fnum(sch.get("lastP95Ms")), sch.get("lastErrors")))
            if not entry.get("lastProbe"):
                continue
            exists = con.execute(
                "SELECT 1 FROM probe_runs WHERE ref=? AND mode='state-migration' LIMIT 1",
                (ref,)).fetchone()
            if exists:
                continue
            ttfb, total = entry.get("ttfbMs") or {}, entry.get("totalMs") or {}
            con.execute(
                "INSERT INTO probe_runs (ts, ref, mode, samples, ttfb_p50, ttfb_p95, "
                "total_p50, total_p95, errors, ok, score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry.get("lastProbe"), ref, "state-migration", None,
                 _fnum(ttfb.get("p50")), _fnum(ttfb.get("p95")),
                 _fnum(total.get("p50")), _fnum(total.get("p95")),
                 int(entry.get("errors") or 0),
                 1 if entry.get("status") == "monitoring" else 0,
                 _fnum(entry.get("score"))))
    _history("backfill", _run)


def _hist_record_probe(cand: dict, res: dict, mode: str):
    """Probe-Ergebnis + Schedule-Stand in die Historie (best-effort)."""
    def _run(con):
        ttfb, total = res.get("ttfbMs") or {}, res.get("totalMs") or {}
        sch = cand.get("schedule") or {}
        con.execute(
            "INSERT INTO probe_runs (ts, ref, mode, samples, ttfb_p50, ttfb_p95, "
            "total_p50, total_p95, errors, ok, score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now_iso(), cand.get("ref"), mode or "manual",
             int(res.get("samples") or 0),
             _fnum(ttfb.get("p50")), _fnum(ttfb.get("p95")),
             _fnum(total.get("p50")), _fnum(total.get("p95")),
             int(res.get("errors") or 0), 1 if res.get("ok") else 0,
             _fnum(cand.get("score"))))
        con.execute(
            "INSERT INTO schedule (ref, interval_hours, stable_streak, next_probe_at, "
            "last_probe_at, last_p50_ms, last_p95_ms, last_errors) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ref) DO UPDATE SET interval_hours=excluded.interval_hours, "
            "stable_streak=excluded.stable_streak, next_probe_at=excluded.next_probe_at, "
            "last_probe_at=excluded.last_probe_at, last_p50_ms=excluded.last_p50_ms, "
            "last_p95_ms=excluded.last_p95_ms, last_errors=excluded.last_errors",
            (cand.get("ref"), _fnum(sch.get("intervalHours")),
             int(sch.get("stableStreak") or 0), sch.get("nextProbeAt"),
             sch.get("lastProbeAt"), _fnum(sch.get("lastP50Ms")),
             _fnum(sch.get("lastP95Ms")), sch.get("lastErrors")))
    _history("probe", _run)


def _metadata_hash(m: dict) -> str:
    """Stabiler Hash über die katalogrelevanten Felder (Änderungserkennung)."""
    payload = {k: m.get(k) for k in ("modelID", "providerID", "released", "status",
                                     "enabled", "variants", "cost", "costTiers",
                                     "costEffective", "limit")}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _new_candidate(m: dict) -> dict:
    ref = m["ref"]
    return {
        "ref": ref,
        "free": catalog.is_free(ref),
        "provider": m.get("providerID"),
        "model": m.get("modelID"),
        "runs": 0,
        "ttfbMs": {"p50": None, "p95": None},
        "totalMs": {"p50": None, "p95": None},
        "toolOk": None,               # nie behaupten, ohne echte Tool-Probe
        "errors": 0,
        "score": None,
        "status": "queued",
        "lastProbe": None,
        "schedule": _new_schedule(),
    }


def _current_refs() -> set:
    """refs des aktuellen Hermes-Primaries + Fallbacks (Ausnahme vom Free-only)."""
    try:
        cfg = hermes._public(hermes.config(force=False))
    except Exception as e:  # noqa: BLE001
        _log(f"hermes config nicht lesbar ({e}) — Free-only ohne Ausnahmen")
        return set()
    refs = set()
    p = cfg.get("primary") or {}
    if p.get("provider") and p.get("model"):
        refs.add(f"{p['provider']}/{p['model']}")
    for f in cfg.get("fallback") or []:
        if isinstance(f, dict) and f.get("provider") and f.get("model"):
            refs.add(f"{f['provider']}/{f['model']}")
    return refs


# ── Discovery ───────────────────────────────────────────────────────────────

def discover(force: bool = True) -> list:
    """Katalog scannen und neue/veränderte Kandidaten in die Queue legen.

    force=True umgeht das Tageslimit (und den Katalog-Cache) — für manuelle
    Läufe und Tests. Reguläre Läufe (run_discovery) nutzen force=False, damit
    HESTIA_SPEED_MAX_NEW_PER_DAY das Probe-Volumen begrenzt.

    Filter: erlaubter Provider, status=active, enabled, keine Trainings-Klausel
    (-contributor), free-only (Ausnahme: aktueller Primary/Fallback), noch
    nicht archiviert (bzw. recheckAfter abgelaufen), Katalog-Hash gegenüber
    known unverändert → kein neuer Kandidat. Die Queue wird NICHT geprobt und
    known NICHT angefasst — das passiert erst in run_discovery nach der Probe.
    """
    models = catalog.fetch_models(force=force)
    _hist_upsert_models(models)        # Historie: ALLE Katalogmodelle (best-effort)
    with FILE_LOCK:
        st = _load_state()
        known = _load_known()
    _hist_backfill_state(st)           # Bestand einmalig, ohne Netz/Probe
    current = _current_refs()
    queued = {c["ref"] for c in st["candidates"]}
    archived = {a["ref"]: a for a in st["archive"]}
    now_iso = _now_iso()
    today = now_iso[:10]
    book = st["newToday"]
    if book.get("date") != today:
        book = {"date": today, "count": 0}
        st["newToday"] = book
    added = []
    for m in models:                   # Katalog ist newest-first sortiert
        ref = m.get("ref") or ""
        if not ref or ref in queued or ref in {c["ref"] for c in added}:
            continue
        if m.get("providerID") not in ALLOWED_PROVIDERS:
            continue
        if m.get("status") != "active" or not m.get("enabled"):
            continue
        if (m.get("modelID") or "").endswith(TRAINING_SUFFIX):
            continue
        # Archiviert? Erst nach recheckAgain wieder aufnehmen (und dann
        # aus dem Archiv herauslösen — die Probe entscheidet neu).
        arch = archived.get(ref)
        if arch:
            if (arch.get("recheckAfter") or "") > now_iso:
                continue
        free = catalog.is_free(ref)
        if FREE_ONLY and not free and ref not in current:
            continue
        h = _metadata_hash(m)
        k = known.get(ref)
        if not arch and k and k.get("hash") == h:
            continue                   # bekannt und unverändert → nichts zu tun
        if not force and book["count"] >= MAX_NEW_PER_DAY:
            break                      # Tageslimit — Rest kommt auf den nächsten Tag
        if arch:
            st["archive"] = [a for a in st["archive"] if a["ref"] != ref]
            archived.pop(ref, None)
        cand = _new_candidate(m)
        st["candidates"].append(cand)
        book["count"] = int(book.get("count") or 0) + 1
        added.append(cand)
    if added:
        with FILE_LOCK:
            _save_state(st)
        _log(f"discovery: {len(added)} neue Kandidaten "
             f"({', '.join(c['ref'] for c in added)})")
    return [c for c in st["candidates"] if c.get("status") == "queued"]


# ── Probe (exakt das failover.probe-Muster) ─────────────────────────────────

def _pctl(values: list, pct: float):
    """Perzentil (linear interpoliert) über die gültigen Werte; None ohne Werte."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 1)
    k = (len(vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return round(vals[f] + (vals[c] - vals[f]) * (k - f), 1)


def _score(ttfb: dict, total: dict):
    """Kompaktscore (ms-Gewicht, niedriger = besser); None ohne Messwerte."""
    if (ttfb or {}).get("p50") is None and (total or {}).get("p50") is None:
        return None
    t1 = (ttfb or {}).get("p50") or 0.0
    t2 = (ttfb or {}).get("p95") or t1
    g1 = (total or {}).get("p50") or 0.0
    g2 = (total or {}).get("p95") or g1
    # 80 % TTFB (spürbare Antwortzeit), 20 % Gesamtzeit — dokumentiert einfach.
    return round(0.5 * t1 + 0.3 * t2 + 0.15 * g1 + 0.05 * g2, 1)


def _single_run(ref: str, variant: str = "", timeout: int = PROBE_TIMEOUT) -> dict:
    """EINE Test-Session: anlegen → Modell wechseln → Prompt → Watch → löschen.

    Gesamtzeit = Prompt-POST bis Watch-Rückkehr; TTFB = Prompt-POST bis zum
    ersten .delta-Event (via on_first_delta-Callback von suggest._event_watch —
    fehlt der Callback/das Event, bleibt ttfbMs None).
    """
    # Der Zusatz "Ausfall-Probe" ist der Stumm-Marker, den die bereits
    # laufenden OpenCode-ntfy-Instanzen aus ihrer Startkonfiguration kennen.
    # Zusätzlich läuft die eigentliche Probe in einer Subagent-Session
    # (parentID): Das ntfy-Plugin ignoriert Subagent-Sessions grundsätzlich —
    # unabhängig davon, ob es den Session-Titel rechtzeitig sieht. Die leere
    # Eltern-Session wird nie gepromptet und löst daher keine Meldung aus.
    parent_status, parent_res = suggest._req(
        "POST", f"{suggest.OC_SERVER_URL}/api/session",
        {"location": {"directory": suggest.OC_SUGGEST_DIR},
         "title": suggest.SILENCE_TITLE})
    if parent_status not in (200, 201):
        return {"ok": False, "detail": f"Session-Anlegen fehlgeschlagen ({parent_status})",
                "ttfbMs": None, "totalMs": None}
    parent_id = (parent_res.get("data") or parent_res).get("id") if isinstance(parent_res, dict) else None
    if not parent_id:
        return {"ok": False, "detail": "Keine Eltern-Session-ID",
                "ttfbMs": None, "totalMs": None}
    sstatus, sres = suggest._req(
        "POST", f"{suggest.OC_SERVER_URL}/api/session",
        {"location": {"directory": suggest.OC_SUGGEST_DIR},
         "parentID": parent_id,
         "title": suggest.SILENCE_TITLE})
    if sstatus not in (200, 201):
        suggest._delete_session(parent_id)
        return {"ok": False, "detail": f"Probe-Session-Anlegen fehlgeschlagen ({sstatus})",
                "ttfbMs": None, "totalMs": None}
    sid = (sres.get("data") or sres).get("id") if isinstance(sres, dict) else None
    if not sid:
        suggest._delete_session(parent_id)
        return {"ok": False, "detail": "Keine Probe-Session-ID",
                "ttfbMs": None, "totalMs": None}
    try:
        provider, _, mid = ref.partition("/")
        body = {"providerID": provider, "id": mid}
        if variant:
            body["variant"] = variant
        mst, _ = suggest._req("POST", f"{suggest.OC_SERVER_URL}/api/session/{sid}/model",
                              {"model": body})
        if mst not in (200, 201, 204):
            return {"ok": False, "detail": f"Modell nicht verfügbar (switch {mst})",
                    "ttfbMs": None, "totalMs": None}
        t0 = time.perf_counter()
        suggest._req("POST", f"{suggest.OC_SERVER_URL}/api/session/{sid}/prompt",
                     {"text": PROMPT_TEXT})
        ttfb_box: dict = {}

        def _first_delta():
            ttfb_box.setdefault("t", time.perf_counter())

        result = suggest._event_watch(sid, deadline=time.time() + timeout,
                                      stall_timeout=timeout, on_first_delta=_first_delta)
        total_ms = (time.perf_counter() - t0) * 1000.0
        ttfb_ms = (ttfb_box["t"] - t0) * 1000.0 if ttfb_box.get("t") else None
        return {"ok": result == "succeeded", "detail": result,
                "ttfbMs": round(ttfb_ms, 1) if ttfb_ms is not None else None,
                "totalMs": round(total_ms, 1)}
    finally:
        def _cleanup_probe_sessions():
            suggest._delete_session(sid)
            suggest._delete_session(parent_id)

        if SESSION_DELETE_DELAY > 0:
            timer = threading.Timer(SESSION_DELETE_DELAY, _cleanup_probe_sessions)
            timer.daemon = True
            timer.start()
        else:
            _cleanup_probe_sessions()


def probe(ref: str, samples: int | None = None, variant: str = "") -> dict:
    """Speed-Probe eines Modells: 1 Warm-up (Verwurf) + N Samples → Aggregate.

    toolOk bleibt bewusst None: ohne echte Tool-Probe wird kein Tool-Erfolg
    behauptet (Report §2.1).
    """
    if not ref:
        return {"ok": False, "ref": ref, "detail": "kein Modell konfiguriert",
                "runs": 0, "errors": 1, "ttfbMs": {"p50": None, "p95": None},
                "totalMs": {"p50": None, "p95": None}, "toolOk": None}
    ok, info = catalog.usable(ref)
    if not ok:
        return {"ok": False, "ref": ref, "detail": str(info),
                "runs": 0, "errors": 1, "ttfbMs": {"p50": None, "p95": None},
                "totalMs": {"p50": None, "p95": None}, "toolOk": None}
    try:
        n = max(SAMPLES_RANGE[0], min(SAMPLES_RANGE[1], int(samples or SAMPLES)))
    except (TypeError, ValueError):
        n = SAMPLES
    ttfb_vals, total_vals = [], []
    errors, details = 0, []

    # Warm-up: Verbindungen aufbauen, erstes Token des Modells abwarten — geht
    # NICHT in die Statistik ein (Kalibrierung, wie im Speed-Report vereinbart).
    try:
        warm = _single_run(ref, variant)
        if not warm["ok"]:
            details.append(f"warm-up: {warm['detail']}")
    except Exception as e:  # noqa: BLE001 — Netzfehler darf die Probe nicht abbrechen
        details.append(f"warm-up: {type(e).__name__}: {e}")

    for _ in range(n):
        try:
            r = _single_run(ref, variant)
        except Exception as e:  # noqa: BLE001
            errors += 1
            details.append(f"{type(e).__name__}: {e}")
            continue
        details.append(str(r["detail"]))
        if r["ok"]:
            ttfb_vals.append(r.get("ttfbMs"))
            total_vals.append(r.get("totalMs"))
        else:
            errors += 1
    ttfb = {"p50": _pctl(ttfb_vals, 50), "p95": _pctl(ttfb_vals, 95)}
    total = {"p50": _pctl(total_vals, 50), "p95": _pctl(total_vals, 95)}
    return {"ok": not errors and total["p50"] is not None, "ref": ref, "samples": n,
            "runs": n, "ttfbMs": ttfb, "totalMs": total, "toolOk": None,
            "errors": errors, "detail": " | ".join(details[-2:])}


# ── Läufe: Discovery/Monitoring/Manual — Status pflegen, archivieren ────────

def _archive_entry(cand: dict) -> dict:
    now_iso = _now_iso()
    return {**cand, "status": "archived", "archivedAt": now_iso,
            "recheckAfter": _now_iso(time.time() + ARCHIVE_DAYS * 86400)}


def _flush_archived(st: dict):
    """Invariante: Kandidaten mit status=archived wandern in die Archiv-Liste.

    Entfällt der Flush, würde discover() den Ref über die Kandidatenliste
    weiter blockieren, ohne dass ein recheckAfter ihn je wieder freigäbe.
    """
    still = []
    for cand in st["candidates"]:
        if cand.get("status") == "archived":
            st["archive"].append(_archive_entry(cand))
        else:
            still.append(cand)
    st["candidates"] = still


def _prune_archive(st: dict):
    """Archiv aufräumen: Einträge älter als das Doppelte der Archiv-Frist fliegen."""
    cutoff = _now_iso(time.time() - 2 * ARCHIVE_DAYS * 86400)
    st["archive"] = [a for a in st["archive"]
                     if (a.get("archivedAt") or "") >= cutoff]


def _stable_enough(prev_total: dict | None, new_total: dict, errors: int) -> bool:
    """Stabil = keine Fehler und rel. Änderung von total-p50 UND p95 ≤ Toleranz.

    Die erste Messung (keine vorherige) zählt als stabile Basislinie.
    """
    if errors:
        return False
    prev = prev_total or {}
    if prev.get("p50") is None and prev.get("p95") is None:
        return True                     # erste Messung = Basislinie
    for new, old in (((new_total or {}).get("p50"), prev.get("p50")),
                     ((new_total or {}).get("p95"), prev.get("p95"))):
        if new is None or old is None:
            return False
        old = float(old)
        if old <= 0:
            if float(new) > 0:
                return False
        elif abs(float(new) - old) / old > STABLE_TOLERANCE:
            return False
    return True


def _probe_and_update(cand: dict, known: dict, samples: int | None = None,
                      mode: str = "") -> dict:
    """Einen Kandidaten proben und State + known + Schedule aktualisieren (kein Apply)."""
    ref = cand["ref"]
    prev_total = json.loads(json.dumps(cand.get("totalMs") or {}))  # Basis VOR dem Überschreiben
    res = probe(ref, samples=samples)
    cand["runs"] = int(cand.get("runs") or 0) + int(res.get("runs") or 0)
    cand["errors"] = int(cand.get("errors") or 0) + int(res.get("errors") or 0)
    cand["lastProbe"] = _now_iso()
    cand["toolOk"] = None               # ohne Tool-Probe nie einen Erfolg behaupten
    if res["ok"]:
        cand["ttfbMs"] = res["ttfbMs"]
        cand["totalMs"] = res["totalMs"]
        cand["score"] = _score(res["ttfbMs"], res["totalMs"])
        cand["status"] = "monitoring"
    else:
        cand["status"] = "archived"     # Fehler → direkt ins Archiv
    if res["ok"] and (res["totalMs"].get("p95") or 0) >= SLOW_P95_MS:
        cand["status"] = "archived"     # zu langsam (p95-Gesamtzeit)
    # Adaptiver Schedule: Stabilität gegen die vorherige Messung — nach genug
    # stabilen Proben in Folge das Intervall verdoppeln (bis Cap), Drift/Fehler
    # setzen zurück. Archiviert: Termin ans Archiv-Fenster (kein eigener
    # Re-Probe-Loop — die Wiederaufnahme läuft über discover/recheckAfter).
    sch = cand.get("schedule") if isinstance(cand.get("schedule"), dict) else _new_schedule()
    cand["schedule"] = sch
    sch["lastProbeAt"] = cand["lastProbe"]
    sch["lastP50Ms"] = (cand.get("totalMs") or {}).get("p50")
    sch["lastP95Ms"] = (cand.get("totalMs") or {}).get("p95")
    sch["lastErrors"] = int(res.get("errors") or 0)
    if cand["status"] == "monitoring":
        if _stable_enough(prev_total, res.get("totalMs") or {},
                          int(res.get("errors") or 0)):
            sch["stableStreak"] = int(sch.get("stableStreak") or 0) + 1
            if (sch["stableStreak"] >= STABLE_RUNS
                    and sch["intervalHours"] < MAX_INTERVAL_HOURS):
                sch["intervalHours"] = min(MAX_INTERVAL_HOURS,
                                           max(1, int(sch["intervalHours"])) * 2)
                sch["stableStreak"] = 0
        else:
            sch["stableStreak"] = 0
            sch["intervalHours"] = INTERVAL_HOURS
        sch["nextProbeAt"] = _now_iso(time.time() + sch["intervalHours"] * 3600)
    else:
        sch["stableStreak"] = 0
        sch["intervalHours"] = ARCHIVE_DAYS * 24
        sch["nextProbeAt"] = _now_iso(time.time() + ARCHIVE_DAYS * 86400)
    # known erst NACH der Probe schreiben (nie Blindvermerke für Ausfälle ohne
    # Versuch — so bleibt die Wiederentdeckung über den Hash intakt).
    m = catalog.find_model(ref)
    k = known.setdefault(ref, {})
    k["hash"] = _metadata_hash(m) if m else k.get("hash") or ""
    k["firstSeen"] = k.get("firstSeen") or _now_iso()
    k["lastSeen"] = _now_iso()
    if cand["status"] == "archived":
        k["lastFail"] = cand["lastProbe"]
    _hist_record_probe(cand, res, mode)
    return res


def _run_guarded(mode: str):
    """Lauf-Reservierung; None = es läuft bereits etwas."""
    with RUN_LOCK:
        if RUN["running"]:
            return None
        RUN.update(running=True, mode=mode, startedAt=time.time(),
                   finishedAt=None, error=None)
        return True


def _run_finish(err: str | None = None):
    with RUN_LOCK:
        RUN.update(running=False, finishedAt=time.time(), error=err)


def run_discovery(samples: int | None = None) -> dict:
    """Neue Kandidaten entdecken und proben (Tageslimit gilt, kein Auto-Apply)."""
    if not _run_guarded("discovery"):
        return {"started": False, "running": True, "mode": RUN.get("mode")}
    err, results = None, {}
    try:
        queue = discover(force=False)   # Tageslimit respektieren
        with FILE_LOCK:
            st = _load_state()
        known = _load_known()
        by_ref = {c["ref"]: c for c in st["candidates"]}
        for c in queue:
            cand = by_ref.get(c["ref"])
            if not cand or cand.get("status") != "queued":
                continue
            if not cand.get("free") and _paid_today(st) >= MAX_PAID_PER_DAY:
                _log(f"Kostenbremse: bezahlte Discovery-Probe für {cand['ref']} "
                     f"übersprungen ({MAX_PAID_PER_DAY}/Tag)")
                continue
            res = _probe_and_update(cand, known, samples=samples, mode="discovery")
            if not cand.get("free"):
                _bump_paid(st)
            results[cand["ref"]] = {"status": cand["status"], "score": cand.get("score"),
                                    "errors": cand.get("errors"), "detail": res.get("detail")}
        st["checkedAt"] = _now_iso()
        _flush_archived(st)
        _prune_archive(st)
        with FILE_LOCK:
            _save_all(st, known)
        moved = [r for r, v in results.items() if v["status"] == "archived"]
        if moved:
            _log(f"discovery: archiviert: {', '.join(moved)}")
    except Exception as e:  # noqa: BLE001 — der Lauf darf den Server nie reißen
        err = str(e)
        _log(f"discovery fehlgeschlagen: {e}")
    finally:
        _run_finish(err)
    return {"started": True, "mode": "discovery", "results": results, "error": err}


def run_monitoring(samples: int | None = None) -> dict:
    """Nur FÄLLIGE Monitoring-Kandidaten neu proben (adaptives nextProbeAt).

    Ausfälle/Langsame archivieren. Ohne explizite Sample-Zahl gilt
    HESTIA_SPEED_STABILITY_SAMPLES (Standard 5) — die automatische
    Stabilitätsmessung darf etwas genauer sein als der Discovery-Default.
    """
    if not _run_guarded("monitoring"):
        return {"started": False, "running": True, "mode": RUN.get("mode")}
    err, results = None, {}
    try:
        with FILE_LOCK:
            st = _load_state()
        known = _load_known()
        now_iso = _now_iso()
        watch = [c for c in st["candidates"]
                 if c.get("status") == "monitoring" and _schedule_due(c, now_iso)]
        n = samples or STABILITY_SAMPLES
        for cand in watch:
            if not cand.get("free") and _paid_today(st) >= MAX_PAID_PER_DAY:
                # Nicht fällig lassen: sonst würde der Loop alle 60 s erneut
                # anklopfen. Auf den nächsten Tag verschieben (Cap-Reset).
                sch = cand.get("schedule") if isinstance(cand.get("schedule"), dict) \
                    else _new_schedule()
                sch["nextProbeAt"] = _now_iso(time.time() + 24 * 3600)
                cand["schedule"] = sch
                _log(f"Kostenbremse: bezahlte Monitoring-Probe für {cand['ref']} "
                     f"übersprungen ({MAX_PAID_PER_DAY}/Tag), nächster Termin morgen")
                continue
            res = _probe_and_update(cand, known, samples=n, mode="monitoring")
            if not cand.get("free"):
                _bump_paid(st)
            results[cand["ref"]] = {"status": cand["status"], "score": cand.get("score"),
                                    "errors": cand.get("errors"), "detail": res.get("detail")}
        st["checkedAt"] = _now_iso()
        _flush_archived(st)
        _prune_archive(st)
        with FILE_LOCK:
            _save_all(st, known)
    except Exception as e:  # noqa: BLE001
        err = str(e)
        _log(f"monitoring fehlgeschlagen: {e}")
    finally:
        _run_finish(err)
    return {"started": True, "mode": "monitoring", "results": results, "error": err}


def run_probe_refs(refs: list, samples: int | None = None) -> dict:
    """Manuelle Probe konkreter refs (Panel-Button) — legt Kandidaten an/aktualisiert."""
    if not _run_guarded("probe"):
        return {"started": False, "running": True, "mode": RUN.get("mode")}
    err, results = None, {}
    try:
        with FILE_LOCK:
            st = _load_state()
        known = _load_known()
        for ref in refs or []:
            ref = (ref or "").strip()
            if not ref:
                continue
            cand = next((c for c in st["candidates"] if c["ref"] == ref), None)
            if not cand:
                arch_idx = next((i for i, a in enumerate(st["archive"])
                                 if a.get("ref") == ref), None)
                if arch_idx is not None:
                    arch = st["archive"].pop(arch_idx)
                    cand = {k: v for k, v in arch.items()
                            if k not in ("archivedAt", "recheckAfter")}
                else:
                    m = catalog.find_model(ref)
                    if not m:
                        results[ref] = {"status": "unbekannt",
                                        "detail": "nicht im Katalog"}
                        continue
                    cand = _new_candidate(m)
                st["candidates"].append(cand)
            res = _probe_and_update(cand, known, samples=samples, mode="probe")
            if not cand.get("free"):
                _bump_paid(st)
            results[ref] = {"status": cand["status"], "score": cand.get("score"),
                            "errors": cand.get("errors"), "detail": res.get("detail")}
        st["checkedAt"] = _now_iso()
        _flush_archived(st)
        _prune_archive(st)
        with FILE_LOCK:
            _save_all(st, known)
    except Exception as e:  # noqa: BLE001
        err = str(e)
        _log(f"probe fehlgeschlagen: {e}")
    finally:
        _run_finish(err)
    return {"started": True, "mode": "probe", "results": results, "error": err}


def run_async(mode: str, refs: list | None = None, samples: int | None = None) -> dict:
    """Lauf im Hintergrund-Thread starten (API-Endpoint, kehrt sofort zurück)."""
    with RUN_LOCK:
        if RUN["running"]:
            return {"started": False, "running": True, "mode": RUN.get("mode"),
                    "detail": "es läuft bereits ein Speed-Lauf"}
    thread = threading.Thread(target=_thread_main, args=(mode, refs, samples), daemon=True)
    thread.start()
    return {"started": True, "running": True, "mode": mode}


def _thread_main(mode: str, refs: list | None, samples: int | None):
    try:
        if mode == "discovery":
            run_discovery(samples=samples)
        elif mode == "probe":
            run_probe_refs(refs or [], samples=samples)
        else:
            run_monitoring(samples=samples)
    except Exception as e:  # noqa: BLE001
        _log(f"Lauf {mode} fehlgeschlagen: {e}")


# ── Empfehlung + Apply (nur per Klick — kein Auto-Apply) ────────────────────

def recommend():
    """Beste Empfehlung aus den Monitoring-Kandidaten (free-only, niedrigster Score)."""
    st = _load_state()
    cands = [c for c in st.get("candidates", [])
             if c.get("status") == "monitoring" and c.get("free")
             and c.get("score") is not None]
    if not cands:
        return None
    best = min(cands, key=lambda c: (c["score"], c["ref"]))
    return {"ref": best["ref"], "score": best["score"],
            "ttfbMs": best.get("ttfbMs"), "totalMs": best.get("totalMs")}


def _mark_applied(ref: str) -> None:
    """lastApplied im Speed-State vermerken — best-effort.

    Der Apply selbst ist zu diesem Zeitpunkt über hermes.apply bereits
    committet (SOT + Workflow); ein fehlgeschlagener State-Schreibvorgang darf
    ihn deshalb nie zum Fehler machen. Gilt auch für den direkten Apply über
    POST /api/hermes/apply (server.py ruft diesen Helper dort auf).
    """
    try:
        with FILE_LOCK:
            st = _load_state()
            st["lastApplied"] = {"ref": ref, "at": _now_iso()}
            _save_state(st)
    except Exception as e:  # noqa: BLE001 — Vermerk darf den Apply nie kippen
        _log(f"lastApplied-Vermerk fehlgeschlagen ({ref}): {type(e).__name__}: {e}")


def apply_recommendation(ref: str | None = None) -> dict:
    """Empfehlung als Hermes-Primary anwenden — ausschließlich über hermes.apply.

    Das Payload wird aus der aktuellen SOT gebaut ({**_public(config), primary})
    — der Fallback bleibt unangetastet, es wird nie live in die Config
    geschrieben und der Apply-Workflow erledigt den Rest.
    """
    target = (ref or "").strip()
    if not target:
        rec = recommend()
        if not rec:
            return {"applied": False, "detail": "keine Kandidaten"}
        target = rec["ref"]
    ok, info = catalog.usable(target)
    if not ok:
        raise hermes.ApplyError(f"{target}: {info}")
    provider, _, model = target.partition("/")
    cfg = hermes._public(hermes.config(force=True))
    payload = {**cfg, "primary": {"provider": provider, "model": model}}
    res = hermes.apply(payload)
    _mark_applied(target)
    _log(f"Empfehlung angewendet: primary={target}")
    return {"applied": True, "ref": target, "result": res}


# ── O19-Telemetrie: Snapshot der Hermes-state.db lesen (read-only) ──────────

def _to_epoch(value):
    """Epoch-Sekunden aus REAL (s/ms) oder ISO-String; None bei Unbrauchbarem."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    s = str(value).strip()
    if not s:
        return None
    try:
        v = float(s)
        return v / 1000.0 if v > 1e11 else v
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _iso_or_none(epoch):
    if epoch is None:
        return None
    return _now_iso(epoch)


def _approx_response_ms(cutoff: float):
    """Näherung der Antwortzeit (ms) aus user→letzte-assistant-Paaren je Session.

    Reine Zeitstempel-Aggregation — kein Nachrichteninhalt wird gelesen.
    """
    con = sqlite3.connect(f"file:{STATE_DB}?mode=ro&immutable=1", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT s.model AS model, m.role AS role, m.timestamp AS ts, "
            "m.session_id AS sid FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE m.role IN ('user', 'assistant') AND m.timestamp >= ? "
            "ORDER BY m.session_id, m.timestamp", (cutoff,)).fetchall()
    finally:
        con.close()
    by_model: dict = {}
    open_user = None
    open_sid = None
    open_model = None
    for r in rows:
        if r["sid"] != open_sid:            # neue Session → Paarung neu starten
            open_sid, open_user, open_model = r["sid"], None, None
        if r["role"] == "user":
            open_user, open_model = r["ts"], r["model"]
        elif open_user is not None and r["ts"] is not None:
            delta_ms = (float(r["ts"]) - float(open_user)) * 1000.0
            if 0 <= delta_ms < 10 * 60_000:  # unplausible Sprünge (Lücken/Nacht) ignorieren
                by_model.setdefault(open_model or "?", []).append(delta_ms)
            open_user = None
    out = {k: {"avgMs": round(sum(v) / len(v), 1), "pairs": len(v)}
           for k, v in by_model.items() if v}
    return out or None


def telemetry(window_hours: int = 24) -> dict:
    """Verbrauch je Modell aus dem O19-Snapshot (session_model_usage).

    Fehlt der Snapshot (Seitenwagen aus/erster Start), kommt
    {available: False, ...} — die Telemetrie darf NIE die API reißen.
    """
    out = {"available": False, "generatedAt": _now_iso(), "windowHours": window_hours,
           "models": [], "totals": {"requests": 0, "inputTokens": 0, "outputTokens": 0,
                                    "costUsd": 0.0}}
    if not os.path.isfile(STATE_DB):
        out["reason"] = f"{STATE_DB} nicht vorhanden (Snapshot-Seitenwagen inaktiv?)"
        return out
    cutoff = time.time() - int(window_hours) * 3600
    try:
        con = sqlite3.connect(f"file:{STATE_DB}?mode=ro&immutable=1", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT model, billing_provider, SUM(api_call_count) AS requests, "
                "SUM(input_tokens) AS in_tok, SUM(output_tokens) AS out_tok, "
                "SUM(estimated_cost_usd) AS cost_usd, MIN(first_seen) AS first_seen, "
                "MAX(last_seen) AS last_seen FROM session_model_usage "
                "WHERE last_seen >= ? "
                "GROUP BY model, billing_provider", (cutoff,)).fetchall()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        out["error"] = f"Snapshot nicht lesbar: {type(e).__name__}: {e}"
        return out
    models = []
    for r in rows:
        last = _to_epoch(r["last_seen"])
        first = _to_epoch(r["first_seen"])
        if last is not None and last < cutoff:
            continue                    # außerhalb des Fensters
        models.append({
            "model": r["model"],
            "provider": r["billing_provider"],
            "requests": int(r["requests"] or 0),
            "inputTokens": int(r["in_tok"] or 0),
            "outputTokens": int(r["out_tok"] or 0),
            "costUsd": round(float(r["cost_usd"] or 0.0), 6),
            "firstSeen": _iso_or_none(first),
            "lastSeen": _iso_or_none(last),
        })
    models.sort(key=lambda m: -m["requests"])
    out["totals"] = {
        "requests": sum(m["requests"] for m in models),
        "inputTokens": sum(m["inputTokens"] for m in models),
        "outputTokens": sum(m["outputTokens"] for m in models),
        "costUsd": round(sum(m["costUsd"] for m in models), 6),
    }
    out.update(available=True, models=models)
    try:
        out["responseMs"] = _approx_response_ms(cutoff)
    except Exception as e:  # noqa: BLE001 — Näherung ist optional
        out["responseMs"] = None
        out["responseMsError"] = str(e)
    return out


# ── Panel-Status + Automatik ────────────────────────────────────────────────

def state() -> dict:
    """Panel-Abfrage: Speed-Zustand + Telemetrie + Empfehlung (nie werfend)."""
    st = _load_state()
    with RUN_LOCK:
        run = dict(RUN)
    try:
        rec = recommend()
    except Exception as e:  # noqa: BLE001
        rec = None
        _log(f"recommend fehlgeschlagen: {e}")
    try:
        tel = telemetry()
    except Exception as e:  # noqa: BLE001
        tel = {"available": False, "error": str(e)}
    return {
        "auto": AUTO,
        "running": run,
        "lastRun": st.get("checkedAt"),
        "config": {"path": SPEED_PATH, "knownPath": KNOWN_PATH, "stateDb": STATE_DB,
                   "historyDb": HISTORY_DB,
                   "intervalHours": INTERVAL_HOURS, "samples": SAMPLES,
                   "stabilitySamples": STABILITY_SAMPLES, "stableRuns": STABLE_RUNS,
                   "stableTolerance": STABLE_TOLERANCE,
                   "maxIntervalHours": MAX_INTERVAL_HOURS,
                   "discoveryHours": DISCOVERY_HOURS,
                   "maxPaidPerDay": MAX_PAID_PER_DAY,
                   "sessionDeleteDelay": SESSION_DELETE_DELAY,
                   "probeTimeout": PROBE_TIMEOUT, "maxNewPerDay": MAX_NEW_PER_DAY,
                   "archiveDays": ARCHIVE_DAYS, "freeOnly": FREE_ONLY,
                   "slowP95Ms": SLOW_P95_MS, "providers": ALLOWED_PROVIDERS},
        "state": st,
        "recommendation": rec,
        "telemetry": tel,
    }


_LOOP_THREAD = None
_STOP = threading.Event()


def _seconds_until_auto_run(now: float | None = None) -> float:
    """Sekunden bis zum nächsten Automatik-Lauf (0.0 = sofort fällig).

    Reine Lese-Logik (keine Writes, keine Proben): nächster Termin ist das
    Minimum aus dem frühesten nextProbeAt eines Monitoring-Kandidaten und
    checkedAt + Discovery-Intervall (HESTIA_SPEED_DISCOVERY_HOURS). Fehlt
    beides (erste Installation) → 0.0. Junger checkedAt und zukünftige
    Termine — typisch nach Deploy/Neustart — liefern die Restzeit, d.h. KEIN
    Sofort-Probe; ein fälliger Kandidat bleibt trotzdem sofort dran.
    """
    st = _load_state()
    now_ts = time.time() if now is None else now
    due: list[float] = []
    for cand in st["candidates"]:
        if cand.get("status") != "monitoring":
            continue
        nxt = _to_epoch((cand.get("schedule") or {}).get("nextProbeAt"))
        due.append(nxt if nxt is not None else now_ts)  # ohne Termin → sofort
    last = _to_epoch(st.get("checkedAt"))
    if last is not None:
        due.append(last + max(60, DISCOVERY_HOURS * 3600))
    if not due:
        return 0.0
    return max(0.0, min(due) - now_ts)


def loop(stop_event: threading.Event):
    """Automatik: fällige Monitoring-Kandidaten + Discovery (kein Auto-Apply).

    Neustart-Schutz: `_seconds_until_auto_run` liefert die Restzeit bis zur
    nächsten Fälligkeit — ein frischer Deploy mit jungem checkedAt und
    zukünftigen Terminen probt daher NICHT sofort. Erste Installation (kein
    checkedAt, kein Termin) läuft sofort; danach gilt der adaptive Rhythmus.
    """
    _log(f"Speed-Automatik aktiv (Basisintervall {INTERVAL_HOURS} h, Discovery "
         f"{DISCOVERY_HOURS} h, Monitoring-Samples {STABILITY_SAMPLES}, "
         f"free-only={FREE_ONLY}, Tageslimit {MAX_NEW_PER_DAY})")
    while not stop_event.is_set():
        wait = _seconds_until_auto_run()
        if wait > 0:
            _log(f"nächster Speed-Lauf in {wait / 3600.0:.1f} h (adaptive Fälligkeit)")
            stop_event.wait(wait)
            continue
        try:
            run_monitoring()
        except Exception as e:  # noqa: BLE001
            _log(f"Monitoring fehlgeschlagen: {e}")
        try:
            run_discovery()
        except Exception as e:  # noqa: BLE001
            _log(f"Discovery fehlgeschlagen: {e}")
        # Neu rechnen statt festem Intervall — nach dem Lauf ist checkedAt
        # frisch, der Boden (60 s) verhindert nur eine Busy-Schleife.
        stop_event.wait(max(60.0, _seconds_until_auto_run()))
    _log("Speed-Automatik beendet")


def start():
    """Daemon-Thread nur bei HESTIA_SPEED_AUTO=1 — NIE beim Import proben."""
    if not AUTO:
        _log("Speed-Automatik inaktiv (HESTIA_SPEED_AUTO=0) — Proben nur manuell")
        return
    global _LOOP_THREAD, _STOP
    if _LOOP_THREAD and _LOOP_THREAD.is_alive():
        return
    _STOP = threading.Event()
    _LOOP_THREAD = threading.Thread(target=loop, args=(_STOP,), daemon=True)
    _LOOP_THREAD.start()
