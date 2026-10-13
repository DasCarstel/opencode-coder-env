#!/usr/bin/env python3
"""failover.py — Ausfallschutz für die Tier-Modelle.

Prüft die aktiven Tier-Modelle und weicht bei Ausfall auf das hinterlegte
bezahlte Modell aus (Datei .opencode/hestia-fallbacks.json, vom Panel gepflegt):

    {"fast":   {"model": "opencode/<gratis>",   "variant": ""},
     "medium": {"model": "opencode/<gratis>",   "variant": ""}}

Ablauf:
  - Probe: winzige Session je Tier-Modell ("Antworte nur mit OK") über den
    OpenCode-Server; Erfolg = session.execution.succeeded innerhalb des Fensters.
  - Nach HESTIA_FAIL_THRESHOLD Fehlschlägen in Folge UND wenn ein Ausfall-Modell
    hinterlegt ist, wird dieses als Tier-Modell geschrieben (Commit + Rollout) —
    automatisch nur wenn HESTIA_AUTO_FAILOVER=1, sonst per Klick im Panel.

Die Laufzeit-Parameter (enabled/threshold/Intervall/Timeout) liegen in der SOT
.opencode/hestia-failover.jsonc und werden über POST /api/failover/settings vom
Panel geschrieben; fehlt die Datei, gelten die HESTIA_*-Defaults.
"""
import json
import os
import threading
import time

import catalog
import github_api
import suggest

TIERS = ("fast", "medium", "heavy")
FALLBACKS_PATH = os.environ.get("HESTIA_FALLBACKS_PATH", ".opencode/hestia-fallbacks.json")
FAILOVER_SETTINGS_PATH = os.environ.get(
    "HESTIA_FAILOVER_SETTINGS_PATH", ".opencode/hestia-failover.jsonc"
)
PROBE_TIMEOUT = int(os.environ.get("HESTIA_PROBE_TIMEOUT", "35"))
THRESHOLD = int(os.environ.get("HESTIA_FAIL_THRESHOLD", "2"))
INTERVAL = int(os.environ.get("HESTIA_PROBE_INTERVAL", "900"))
AUTO = os.environ.get("HESTIA_AUTO_FAILOVER", "0") == "1"

# Grenzen für die Panel-Eingaben (POST /api/failover/settings)
THRESHOLD_RANGE = (1, 10)
INTERVAL_RANGE = (60, 86400)
TIMEOUT_RANGE = (5, 300)
SETTINGS_KEYS = ("enabled", "threshold", "intervalSeconds", "timeoutSeconds")


def default_settings() -> dict:
    """Startwerte = heutige Env-Defaults (Fallback, falls die SOT fehlt)."""
    return {
        "enabled": bool(AUTO),
        "threshold": THRESHOLD,
        "intervalSeconds": INTERVAL,
        "timeoutSeconds": PROBE_TIMEOUT,
    }


STATE = {
    "autoFailover": AUTO,
    "interval": INTERVAL,
    "threshold": THRESHOLD,
    "timeout": PROBE_TIMEOUT,
    "fallbacks": {},
    "current": {},
    "fails": {},
    "lastRun": None,
    "lastResults": {},
    "events": [],
}
LOCK = threading.Lock()


class SettingsError(Exception):
    """Ungültige Nutzereingabe für die Ausfallschutz-Einstellungen."""


