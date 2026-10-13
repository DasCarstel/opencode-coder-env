#!/usr/bin/env python3
"""hermes.py — Hermes-Agent (Nous Research) aus Hestia steuern.

Quelle der Wahrheit ist .opencode/hestia-hermes.jsonc (SOT, wie die
Accounts-Datei via GitHub Contents API gelesen). apply() validiert die Modelle
gegen den Live-Katalog, committet die SOT und dispatcht apply-hermes.yml —
der Workflow spielt sie per SSH in den Container "hermes" ein
(hestia/ci/apply-hermes.sh). Das Panel selbst fasst weder Container noch Keys
an; der Key-Pool bleibt Aufgabe des Runner-Skripts (OpenBao).
"""
import json
import os
import time
import urllib.error
import urllib.request

import github_api
import hjsonc

CONFIG_PATH = os.environ.get("HESTIA_HERMES_PATH", ".opencode/hestia-hermes.jsonc")
HEALTH_URL = os.environ.get("HERMES_HEALTH_URL", "http://hermes:9119/api/health")
WORKFLOW = os.environ.get("HESTIA_HERMES_WORKFLOW", "apply-hermes.yml")
CONFIG_TTL = 60      # Sekunden Cache für die SOT (Panel pollt jede Minute)
HTTP_TIMEOUT = 5
# Erlaubte Provider (Komma-getrennt) — identisch zum Tier-System.
ALLOWED_PROVIDERS = [p.strip() for p in
                     os.environ.get("HESTIA_PROVIDERS", "opencode-go,opencode").split(",") if p.strip()]

_CACHE = {"at": 0.0, "cfg": None, "error": None}


def _log(msg):
    print(f"[hestia-hermes] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _default_config() -> dict:
    return {
        "enabled": True,
        "primary": {"provider": "", "model": ""},
        "fallback": [],
        "pool": {"followActive": False, "onlyActiveWhenExpiring": False},
        "restart": True,
    }


def config(force: bool = False) -> dict:
    """SOT (JSONC) lesen; 60 s Cache. Fehler → Default + _error."""
    now = time.time()
    if not force and _CACHE["cfg"] is not None and now - _CACHE["at"] < CONFIG_TTL:
        return _CACHE["cfg"]
    cfg, err = {}, None
    try:
        raw = github_api.get_file(CONFIG_PATH)
        if raw:
            cfg = hjsonc.parse(raw) or {}
        else:
            err = f"{CONFIG_PATH} nicht lesbar (fehlt oder leer)"
    except Exception as e:  # noqa: BLE001
        err = str(e)
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["_error"] = err
    _CACHE.update(at=now, cfg=cfg, error=err)
    return cfg


def _public(cfg: dict) -> dict:
    """SOT ohne interne Felder (für Panel + apply-Vergleich)."""
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "primary": cfg.get("primary") or {},
        "fallback": cfg.get("fallback") or [],
        "pool": {"followActive": bool((cfg.get("pool") or {}).get("followActive")),
                 "onlyActiveWhenExpiring": bool(
                     (cfg.get("pool") or {}).get("onlyActiveWhenExpiring"))},
        "restart": bool(cfg.get("restart", True)),
    }


