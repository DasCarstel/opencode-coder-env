#!/usr/bin/env python3
"""failover.py — Ausfallschutz für die Tier-Modelle.

Prüft die aktiven Tier-Modelle und weicht bei Ausfall auf das hinterlegte
bezahlte Modell aus (Datei .opencode/hestia-fallbacks.json, vom Panel gepflegt):

    {"fast":   {"model": "opencode/<gratis>",   "variant": ""},
     "medium": {"model": "opencode/<gratis>",   "variant": ""}}

Ablauf:
  - Ereignis-Watcher (SSE auf GET {OC_SERVER_URL}/api/event, Basic-Auth): OpenCode
    meldet Modellfehler typisiert — "provider.free-limit" (429, mit retryAfterMs),
    "provider.quota" (402) und sonstige. Klassifizierung:
      free_limit → sofortiger Swap (Schwelle 1) + Rückweg-Timer = now + retryAfterMs
      quota      → sofortiger Swap, ohne Rückweg-Timer (Konto leer = dauerhaft)
      other      → bestehende Schwelle aus der SOT (Default 2), Watchdog-Muster
  - Probe (Watchdog): winzige Session je Tier-Modell ("Antworte nur mit OK") über
    den OpenCode-Server; Erfolg = session.execution.succeeded. Höchstens 1×/h und
    primär als Netz, wenn der Ereignis-Stream getrennt ist.
  - Rückweg: ohne Probe — sobald paidUntil (now + retryAfterMs) erreicht ist,
    wechselt das Tier zurück aufs Primärmodell (Check alle ≤ 60 s, kein sleep).
  - Nach HESTIA_FAIL_THRESHOLD Fehlschlägen in Folge UND wenn ein Ausfall-Modell
    hinterlegt ist, wird dieses als Tier-Modell geschrieben (Commit + Rollout) —
    automatisch nur wenn HESTIA_AUTO_FAILOVER=1, sonst per Klick im Panel.
  - Beim Swap werden auch die hergeleiteten Primary-Agents (build/plan) auf das
    neue @medium gezogen; manuell gesetzte Agenten bleiben unangetastet.

Schutz vor Blindflug und Dauer-Neustarts (jede Regel verhindert einen konkreten
Fehlfall aus der Praxis):

  1. Kontingent ≠ Ausfall. Ist das aktive OpenCode-Go-Konto erschöpft oder
     rate-limited (HTTP 429), hilft ein Modellwechsel nicht — dann passiert
     NICHTS und accounts.py übernimmt (Kontowechsel).
  2. Kein No-op-Swap. Steht das Tier schon auf dem Ausweich-Modell, wird nicht
     geschrieben: das erzeugte vorher einen (leeren) Commit und damit einen
     Rollout mit Container-Neustarts, ohne dass sich irgendetwas änderte.
     `github_api.put_files` überspringt identische Inhalte zusätzlich generell.
  3. Ausweich-Modell wird vorher geprobt. Antwortet es selbst nicht, ist der
     Wechsel sinnlos.
  4. Modell-Gedächtnis + Quarantäne (.opencode/hestia-model-health.json): ein
     Modell, das mehrfach in Folge echt ausgefallen ist, wird für
     HESTIA_QUARANTINE_HOURS nicht als Ziel gewählt → kein Hin-und-Her-Flip-Flop.
     Die Datei liegt außerhalb des paths-Filters von rollout.yml, ein Schreiben
     kostet also nie einen Container-Neustart.
  5. Ein Rollout pro Prüflauf. Vorher dispatchte jedes Tier einzeln; bei zwei
     gleichzeitig ausfallenden Tiers liefen zwei Rollouts parallel und starteten
     die Workspaces doppelt neu.

Die Laufzeit-Parameter (enabled/threshold/Intervall/Timeout/Quarantäne) liegen
in der SOT .opencode/hestia-failover.jsonc und werden über
POST /api/failover/settings vom Panel geschrieben; fehlt die Datei, gelten die
HESTIA_*-Defaults. Die SOT hat Vorrang vor der Env-Variable HESTIA_AUTO_FAILOVER.
"""
import base64
import collections
import json
import os
import threading
import time
import urllib.request

import catalog
import github_api
import suggest

TIERS = ("fast", "medium", "heavy")
PRIMARY_AGENTS = ("build", "plan")
FALLBACKS_PATH = os.environ.get("HESTIA_FALLBACKS_PATH", ".opencode/hestia-fallbacks.json")
AGENTS_PATH = os.environ.get("HESTIA_AGENTS_PATH", ".opencode/hestia-primary-agents.json")
FAILOVER_SETTINGS_PATH = os.environ.get(
    "HESTIA_FAILOVER_SETTINGS_PATH", ".opencode/hestia-failover.jsonc"
)
# Modell-Gedächtnis (Fehlschlagsserie + Quarantäne je Tier und je Modell).
# Bewusst NICHT im paths-Filter von rollout.yml → Schreiben löst keinen Rollout aus.
HEALTH_PATH = os.environ.get("HESTIA_HEALTH_PATH", ".opencode/hestia-model-health.json")
PROBE_TIMEOUT = int(os.environ.get("HESTIA_PROBE_TIMEOUT", "35"))
THRESHOLD = int(os.environ.get("HESTIA_FAIL_THRESHOLD", "2"))
INTERVAL = int(os.environ.get("HESTIA_PROBE_INTERVAL", "900"))
QUARANTINE_HOURS = int(os.environ.get("HESTIA_QUARANTINE_HOURS", "6"))
AUTO = os.environ.get("HESTIA_AUTO_FAILOVER", "0") == "1"

# Ereignisgesteuerter Ausfallschutz: OpenCode meldet Modellfehler strukturiert
# ("provider.free-limit"/"provider.quota"/…) als SSE-Events auf /api/event.
# Der Watchdog (Proben) ist nur noch das Netz für den Fall, dass der Stream
# getrennt ist — höchstens 1×/h.
EVENT_BACKOFF_MIN = int(os.environ.get("HESTIA_EVENT_BACKOFF", "5"))    # erster Reconnect (s)
EVENT_BACKOFF_MAX = int(os.environ.get("HESTIA_EVENT_BACKOFF_MAX", "60"))
EVENT_READ_TIMEOUT = int(os.environ.get("HESTIA_EVENT_READ_TIMEOUT", "90"))  # SSE-Lesefrist (s)
EVENT_HISTORY = int(os.environ.get("HESTIA_EVENT_HISTORY", "50"))       # Ringpuffer fürs Panel
RETURN_TICK = int(os.environ.get("HESTIA_RETURN_TICK", "60"))           # Rückweg-Check-Takt (s)
WATCHDOG_MIN = int(os.environ.get("HESTIA_WATCHDOG_INTERVAL", "3600"))  # Proben höchstens 1×/h
# Rollout-Modi: "sync" = sync.yml (nur Overrides-Datei in die Container schreiben,
# kein Neustart — der Plugin-Cache invalidiert über den Fingerprint beim nächsten
# Prompt); "restart" = klassisches rollout.yml für echte Deployments.
ROLLOUT_WORKFLOW = os.environ.get("ROLLOUT_WORKFLOW", "rollout.yml")
SYNC_WORKFLOW = os.environ.get("SYNC_WORKFLOW", "sync.yml")

