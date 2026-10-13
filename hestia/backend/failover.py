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
PROBE_TIMEOUT = int(os.environ.get("HESTIA_PROBE_TIMEOUT", "35"))
THRESHOLD = int(os.environ.get("HESTIA_FAIL_THRESHOLD", "2"))
INTERVAL = int(os.environ.get("HESTIA_PROBE_INTERVAL", "900"))
AUTO = os.environ.get("HESTIA_AUTO_FAILOVER", "0") == "1"

STATE = {
    "autoFailover": AUTO,
    "interval": INTERVAL,
    "threshold": THRESHOLD,
    "fallbacks": {},
    "current": {},
    "fails": {},
    "lastRun": None,
    "lastResults": {},
    "events": [],
}
LOCK = threading.Lock()


def _log(msg):
    print(f"[hestia-failover] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


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
    _log(f"Auto-Ausfallschutz aktiv (Intervall {INTERVAL}s, Schwelle {THRESHOLD})")
    while True:
        try:
            run_probes()
        except Exception as e:  # noqa: BLE001
            _log(f"Probe fehlgeschlagen: {e}")
        time.sleep(INTERVAL)


def start():
    try:
        with LOCK:
            STATE["fallbacks"] = read_fallbacks()
    except Exception:
        pass
    if AUTO:
        threading.Thread(target=loop, daemon=True).start()