def health() -> dict:
    """Hermes-Health abfragen. Der Default-urllib-UA wird von Hermes teils
    abgewiesen → eigener User-Agent (wie accounts.py)."""
    req = urllib.request.Request(
        HEALTH_URL,
        headers={"Accept": "application/json",
                 "User-Agent": "Hestia/1.0 (hermes-control)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"reachable": False, "error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": str(e)}
    return {"reachable": True, "ok": bool(payload.get("ok")),
            "version": payload.get("version"), "error": None}


def state() -> dict:
    """Panel-Abfrage: SOT + Health + Ziel-Workflow."""
    cfg = _public(config())
    h = health()
    return {
        "enabled": cfg["enabled"],
        "reachable": h.get("reachable", False),
        "health": h,
        "config": cfg,
        "configError": _CACHE.get("error"),
        "workflow": WORKFLOW,
        "path": CONFIG_PATH,
        "providers": ALLOWED_PROVIDERS,
    }


class ApplyError(Exception):
    pass


def _validate(entry: dict, label: str) -> dict:
    """Modell-Eintrag (provider/model) gegen Katalog + Provider-Allowlist prüfen."""
    provider = str((entry or {}).get("provider") or "").strip()
    model = str((entry or {}).get("model") or "").strip()
    if not provider or not model:
        raise ApplyError(f"{label}: provider und model sind Pflicht")
    ref = f"{provider}/{model}"
    if provider not in ALLOWED_PROVIDERS:
        raise ApplyError(
            f"{label}: Provider '{provider}' nicht erlaubt (erlaubt: {', '.join(ALLOWED_PROVIDERS)})"
        )
    import catalog
    ok, res = catalog.usable(ref)
    if not ok:
        raise ApplyError(f"{label}: {res}")
    return {"provider": provider, "model": model}


def apply(payload: dict) -> dict:
    """SOT aus dem Panel schreiben und den Apply-Workflow dispatchen.

    Unveränderter Inhalt → kein Commit (nur Dispatch, damit ein Klick nach
    einem zwischenzeitlichen Fehlschlag erneut wirkt).
    """
    primary_in = payload.get("primary") or {}
    primary = _validate(primary_in, "primary")

    fb_out = []
    for i, f in enumerate(payload.get("fallback") or [], start=1):
        if not isinstance(f, dict) or (not f.get("model") and not f.get("provider")):
            continue                      # "—" = kein Fallback → überspringen
        fb_out.append(_validate(f, f"fallback[{i}]"))

    pool_in = payload.get("pool") or {}
    current_pool = (config(force=True).get("pool") or {})
    new_cfg = {
        "enabled": bool(payload.get("enabled", True)),
        "primary": primary,
        "fallback": fb_out,
        # onlyActiveWhenExpiring aus der aktuellen SOT übernehmen (das Panel
        # schickt es noch nicht mit) — sonst würde ein apply() den Schalter löschen.
        "pool": {"followActive": bool(pool_in.get("followActive")),
                 "onlyActiveWhenExpiring": bool(pool_in.get(
                     "onlyActiveWhenExpiring",
                     (current_pool or {}).get("onlyActiveWhenExpiring")))},
        "restart": bool(payload.get("restart", True)),
    }
    new_text = json.dumps(new_cfg, indent=2, ensure_ascii=False) + "\n"

    current = config()   # gerade frisch gelesen (Cache)
    changed = _public(current) != new_cfg
    commit = None
    if changed:
        desc = ", ".join(f"{f['provider']}/{f['model']}" for f in fb_out) or "—"
        commit = github_api.put_files({CONFIG_PATH: new_text}, (
            "hestia: Hermes-Update\n\n"
            f"primary={primary['provider']}/{primary['model']}\n"
            f"fallback={desc}\n"
            f"pool.followActive={new_cfg['pool']['followActive']}"
        ))
        _log(f"Hermes-SOT aktualisiert: {primary['provider']}/{primary['model']} | fallback={desc}")

    try:
        dispatched = github_api.dispatch_workflow(WORKFLOW, inputs={
            # single=true → apply-hermes.sh schreibt NUR den Key des aktiven
            # Abos in den Pool (die _2/_3-Zeilen verschwinden).
            "single": "true" if (new_cfg["pool"].get("onlyActiveWhenExpiring")) else "false",
        })
    except Exception as e:  # noqa: BLE001
        _log(f"Hermes-Dispatch fehlgeschlagen: {e}")
        dispatched = {"dispatched": False, "error": str(e)}

    config(force=True)  # SOT ist jetzt im Repo — Cache für das Panel auffrischen
    return {"applied": True, "unchanged": not changed,
            "commit": commit, "dispatched": dispatched, "config": new_cfg}