# Effort-Rang für die Ableitung der Primary-Agents (plan = höchste Variante).
# Bewusst gespiegelt zu server.VARIANT_RANK — hier nur die für den Failover
# nötige Teilmenge, um keinen Import-Zyklus (server → failover) zu erzeugen.
VARIANT_RANK = {"minimal": 1, "none": 2, "low": 3, "medium": 4, "thinking": 4,
                "high": 5, "xhigh": 6, "max": 7}

# Grenzen für die Panel-Eingaben (POST /api/failover/settings)
THRESHOLD_RANGE = (1, 10)
INTERVAL_RANGE = (60, 86400)
TIMEOUT_RANGE = (5, 300)
QUARANTINE_RANGE = (0, 72)          # 0 = Quarantäne aus
SETTINGS_KEYS = ("enabled", "threshold", "intervalSeconds", "timeoutSeconds",
                 "quarantineHours")


def default_settings() -> dict:
    """Startwerte = heutige Env-Defaults (Fallback, falls die SOT fehlt)."""
    return {
        "enabled": bool(AUTO),
        "threshold": THRESHOLD,
        "intervalSeconds": INTERVAL,
        "timeoutSeconds": PROBE_TIMEOUT,
        "quarantineHours": QUARANTINE_HOURS,
    }


STATE = {
    "autoFailover": AUTO,
    "interval": INTERVAL,
    "threshold": THRESHOLD,
    "timeout": PROBE_TIMEOUT,
    "quarantineHours": QUARANTINE_HOURS,
    "fallbacks": {},
    "current": {},
    "fails": {},
    "lastRun": None,
    "lastResults": {},
    "health": {"tiers": {}, "models": {}},
    "quota": {},
    "events": [],
    # Ereignis-Watcher-Verbindung: {"connected": bool, "lastConnect": float,
    # "lastError": str|None, "lastChange": float} — für das Panel.
    "eventStream": {"connected": False, "lastConnect": None, "lastError": None},
    # Frühester Rückweg-Termin über alle Tiers ("bis wann läuft bezahlt?"),
    # aus den paidUntil-Einträgen im Gedächtnis.
    "freeLimitUntil": None,
}
LOCK = threading.Lock()

# Ringpuffer der erkannten Ereignisse (für /api/failover/events) und Dedupe-Liste
# (gleiche Session + gleicher Fehlertyp nur einmal) — beide im RAM; das
# Entscheidungsgedächtnis selbst liegt im Health-File (Quelle der Wahrheit).
EVENTS = collections.deque(maxlen=EVENT_HISTORY)
_SEEN = collections.deque(maxlen=200)


class SettingsError(Exception):
    """Ungültige Nutzereingabe für die Ausfallschutz-Einstellungen."""


def _log(msg):
    print(f"[hestia-failover] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _now_iso(ts: float | None = None) -> str:
    """UTC-Zeitstempel (ISO-8601, sekundengenau) — Vergleichbarkeit per String."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if ts is None else ts))


def _now() -> float:
    """Zeitquelle für Timer — bewusst injizierbar (Tests setzen eine Fake-Clock)."""
    return time.time()


def _clamp_int(value, lo: int, hi: int, fallback: int) -> int:
    """Zahl aus der SOT übernehmen; unbrauchbar → Default (kein Panel-Fehler)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return n if lo <= n <= hi else fallback


def read_settings() -> dict:
    """Ausfallschutz-SOT lesen und normalisieren.

    Fehlende/kaputte Datei oder GitHub-Fehler → die Env-Defaults, damit das
    Panel immer eine plausible Konfiguration sieht. Liegt die SOT vor, hat ihr
    Feld "enabled" Vorrang vor HESTIA_AUTO_FAILOVER.
    """
    data = {}
    try:
        raw = github_api.get_file(FAILOVER_SETTINGS_PATH)
        if raw:
            import hjsonc
            data = hjsonc.parse(raw) or {}
    except Exception as e:  # noqa: BLE001
        _log(f"settings: {FAILOVER_SETTINGS_PATH} nicht lesbar ({e}) — Defaults")
        data = {}
    if not isinstance(data, dict):
        data = {}
    d = default_settings()
    enabled = d["enabled"]
    if isinstance(data.get("enabled"), bool):
        enabled = data["enabled"]
    return {
        "enabled": enabled,
        "threshold": _clamp_int(data.get("threshold"), *THRESHOLD_RANGE, d["threshold"]),
        "intervalSeconds": _clamp_int(
            data.get("intervalSeconds"), *INTERVAL_RANGE, d["intervalSeconds"]),
        "timeoutSeconds": _clamp_int(
            data.get("timeoutSeconds"), *TIMEOUT_RANGE, d["timeoutSeconds"]),
        "quarantineHours": _clamp_int(
            data.get("quarantineHours"), *QUARANTINE_RANGE, d["quarantineHours"]),
    }


def _require_int(payload: dict, key: str, lo: int, hi: int) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"'{key}': muss eine ganze Zahl zwischen {lo} und {hi} sein")
    if not (lo <= value <= hi):
        raise SettingsError(f"'{key}': {value} liegt außerhalb {lo}..{hi}")
    return value


def update_settings(payload: dict) -> dict:
    """Panel-Einstellungen validieren, als SOT committen und STATE aktualisieren.

    Wirkt sofort: der Probe-Thread wird live gestartet/gestoppt und liest
    Intervall/Schwelle je Iteration neu — kein Container-Neustart nötig.
    """
    if not isinstance(payload, dict):
        raise SettingsError("Body muss ein JSON-Objekt sein")
    unknown = sorted(set(payload) - set(SETTINGS_KEYS))
    if unknown:
        raise SettingsError(f"unbekannte Felder: {', '.join(unknown)}")

    current = read_settings()
    if "enabled" not in payload:
        raise SettingsError("'enabled' fehlt")
    enabled = payload["enabled"]
    if not isinstance(enabled, bool):
        raise SettingsError("'enabled' muss true oder false sein")
    for key in ("threshold", "intervalSeconds"):
        if key not in payload:
            raise SettingsError(f"'{key}' fehlt")

    settings = {
        "enabled": enabled,
        "threshold": _require_int(payload, "threshold", *THRESHOLD_RANGE),
        "intervalSeconds": _require_int(payload, "intervalSeconds", *INTERVAL_RANGE),
        # optional: ohne Feld bleibt der aktuelle Wert (SOT, sonst Default)
        "timeoutSeconds": (
            _require_int(payload, "timeoutSeconds", *TIMEOUT_RANGE)
            if "timeoutSeconds" in payload else current["timeoutSeconds"]
        ),
        "quarantineHours": (
            _require_int(payload, "quarantineHours", *QUARANTINE_RANGE)
            if "quarantineHours" in payload else current["quarantineHours"]
        ),
    }

    header = (
        "// Ausfallschutz-Einstellungen (SOT des Hestia-Panels).\n"
        "// Geschrieben über POST /api/failover/settings, gelesen von hestia/backend/failover.py.\n"
        "// quarantineHours: 0 schaltet die Modell-Quarantäne ab.\n"
    )
    text = header + json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    commit = github_api.put_files(
        {FAILOVER_SETTINGS_PATH: text}, "hestia: Ausfallschutz-Einstellungen aktualisiert")

    with LOCK:
        STATE["autoFailover"] = settings["enabled"]
        STATE["threshold"] = settings["threshold"]
        STATE["interval"] = settings["intervalSeconds"]
        STATE["timeout"] = settings["timeoutSeconds"]
        STATE["quarantineHours"] = settings["quarantineHours"]
    # Live-Umschaltung: Probe-Thread starten/stoppen, ohne Container-Neustart.
    _apply_loop_state(settings["enabled"])
    _log(
        f"settings: enabled={settings['enabled']} threshold={settings['threshold']} "
        f"intervall={settings['intervalSeconds']}s timeout={settings['timeoutSeconds']}s "
        f"quarantäne={settings['quarantineHours']}h "
        f"(Commit {str((commit or {}).get('sha', ''))[:8]})"
    )
    return {"saved": True, "settings": settings, "commit": commit}