def _log(msg):
    print(f"[hestia-failover] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


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
    Panel immer eine plausible Konfiguration sieht.
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

    Wirkt sofort: der Hintergrund-Thread liest das Intervall je Iteration neu.
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
    }

    header = (
        "// Ausfallschutz-Einstellungen (SOT des Hestia-Panels).\n"
        "// Geschrieben über POST /api/failover/settings, gelesen von hestia/backend/failover.py.\n"
    )
    text = header + json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    commit = github_api.put_files(
        {FAILOVER_SETTINGS_PATH: text}, "hestia: Ausfallschutz-Einstellungen aktualisiert")

    with LOCK:
        STATE["autoFailover"] = settings["enabled"]
        STATE["threshold"] = settings["threshold"]
        STATE["interval"] = settings["intervalSeconds"]
        STATE["timeout"] = settings["timeoutSeconds"]
    _log(
        f"settings: enabled={settings['enabled']} threshold={settings['threshold']} "
        f"intervall={settings['intervalSeconds']}s timeout={settings['timeoutSeconds']}s "
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


def probe(model_ref: str, variant: str = "") -> dict:
    """Winzige Test-Session auf dem Modell. Erfolg = execution.succeeded."""
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
        suggest._req("POST", f"{suggest.OC_SERVER_URL}/api/session/{sid}/prompt",
                     {"text": "Antworte nur mit OK"})
        result = suggest._event_watch(sid, deadline=time.time() + PROBE_TIMEOUT,
                                      stall_timeout=PROBE_TIMEOUT)
        ok = result == "succeeded"
        return {"ok": ok, "detail": result}
    finally:
        suggest._delete_session(sid)


def run_probes(apply_backup: bool = False) -> dict:
    """Alle Tier-Modelle prüfen; optional beim Ausfall auf das Backup wechseln."""
    cfg = github_api.get_overrides()["config"]
    preset = (cfg.get("presets") or {}).get(os.environ.get("HESTIA_PRESET_PROVIDER", "hestia"), {})
    fallbacks = read_fallbacks()
    results, swaps, state_updates = {}, {}, {}
    for tier in TIERS:
        entry = preset.get(tier) or {}
        ref = (entry.get("model") or "").strip()
        var = (entry.get("variant") or "").strip()
        if not ref:
            continue
        res = probe(ref, var)
        results[tier] = {"model": ref + (f"#{var}" if var else ""), **res}
        fails = 0 if res["ok"] else (STATE["fails"].get(tier, 0) + 1)
        state_updates[tier] = fails
        fb = fallbacks.get(tier) or {}
        if (not res["ok"] and fb.get("model") and fails >= THRESHOLD):
            label = fb["model"] + (f"#{fb['variant']}" if fb.get("variant") else "")
            if apply_backup or AUTO:
                try:
                    _swap_tier(tier, fb)
                    swaps[tier] = label
                    state_updates[tier] = 0
                except Exception as e:  # noqa: BLE001
                    swaps[tier] = f"FEHLER: {e}"
            else:
                swaps[tier] = f"bereit (Ausfall erkannt, Auto-Failover aus): {label}"
    with LOCK:
        STATE["fails"].update(state_updates)
        STATE["fallbacks"] = fallbacks
        STATE["current"] = {t: (preset.get(t) or {}).get("model") for t in TIERS}
        STATE["lastRun"] = time.time()
        STATE["lastResults"] = results
        if swaps:
            STATE["events"] = ([{"at": time.time(), "swaps": swaps}] + STATE["events"])[:10]
            _log(f"Ausweichungen: {swaps}")
    return {"results": results, "swaps": swaps}


def _swap_tier(tier: str, backup: dict):
    """Ausfall-Modell als Tier-Modell schreiben (Commit + Rollout)."""
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
    res = github_api.put_files({github_api.OVERRIDES_PATH: jsonc},
                               f"hestia: Ausfallschutz — {tier} auf {label} gewechselt")
    github_api.dispatch_workflow(os.environ.get("ROLLOUT_WORKFLOW", "rollout.yml"))
    _log(f"{tier} -> {label} (Commit {res.get('sha', '')[:8]})")
    return res


def loop():
    with LOCK:
        _log(f"Auto-Ausfallschutz aktiv (Intervall {STATE['interval']}s, "
             f"Schwelle {STATE['threshold']}, Timeout {STATE['timeout']}s)")
    while True:
        try:
            run_probes()
        except Exception as e:  # noqa: BLE001
            _log(f"Probe fehlgeschlagen: {e}")
        # Intervall je Iteration neu lesen → Panel-Änderung wirkt ohne Neustart.
        with LOCK:
            wait = int(STATE["interval"])
        time.sleep(wait)


def start():
    try:
        settings = read_settings()
        with LOCK:
            STATE["autoFailover"] = settings["enabled"]
            STATE["threshold"] = settings["threshold"]
            STATE["interval"] = settings["intervalSeconds"]
            STATE["timeout"] = settings["timeoutSeconds"]
            STATE["fallbacks"] = read_fallbacks()
    except Exception:
        pass
    with LOCK:
        auto = bool(STATE["autoFailover"])
    if auto:
        threading.Thread(target=loop, daemon=True).start()