def read_fallbacks() -> dict:
    try:
        raw = github_api.get_file(FALLBACKS_PATH)
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    return {t: (data.get(t) or None) for t in TIERS}


# ── Ereignis-Watcher: Modellfehler kommen live aus OpenCode selbst ───────────

def classify_error(etype: str) -> str:
    """OpenCode-Fehlertyp → Klasse: free_limit | quota | variant | other."""
    t = str(etype or "")
    if "free-limit" in t:
        return "free_limit"
    if "quota" in t:
        return "quota"
    if "no-route" in t:
        # provider.no-route ist meist eine Variante, die es für das Modell
        # nicht gibt (z. B. xhigh für ein Modell mit nur low/high/max) —
        # erst die ungültige Variante räumen, nicht aufs Ausweich-Modell
        # eskalieren (siehe handle_execution_failed, Klasse "variant").
        return "variant"
    return "other"


def _event_headers() -> dict:
    """Header für den SSE-Stream (Basic-Auth für den OpenCode-Server).

    Direkt gebaut aus OC_SERVER_USER/OC_SERVER_PASSWORD (vom bootstrap.py aus
    OpenBao gesetzt): die SSE-Verbindung braucht ein eigenes Read-Timeout
    an der URL /api/event — und das data:-Framing ist identisch zum
    suggest._event_watch (dort Basic-Auth eingebaut).
    """
    user = str(getattr(suggest, "OC_SERVER_USER", "") or os.environ.get("OC_SERVER_USER", ""))
    pw = str(getattr(suggest, "OC_SERVER_PASSWORD", "") or os.environ.get("OC_SERVER_PASSWORD", ""))
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache"}


def _set_stream(connected: bool, error: str | None) -> None:
    """Verbindungszustand des Ereignis-Streams im STATE aktualisieren."""
    with LOCK:
        st = STATE.setdefault("eventStream", {})
        st["connected"] = bool(connected)
        st["lastError"] = error or None
        st["lastChange"] = time.time()
        if connected:
            st["lastConnect"] = time.time()


def recent_events(limit: int = 20) -> list:
    """Ringpuffer der erkannten Ereignisse (neueste zuerst) fürs Panel."""
    try:
        n = max(1, min(int(limit or 20), EVENT_HISTORY))
    except (TypeError, ValueError):
        n = 20
    with LOCK:
        evs = list(EVENTS)
    return [dict(e) for e in reversed(evs[-n:])]


def status_extras() -> dict:
    """Panel-Zusatzfelder: Stream-Zustand, Rückweg-Timer, letztes Ereignis."""
    with LOCK:
        stream = dict(STATE.get("eventStream") or {})
        free_until = STATE.get("freeLimitUntil")
        last_event = dict(EVENTS[-1]) if EVENTS else None
    return {"eventStream": stream, "freeLimitUntil": free_until, "lastEvent": last_event}


def event_loop(stop_event: threading.Event):
    """SSE-Watcher: verbindet {OC_SERVER_URL}/api/event und klassifiziert Fehler.

    Reconnect mit Backoff (5 s → 60 s, Zurücksetzen bei jeder erfolgreichen
    Verbindung). Nur data:-Zeilen mit JSON werden gezählt (Framing wie
    suggest._event_watch); session.execution.failed-Events gehen an
    handle_execution_failed(). Der Thread darf nie unbeobachtet sterben:
    jede Iteration ist einzeln abgesichert, das Lesefenster über
    EVENT_READ_TIMEOUT so dass tote Verbindungen bemerkt werden.
    """
    backoff = float(EVENT_BACKOFF_MIN)
    while not stop_event.is_set():
        try:
            url = str(suggest.OC_SERVER_URL).rstrip("/") + "/api/event"
            req = urllib.request.Request(url, headers=_event_headers())
            _log(f"event-watcher: verbinde {url}")
            with urllib.request.urlopen(req, timeout=EVENT_READ_TIMEOUT) as resp:
                _set_stream(True, None)
                backoff = float(EVENT_BACKOFF_MIN)
                for raw in resp:
                    if stop_event.is_set():
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue   # SSE-Kommentar/Feldzeile — nur data: zählt
                    try:
                        ev = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if not isinstance(ev, dict) or ev.get("type") != "session.execution.failed":
                        continue
                    try:
                        handle_execution_failed(ev)
                    except Exception as e:  # noqa: BLE001
                        _log(f"event-watcher: Event nicht verarbeitbar: {e}")
        except Exception as e:  # noqa: BLE001
            _set_stream(False, str(e))
        if stop_event.is_set():
            break
        stop_event.wait(backoff)
        backoff = min(EVENT_BACKOFF_MAX, backoff * 2)
    _set_stream(False, None)


def _paid_until(entry) -> float:
    """paidUntil eines Tier-Eintrags als Zahl (0.0 = kein Rückweg-Timer)."""
    try:
        return float((entry or {}).get("paidUntil") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_free_ref(ref: str) -> bool:
    """Gratis-Modell? (Namens-Endung -free oder Preis 0 laut Katalog)

    Bewusst defensiv: Tests injizieren ein schlankes Stub-Modul für `catalog`,
    das `is_free` nicht zwingend mitbringt — der Suffix-Test genügt dort.
    """
    if str(ref or "").strip().lower().endswith("-free"):
        return True
    fn = getattr(catalog, "is_free", None)
    if callable(fn):
        try:
            return bool(fn(ref))
        except Exception:  # noqa: BLE001
            return False
    return False


def _strip_invalid_variant(tier: str, ref: str, var: str) -> tuple:
    """Ungültige Variante aus einem Tier-Eintrag räumen (provider.no-route).

    Prüft gegen die Live-Varianten des Modells (catalog.usable, dieselbe
    Quelle wie suggest._resolve_tier_model). Ohne eine prüfbare Varianten-
    Liste wird nichts geschrieben (Blindflug vermeiden). Der Swap ist
    idempotent: steht das Tier schon ohne Variante, meldet put_files
    "unchanged" und es gibt keinen Commit/Rollout.
    """
    ok, res = catalog.usable(ref)
    live = (res.get("variants") or []) if ok and isinstance(res, dict) else []
    if not live:
        return f"übersprungen — Live-Varianten von {ref} nicht prüfbar", False
    if var in live:
        return f"übersprungen — Variante '{var}' ist für {ref} gültig", False
    try:
        res_swap = _swap_tier(tier, {"model": ref, "variant": ""})
    except Exception as e:  # noqa: BLE001
        return f"FEHLER: {e}", False
    if res_swap.get("unchanged"):
        return f"übersprungen — {ref} bereits ohne ungültige Variante", False
    return (f"Variante entfernt ({ref} kennt '{var}' nicht, "
            f"live: {', '.join(live)})", True)


def handle_execution_failed(event: dict) -> dict:
    """Ein 'session.execution.failed'-Event klassifizieren und umsetzen.

    - free_limit → Schwelle 1: sofortiger Swap je Tier mit Ausfall-Modell,
      Rückweg-Timer = now + retryAfterMs (persistiert als tiers[tier].paidUntil).
    - quota      → Schwelle 1, Swap ohne Rückweg-Timer (Konto leer = dauerhaft).
    - variant    → provider.no-route: vermutlich eine Variante, die es für das
      gesetzte Modell nicht gibt. Erst die ungültige Variante aus dem Preset
      räumen (Commit + EIN sync-Rollout) — kein Wechsel aufs Ausweich-Modell
      und keine Fehlschlag-Serie; der Retry auf demselben Modell ohne Variante
      genügt.
    - other      → Schwelle aus der SOT (Default 2) wie beim Watchdog.

    Quelle der Wahrheit für die Fehlschlagserie ist die PERSISTIERTE Serie im
    Health-File (health.models[label].failStreak); dedupliziert wird pro
    (Session, Fehlertyp). Ohne Ausfall-Modell gibt es sichtbar einen Eintrag
    "skipped: no fallback" (kein stiller Skip). Der Event-Pfad probiert das
    Ausfall-Modell NICHT vorher (ein zusätzlicher Probe-Turn kostet einen
    bezahlten Turn ohne Nutzen — der Swap ist idempotent und der Rückweg-Timer
    korrigiert im Zweifel selbst). Rückgabe dient Tests und dem Panel-Feed.
    """
    data = dict(event.get("data") or {})
    err = dict(data.get("error") or {})
    etype = str(err.get("type") or "")
    if not etype:
        return {"ignored": True, "reason": "Event ohne Fehlertyp"}
    kind = classify_error(etype)
    session = str(data.get("sessionID") or "")
    with LOCK:
        if (session, etype) in _SEEN:
            return {"deduped": True, "kind": kind}
        _SEEN.append((session, etype))
    rec = {"at": _now(), "iso": _now_iso(), "kind": kind, "type": etype,
           "session": session,
           "model": str(data.get("model") or err.get("model") or ""),
           "status": err.get("status"), "retryAfterMs": err.get("retryAfterMs"),
           "message": str(err.get("message") or "")[:200]}
    cfg = github_api.get_overrides()["config"]
    preset = (cfg.get("presets") or {}).get(os.environ.get("HESTIA_PRESET_PROVIDER", "hestia"), {})
    fallbacks = read_fallbacks()
    health = read_health()
    old_sig = _health_sig(health)
    apply = _auto_enabled()
    swaps, streaks, swapped_tiers = {}, {}, []
    variant_fixed = []
    for tier in TIERS:
        entry = preset.get(tier) or {}
        ref = (entry.get("model") or "").strip()
        var = (entry.get("variant") or "").strip()
        if not ref:
            continue
        label_cur = ref + (f"#{var}" if var else "")
        # Nur betroffene Tiers behandeln: ein free-limit kann ausschließlich von
        # einem Gratis-Modell kommen, "Insufficient account funds" (402) nur vom
        # Zen-Konto (Provider opencode, kostenpflichtig). Ohne diesen Filter kippt
        # ein einziger Gratis-Fehler auch die bereits bezahlten Tiers.
        if kind == "free_limit" and not _is_free_ref(ref):
            continue
        if kind == "quota" and not (ref.startswith("opencode/") and not _is_free_ref(ref)):
            continue
        if kind == "variant":
            # provider.no-route: zuerst die ungültige Variante aus dem Preset
            # entfernen und es erneut probieren — NICHT sofort auf das Ausweich-
            # Modell eskalieren (kein Rollout doppelt: der sync-Dispatch wird
            # unten einmal für alle reparierten Tiers ausgelöst). Ohne Variante
            # im Preset gibt es nichts zu reparieren (idempotent).
            if var:
                note, changed = _strip_invalid_variant(tier, ref, var)
                swaps[tier] = note
                if changed:
                    variant_fixed.append(tier)
                    health["tiers"][tier] = {"model": ref, "ok": True, "failStreak": 0,
                                             "lastOkAt": _now_iso(),
                                             "repairedAt": _now_iso()}
                    health["models"][ref] = {"model": ref, "failStreak": 0,
                                             "quarantineUntil": "", "lastOkAt": _now_iso()}
            continue
        # Quelle der Wahrheit: persistierte Serie im Health-File (überlebt
        # Neustarts); STATE["fails"] bleibt nur RAM-Cache fürs Panel.
        streak = int(((health.get("models") or {}).get(label_cur) or {})
                     .get("failStreak") or 0) + 1
        streaks[tier] = streak
        tier_mem = dict(health["tiers"].get(tier) or {})
        model_mem = dict(health["models"].get(label_cur) or {})
        mem_kind = kind if kind in ("free_limit", "quota") else "outage"
        model_mem.update({"model": label_cur, "failStreak": streak,
                          "lastFailAt": _now_iso(), "lastFailureKind": mem_kind})
        tier_mem.update({"model": label_cur, "ok": False, "failStreak": streak,
                         "lastFailAt": model_mem["lastFailAt"],
                         "lastFailureKind": mem_kind,
                         "quarantineUntil": model_mem.get("quarantineUntil") or ""})
        health["tiers"][tier] = tier_mem
        health["models"][label_cur] = model_mem
        threshold = 1 if kind in ("free_limit", "quota") else \
            max(1, int(STATE.get("threshold") or THRESHOLD))
        if streak < threshold:
            continue
        fb = fallbacks.get(tier) or {}
        fb_ref = (fb.get("model") or "").strip()
        fb_var = (fb.get("variant") or "").strip()
        if not fb_ref:
            swaps[tier] = "skipped: no fallback — kein Ausfall-Modell hinterlegt"
            _log(f"{tier}: {kind}-Event erkannt, aber kein Ausfall-Modell hinterlegt")
            continue
        if not apply:
            swaps[tier] = f"bereit ({kind} erkannt, Auto-Failover aus): {fb_ref}"
            continue
        fb_label = fb_ref + (f"#{fb_var}" if fb_var else "")
        if fb_label == label_cur:
            swaps[tier] = (f"übersprungen — {tier} läuft bereits auf {fb_label} "
                           f"(kein weiterer Ausweichref)")
            tier_mem["failStreak"] = 0       # Serie nicht endlos fortzählen
            health["tiers"][tier] = tier_mem
            continue
        q_until = _quarantined_until(health, fb_label)
        if q_until:
            swaps[tier] = f"übersprungen — {fb_label} in Quarantäne bis {q_until}"
            continue
        try:
            res_swap = _swap_tier(tier, fb)
            if res_swap.get("unchanged"):
                swaps[tier] = f"übersprungen — {fb_label} ist bereits gesetzt (kein Commit)"
                tier_mem["failStreak"] = 0
                health["tiers"][tier] = tier_mem
                streaks[tier] = 0
                continue
            swaps[tier] = fb_label
            swapped_tiers.append(tier)
            streaks[tier] = 0
            new_mem = {"model": fb_label, "ok": True, "failStreak": 0,
                       "lastOkAt": _now_iso(), "swappedAt": _now_iso(),
                       "primaryModel": ref, "primaryVariant": var,
                       "paidReason": kind, "quarantineUntil": ""}
            if kind == "free_limit":
                ms = float(err.get("retryAfterMs") or 0)
                if ms > 0:
                    new_mem["paidUntil"] = _now() + ms / 1000.0
            health["tiers"][tier] = new_mem
            health["models"][fb_label] = {"model": fb_label, "failStreak": 0,
                                          "quarantineUntil": "", "lastOkAt": _now_iso()}
        except Exception as e:  # noqa: BLE001
            swaps[tier] = f"FEHLER: {e}"

    if swaps or streaks:
        try:
            write_health(health, old_sig)
        except Exception as e:  # noqa: BLE001
            _log(f"Gedächtnis (Event-Pfad) nicht schreibbar: {e}")
    if swapped_tiers:
        # EIN sync-Rollout für alle Wechsel dieses Events (nur Datei-Ersatz,
        # kein Container-Neustart — siehe _dispatch_rollout).
        _dispatch_rollout(swapped_tiers, "sync")
    if variant_fixed:
        # Varianten-Reparatur: genauso einmaliger sync (kein Neustart).
        _dispatch_rollout(variant_fixed, "sync")
    with LOCK:
        STATE["fails"].update(streaks)
        STATE["fallbacks"] = fallbacks
        STATE["current"] = {t: (preset.get(t) or {}).get("model") for t in TIERS}
        STATE["health"] = health
        paid = [_paid_until(t) for t in health["tiers"].values() if _paid_until(t) > 0]
        STATE["freeLimitUntil"] = min(paid) if paid else None
        STATE["events"] = ([{"at": time.time(), "kind": kind,
                             "session": session[:12], "swaps": swaps}]
                           + STATE["events"])[:10]
        EVENTS.append(rec)
    if swaps:
        _log(f"Event {etype} ({kind}, Session {session[:12] or '?'}): {swaps}")
    return {"kind": kind, "applied": apply, "swaps": swaps, "swapped": swapped_tiers,
            "variantFixed": variant_fixed, "streaks": streaks, "health": health}


def check_returns() -> dict:
    """Rückweg-Timer je Tier ausführen (vom Loop, max. alle RETURN_TICK s).

    Läuft ein Tier auf dem bezahlten Ausweich-Modell (free_limit-Swap) und
    tiers[tier].paidUntil (= now + retryAfterMs) ist erreicht, wechselt es
    automatisch auf das Primärmodell zurück — bewusst OHNE Modell-Probe: nach
    Ablauf von retryAfterMs ist das Gratis-Kontingent wieder offen, ein
    Zusatz-Turn würde nur kosten. Quota-Swaps haben keinen paidUntil-Eintrag
    und bleiben dauerhaft (kein Auto-Rückweg).
    """
    with LOCK:
        health = json.loads(json.dumps(
            STATE.get("health") or {"tiers": {}, "models": {}}))
    old_sig = _health_sig(health)
    returned, rolled = {}, []
    for tier in TIERS:
        tier_mem = dict((health.get("tiers") or {}).get(tier) or {})
        until = _paid_until(tier_mem)
        primary = str(tier_mem.get("primaryModel") or "").strip()
        if not until or not primary or _now() < until:
            continue
        prim_var = str(tier_mem.get("primaryVariant") or "")
        label = primary + (f"#{prim_var}" if prim_var else "")
        try:
            res_swap = _swap_tier(tier, {"model": primary, "variant": prim_var})
        except Exception as e:  # noqa: BLE001
            _log(f"{tier}: Rückweg auf {label} fehlgeschlagen: {e}")
            continue
        returned[tier] = label
        health["tiers"][tier] = {"model": label, "ok": True, "failStreak": 0,
                                 "lastOkAt": _now_iso(), "returnedAt": _now_iso()}
        health["models"][label] = {"model": label, "failStreak": 0,
                                   "quarantineUntil": "", "lastOkAt": _now_iso()}
        if res_swap.get("unchanged"):
            _log(f"{tier}: bereits auf {label} — kein Commit")
        else:
            rolled.append(tier)
            _log(f"{tier}: Rückweg auf {label} (paidUntil erreicht)")
    if returned:
        try:
            write_health(health, old_sig)
        except Exception as e:  # noqa: BLE001
            _log(f"Gedächtnis (Rückweg) nicht schreibbar: {e}")
        if rolled:
            _dispatch_rollout(rolled, "sync")
    with LOCK:
        paid = [_paid_until(t) for t in (health.get("tiers") or {}).values()
                if _paid_until(t) > 0]
        STATE["freeLimitUntil"] = min(paid) if paid else None
        if returned:
            STATE["health"] = health
            for tier in returned:
                STATE["fails"][tier] = 0
            STATE["events"] = ([{"at": time.time(), "returned": returned}]
                               + STATE["events"])[:10]
    return {"returned": returned}


# ── Modell-Gedächtnis (welches Modell macht gerade Probleme — und bis wann?) ───

def read_health() -> dict:
    """Gedächtnis lesen: {"tiers": {...}, "models": {...}}.

    tiers[tier]  = Zustand des aktuell an diesem Tier gesetzten Modells
    models[label] = Historie je Modell (Fehlschlagsserie, Quarantäne) — gilt
                    unabhängig davon, an welchem Tier das Modell gerade hängt.
    Fehlt/kaputt → leeres Gedächtnis (nie ein harter Fehler).
    """
    data = {}
    try:
        raw = github_api.get_file(HEALTH_PATH)
        if raw:
            data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        _log(f"health: {HEALTH_PATH} nicht lesbar ({e})")
        data = {}
    if not isinstance(data, dict):
        data = {}
    tiers = data.get("tiers") if isinstance(data.get("tiers"), dict) else {}
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    return {"tiers": dict(tiers), "models": dict(models)}


def _health_sig(health: dict) -> str:
    """Signatur ohne Zeitstempel-Rauschen: nur echte Zustandsänderungen zählen."""
    return json.dumps(health, sort_keys=True, ensure_ascii=False)


def write_health(health: dict, old_sig: str = "") -> bool:
    """Gedächtnis persistieren — nur wenn sich inhaltlich etwas geändert hat."""
    if _health_sig(health) == old_sig:
        return False
    text = json.dumps({"updatedAt": _now_iso(), **health}, indent=2, ensure_ascii=False) + "\n"
    res = github_api.put_files({HEALTH_PATH: text}, "hestia: Modell-Gedächtnis (Ausfallschutz)")
    return not res.get("unchanged")


def _quarantined_until(health: dict, label: str) -> str:
    """Bis wann ist dieses Modell gesperrt? "" = nicht gesperrt."""
    entry = (health.get("models") or {}).get(label) or {}
    until = str(entry.get("quarantineUntil") or "")
    return until if until and until > _now_iso() else ""


def quota_state() -> dict:
    """Ist das aktive OpenCode-Go-Konto erschöpft/rate-limited?

    Kontingent-Erschöpfung ist KEIN Modellausfall — ein Modellwechsel heilt sie
    nicht, er kostet nur einen Rollout. In diesem Fall hält der Ausfallschutz
    still und überlässt das Feld accounts.py (Kontowechsel).
    """
    try:
        import accounts
        st = accounts.state() or {}
    except Exception as e:  # noqa: BLE001
        return {"blocked": False, "reason": f"Quota-Check nicht möglich ({e})"}
    accts = [a for a in (st.get("accounts") or []) if isinstance(a, dict)]
    if not accts:
        return {"blocked": False, "reason": "keine Kontodaten"}
    active_id = st.get("active")
    active = next((a for a in accts if a.get("id") == active_id), None)
    if active is not None and accounts._exhausted(active):
        return {"blocked": True, "active": active_id,
                "reason": f"Konto {active_id or '?'} erschöpft/rate-limited"}
    if all(accounts._exhausted(a) for a in accts):
        return {"blocked": True, "active": active_id, "reason": "alle Konten erschöpft/rate-limited"}
    return {"blocked": False, "active": active_id, "reason": ""}


def probe(model_ref: str, variant: str = "", timeout: int = 0) -> dict:
    """Winzige Test-Session auf dem Modell. Erfolg = execution.succeeded.

    timeout=0 → das Timeout aus STATE ("timeoutSeconds" der SOT, sonst Env-Default).
    """
    if not model_ref:
        return {"ok": False, "detail": "kein Modell konfiguriert"}
    ok, info = catalog.usable(model_ref)
    if not ok:
        return {"ok": False, "detail": f"{info} (nicht im Katalog → sofortiger Ausfall)"}
    sstatus, sres = suggest._req("POST", f"{suggest.OC_SERVER_URL}/api/session",
                                 {"location": {"directory": suggest.OC_SUGGEST_DIR},
                                  "title": "Hestia Ausfall-Probe"})
    if sstatus not in (200, 201):
        return {"ok": False, "detail": f"Session-Anlegen fehlgeschlagen ({sstatus})"}
    sid = (sres.get("data") or sres).get("id")
    try:
        provider, _, mid = model_ref.partition("/")
        body = {"providerID": provider, "id": mid}
        if variant:
            body["variant"] = variant
        mst, _ = suggest._req("POST", f"{suggest.OC_SERVER_URL}/api/session/{sid}/model", {"model": body})
        if mst not in (200, 201, 204):
            return {"ok": False, "detail": f"Modell nicht verfügbar (switch {mst})"}
        # Prompt bewusst klein ("Antworte nur mit OK"). Das Session-API kennt
        # für /prompt (Stand heute) kein max_tokens-Feld — sobald die API eines
        # anbietet, gehört hier die Begrenzung hin, um Probe-Turns zu deckeln.
        suggest._req("POST", f"{suggest.OC_SERVER_URL}/api/session/{sid}/prompt",
                     {"text": "Antworte nur mit OK"})
        with LOCK:
            t_out = int(timeout or STATE.get("timeout") or PROBE_TIMEOUT)
        result = suggest._event_watch(sid, deadline=time.time() + t_out,
                                      stall_timeout=t_out)
        ok = result == "succeeded"
        return {"ok": ok, "detail": result}
    finally:
        suggest._delete_session(sid)


def run_probes(apply_backup: bool = False) -> dict:
    """Watchdog: alle Tier-Modelle prüfen; optional beim Ausfall auf das Backup.

    Regulär erkennt der Ereignis-Watcher Ausfälle live — dieser Lauf ist nur
    das Netz (höchstens 1×/h) bzw. der manuelle Panel-Check
    (/api/failover/check). Schwelle und Timeout kommen aus der SOT/STATE,
    die Fehlschlagserie aus dem persistierten Modell-Gedächtnis.
    """
    cfg = github_api.get_overrides()["config"]
    preset = (cfg.get("presets") or {}).get(os.environ.get("HESTIA_PRESET_PROVIDER", "hestia"), {})
    fallbacks = read_fallbacks()
    health = read_health()
    old_sig = _health_sig(health)
    with LOCK:
        quarantine_h = int(STATE.get("quarantineHours") or QUARANTINE_HOURS)
        threshold = int(STATE.get("threshold") or THRESHOLD)
        ptimeout = int(STATE.get("timeout") or PROBE_TIMEOUT)
    quota = quota_state()
    results, swaps, state_updates, swapped_tiers = {}, {}, {}, []
    for tier in TIERS:
        entry = preset.get(tier) or {}
        ref = (entry.get("model") or "").strip()
        var = (entry.get("variant") or "").strip()
        if not ref:
            continue
        res = probe(ref, var, timeout=ptimeout)
        label_cur = ref + (f"#{var}" if var else "")
        results[tier] = {"model": label_cur, **res}
        # Quelle der Wahrheit: persistierte Serie im Health-File (überlebt
        # Neustarts) — STATE["fails"] dient nur als RAM-Cache fürs Panel.
        fails = 0 if res["ok"] else (
            int((health["models"].get(label_cur) or {}).get("failStreak") or 0) + 1)
        state_updates[tier] = fails

        # ── Gedächtnis pflegen ────────────────────────────────────────────
        tier_mem = dict(health["tiers"].get(tier) or {})
        model_mem = dict(health["models"].get(label_cur) or {})
        # Swap-Arrangement (paidUntil/primaryModel/…) im Watchdog-Lauf bewahren:
        # eine erfolgreiche Probe auf dem ausgefallenen Modell löscht sonst den
        # Rückweg-Timer, und der Rückweg fände nie statt.
        swap_extras = {k: tier_mem[k] for k in
                       ("paidUntil", "primaryModel", "primaryVariant", "paidReason",
                        "swappedAt") if k in tier_mem}
        if res["ok"]:
            if (model_mem.get("failStreak") or model_mem.get("quarantineUntil")
                    or tier_mem.get("failStreak") or tier_mem.get("lastFailureKind")):
                tier_mem = {"model": label_cur, "ok": True, "failStreak": 0,
                            "lastOkAt": _now_iso(), **swap_extras}
                model_mem = {"model": label_cur, "failStreak": 0,
                             "quarantineUntil": "", "lastOkAt": _now_iso()}
            else:
                # gesund und ohne Vorgeschichte: nichts zu schreiben
                tier_mem = {"model": label_cur, "ok": True, "failStreak": 0}
                if swap_extras:
                    tier_mem.update(swap_extras)
                model_mem = {"model": label_cur, "failStreak": 0, "quarantineUntil": ""}
        else:
            kind = "quota" if quota["blocked"] else "outage"
            streak = int(model_mem.get("failStreak") or 0) + 1
            model_mem = dict(model_mem)
            model_mem.update({"model": label_cur, "failStreak": streak,
                              "lastFailAt": _now_iso(), "lastFailureKind": kind})
            if kind == "outage" and streak >= threshold and quarantine_h > 0:
                model_mem["quarantineUntil"] = _now_iso(time.time() + quarantine_h * 3600)
            tier_mem = {"model": label_cur, "ok": False, "failStreak": streak,
                        "lastFailAt": model_mem["lastFailAt"], "lastFailureKind": kind,
                        "quarantineUntil": model_mem.get("quarantineUntil") or ""}
            if swap_extras:
                tier_mem.update(swap_extras)
        health["tiers"][tier] = tier_mem
        health["models"][label_cur] = model_mem

        # ── Wechselentscheidung ───────────────────────────────────────────
        fb = fallbacks.get(tier) or {}
        fb_ref = (fb.get("model") or "").strip()
        fb_var = (fb.get("variant") or "").strip()
        fb_label = fb_ref + (f"#{fb_var}" if fb_var else "")
        if res["ok"] or fails < threshold:
            continue
        if not fb_ref:
            # Kein stiller Skip: der Ausfall muss im Panel sichtbar bleiben.
            swaps[tier] = "skipped: no fallback — kein Ausfall-Modell hinterlegt"
            _log(f"{tier}: Ausfall erkannt, aber kein Ausfall-Modell hinterlegt")
            continue
        if not (apply_backup or _auto_enabled()):
            swaps[tier] = f"bereit (Ausfall erkannt, Auto-Failover aus): {fb_label}"
            continue
        if quota["blocked"]:
            swaps[tier] = (f"übersprungen — {quota['reason']}: Kontingent, kein Modellausfall "
                           f"(Kontowechsel ist accounts.py)")
            continue
        if fb_ref == ref and fb_var == var:
            swaps[tier] = f"übersprungen — {tier} läuft bereits auf {fb_label}"
            state_updates[tier] = 0
            continue
        q_until = _quarantined_until(health, fb_label)
        if q_until:
            swaps[tier] = f"übersprungen — {fb_label} in Quarantäne bis {q_until}"
            continue
        fb_res = probe(fb_ref, fb_var, timeout=ptimeout)
        if not fb_res["ok"]:
            swaps[tier] = (f"übersprungen — Ausweich-Modell {fb_label} antwortet ebenfalls "
                           f"nicht ({fb_res['detail']})")
            continue
        try:
            res_swap = _swap_tier(tier, fb)
            state_updates[tier] = 0
            if res_swap.get("unchanged"):
                swaps[tier] = f"übersprungen — {fb_label} ist bereits gesetzt (kein Commit)"
            else:
                swaps[tier] = fb_label
                swapped_tiers.append(tier)
            health["tiers"][tier] = {"model": fb_label, "ok": True, "failStreak": 0,
                                     "lastOkAt": _now_iso(), "swappedAt": _now_iso()}
            health["models"][fb_label] = {"model": fb_label, "failStreak": 0,
                                          "quarantineUntil": "", "lastOkAt": _now_iso()}
        except Exception as e:  # noqa: BLE001
            swaps[tier] = f"FEHLER: {e}"

    health_committed = False
    try:
        health_committed = write_health(health, old_sig)
    except Exception as e:  # noqa: BLE001
        _log(f"Gedächtnis nicht schreibbar: {e}")
    if swapped_tiers:
        # EIN Rollout für alle Wechsel dieses Laufs (vorher: ein Dispatch je Tier
        # → zwei parallele Rollouts mit doppelten Container-Neustarts).
        _dispatch_rollout(swapped_tiers, "sync")
    with LOCK:
        STATE["fails"].update(state_updates)
        STATE["fallbacks"] = fallbacks
        STATE["current"] = {t: (preset.get(t) or {}).get("model") for t in TIERS}
        STATE["lastRun"] = time.time()
        STATE["lastResults"] = results
        STATE["health"] = health
        STATE["quota"] = quota
        if swaps:
            STATE["events"] = ([{"at": time.time(), "swaps": swaps}] + STATE["events"])[:10]
            _log(f"Ausweichungen: {swaps}")
    return {"results": results, "swaps": swaps, "health": health, "quota": quota,
            "healthCommitted": health_committed, "rolledOut": swapped_tiers}


def _dispatch_rollout(tiers: list, mode: str = "sync") -> bool:
    """Rollout genau einmal anstoßen (Push auf die Overrides feuert ihn nicht,
    weil der Commit idempotent-geschützt ggf. gar keine Änderung enthält).

    mode="sync": sync.sh/sync.yml — schreibt NUR die Overrides-Datei in die
    Container (kein Neustart; der Plugin-Fingerprint lädt sie beim nächsten
    Prompt neu) → Standard für Tier-Wechsel.
    mode="restart": klassisches rollout.yml (Container-Restart) — für echte
    Deployments, nicht für einen reinen Modellwechsel.
    """
    workflow = SYNC_WORKFLOW if mode == "sync" else ROLLOUT_WORKFLOW
    try:
        github_api.dispatch_workflow(workflow)
        _log(f"Rollout ({mode}, {workflow}) ausgelöst ({', '.join(tiers)})")
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"Rollout-Dispatch fehlgeschlagen ({workflow}): {e}")
        return False


def _highest_variant(model_ref: str) -> str:
    """Höchste Effort-Variante eines Modells laut Live-Katalog ("" = keine).

    Prüft wie suggest._resolve_tier_model gegen catalog.usable: eine Variante
    wird nur vergeben, wenn sie in den Live-Varianten des Modells liegt —
    sonst "" (keine). So vermieden: 'xhigh' für ein Modell, das nur
    low/high/max kennt (→ provider.no-route, wertete der Ausfallschutz
    fälschlich als Ausfall).
    """
    ok, res = catalog.usable(model_ref)
    if not ok or not isinstance(res, dict):
        return ""
    ranked = sorted([v for v in (res.get("variants") or []) if v in VARIANT_RANK],
                    key=lambda v: VARIANT_RANK[v])
    return ranked[-1] if ranked else ""


def _derived_agents_json(new_preset: dict):
    """Primary-Agents (build/plan) an das neue @medium anpassen.

    Spiegelt server.effective_agents()/agents_to_display(): build = @medium
    (Standardvariante), plan = @medium (höchste Variante). Manuell gewählte
    Agenten (source == "manual") bleiben unangetastet. Rückgabe: neuer JSON-Text
    oder None, wenn keine Änderung / kein @medium vorhanden.

    Ohne diese Synchronisierung blieben build/plan nach einem Auto-Failover auf
    dem (ausgefallenen) Gratis-Modell des alten @medium hängen — der Failover
    wirkte dann nur für die Subagent-Tiers, nicht für die Chat-Agenten.
    """
    medium = (new_preset or {}).get("medium") or {}
    med_ref = (medium.get("model") or "").strip()
    if not med_ref:
        return None
    try:
        raw = github_api.get_file(AGENTS_PATH)
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    out, changed = {}, False
    for agent in PRIMARY_AGENTS:
        entry = data.get(agent) or {}
        model = (entry.get("model") or "").strip()
        source = entry.get("source")
        manual = (source == "manual") or (bool(model) and not source)
        if manual:
            out[agent] = entry
            continue
        variant = (medium.get("variant") or "") if agent == "build" else _highest_variant(med_ref)
        new_entry = {"model": med_ref, "variant": variant or "", "source": "derived"}
        out[agent] = new_entry
        if (entry.get("model") != new_entry["model"]
                or (entry.get("variant") or "") != new_entry["variant"]
                or source != "derived"):
            changed = True
    if not changed:
        return None
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def _swap_tier(tier: str, backup: dict):
    """Ausfall-Modell als Tier-Modell schreiben (nur Commit — Rollout macht der Aufrufer).

    Schreibt nichts, wenn das Tier inhaltlich schon auf dem Ausweich-Modell
    steht (github_api.put_files meldet das als "unchanged"): ein leerer Commit
    löste sonst einen Rollout mit Container-Neustarts ohne jede Änderung aus.
    """
    # aktuelle Werte frisch lesen und nur das betroffene Tier ersetzen
    cfg = github_api.get_overrides()["config"]
    preset = (cfg.get("presets") or {}).get(os.environ.get("HESTIA_PRESET_PROVIDER", "hestia"), {})
    new_preset = dict(preset)
    entry = dict(new_preset.get(tier) or {})
    entry["model"] = backup["model"]
    if backup.get("variant"):
        entry["variant"] = backup["variant"]
    else:
        entry.pop("variant", None)
    new_preset[tier] = entry
    cfg = dict(cfg)
    cfg["presets"] = dict(cfg.get("presets") or {})
    cfg["presets"][os.environ.get("HESTIA_PRESET_PROVIDER", "hestia")] = new_preset

    import hjsonc
    jsonc = hjsonc.serialize(cfg)
    label = backup["model"] + (f"#{backup['variant']}" if backup.get("variant") else "")
    # Primary-Agents (hergeleitet aus @medium) im selben Commit mitziehen.
    files = {github_api.OVERRIDES_PATH: jsonc}
    agents_json = _derived_agents_json(new_preset)
    if agents_json:
        files[AGENTS_PATH] = agents_json
    res = github_api.put_files(files, f"hestia: Ausfallschutz — {tier} auf {label} gewechselt")
    if res.get("unchanged"):
        _log(f"{tier}: schon auf {label} — kein Commit, kein Rollout")
        return res
    _log(f"{tier} -> {label} (Commit {str(res.get('sha', ''))[:8]})"
         + (" · Primary-Agents angepasst" if agents_json else ""))
    return res


# Probe-Thread-Verwaltung: Live-Start/-Stopp bei Settings-Änderung.
_LOOP_THREAD = None
_STOP = threading.Event()
_EVENT_THREAD = None          # Ereignis-Watcher (SSE), startet einmalig in start()
_EVENT_STOP = threading.Event()


def _auto_enabled() -> bool:
    with LOCK:
        return bool(STATE["autoFailover"])


def _apply_loop_state(enabled: bool):
    """Probe-Thread live starten/stoppen (ohne Container-Neustart)."""
    global _LOOP_THREAD, _STOP
    if not enabled:
        _STOP.set()
        _log("Auto-Ausfallschutz deaktiviert (Probe-Thread gestoppt)")
        return
    if _LOOP_THREAD and _LOOP_THREAD.is_alive():
        return
    _STOP = threading.Event()
    _LOOP_THREAD = threading.Thread(target=loop, args=(_STOP,), daemon=True)
    _LOOP_THREAD.start()


def loop(stop_event: threading.Event):
    """Haupt-Thread der Automatik.

    Der Ereignis-Watcher erkennt Ausfälle live; dieser Thread hält nur noch
    den Rückweg-Timer (check_returns, max. alle RETURN_TICK s) und den
    Watchdog als Netz: Proben laufen höchstens 1×/h — sofort beim Übergang auf
    "Ereignis-Stream getrennt", ansonsten im Intervall-Takt der SOT (kleinere
    Intervalle als 1 h werden für den Watchdog auf 1 h hochgesetzt).
    """
    with LOCK:
        _log(f"Auto-Ausfallschutz aktiv (Intervall {STATE['interval']}s, "
             f"Schwelle {STATE['threshold']}, Timeout {STATE['timeout']}s, "
             f"Quarantäne {STATE['quarantineHours']}h, Watchdog max {WATCHDOG_MIN // 3600}×/h)")
    last_dog = 0.0
    was_connected = None
    while not stop_event.is_set():
        with LOCK:
            interval = max(int(STATE["interval"]), RETURN_TICK)
            connected = bool((STATE.get("eventStream") or {}).get("connected"))
        # Rückweg-Timer zuerst (reiner Config-Wechsel, keine Proben).
        try:
            check_returns()
        except Exception as e:  # noqa: BLE001
            _log(f"Rückweg-Check fehlgeschlagen: {e}")
        dog_period = max(WATCHDOG_MIN, interval)
        # Netzfunktion: geht der Ereignis-Stream gerade verloren, sofort proben
        # (nicht erst nach Ablauf der laufenden Stunde) — danach wieder im Takt.
        if was_connected and not connected:
            last_dog = 0.0
        was_connected = connected
        if _auto_enabled() and _now() - last_dog >= dog_period:
            try:
                run_probes()
            except Exception as e:  # noqa: BLE001
                _log(f"Watchdog fehlgeschlagen: {e}")
            last_dog = _now()
        # Intervall/Takt je Iteration neu lesen → Panel-Änderung wirkt ohne Neustart.
        stop_event.wait(min(RETURN_TICK, max(5, interval)))
    _log("Auto-Ausfallschutz deaktiviert — Probe-Thread beendet")


def start():
    global _EVENT_THREAD, _EVENT_STOP
    try:
        settings = read_settings()
        with LOCK:
            STATE["autoFailover"] = settings["enabled"]
            STATE["threshold"] = settings["threshold"]
            STATE["interval"] = settings["intervalSeconds"]
            STATE["timeout"] = settings["timeoutSeconds"]
            STATE["quarantineHours"] = settings["quarantineHours"]
            STATE["fallbacks"] = read_fallbacks()
    except Exception:
        pass
    try:
        with LOCK:
            STATE["health"] = read_health()
            # paidUntil liegt als epoch-Zeitstempel im Health-File — der
            # Rückweg-Timer überlebt damit auch einen Hestia-Neustart.
            paid = [_paid_until(t) for t in STATE["health"].get("tiers", {}).values()
                    if _paid_until(t) > 0]
            STATE["freeLimitUntil"] = min(paid) if paid else None
    except Exception:
        pass
    # Ereignis-Watcher läuft unabhängig von der Auto-Umschaltung, weil der
    # Verbindungszustand (eventStream) immer fürs Panel sichtbar sein soll;
    # Swaps nimmt er aber nur bei enabled=True vor (handle_execution_failed).
    if _EVENT_THREAD and _EVENT_THREAD.is_alive():
        _log("Event-Watcher läuft bereits — kein zweiter Thread")
    else:
        _EVENT_STOP = threading.Event()
        _EVENT_THREAD = threading.Thread(target=event_loop, args=(_EVENT_STOP,),
                                         daemon=True, name="hestia-event-watcher")
        _EVENT_THREAD.start()
    if _auto_enabled():
        _apply_loop_state(True)
    else:
        _log("Auto-Ausfallschutz inaktiv (SOT enabled=false) — Proben nur manuell")
