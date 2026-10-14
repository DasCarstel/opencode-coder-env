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
        # API-Server von Hermes (ENV-Variablen in /opt/data/.env). API_SERVER_KEY
        # ist ein Secret und wird bewusst NICHT hier gepflegt.
        "api_server": {"enabled": True, "port": 8642, "host": "0.0.0.0"},
        "restart": True,
        # Weitere Hermes-Profile des Multiplexers (je eigene config.yaml/.env
        # unter /opt/data/profiles/<name>/). Leeres Dict = nur das Default-Profil.
        "profiles": {},
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
        "api_server": _api_server_public(cfg.get("api_server")),
        "restart": bool(cfg.get("restart", True)),
        "profiles": _profiles_public(cfg.get("profiles") or {}, cfg),
    }


def _api_server_public(section) -> dict:
    """api_server normalisieren; fehlende/ungueltige Felder → Default.

    Aeltere SOT-Dateien ohne "api_server" bleiben so abwaertskompatibel.
    """
    d = _default_config()["api_server"]
    src = section if isinstance(section, dict) else {}
    port = src.get("port", d["port"])
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = d["port"]
    if not 1 <= port <= 65535:
        port = d["port"]
    host = str(src.get("host", d["host"]) or "").strip()
    return {"enabled": bool(src.get("enabled", d["enabled"])),
            "port": port,
            "host": host or d["host"]}


def _validate_api_server(section) -> dict:
    """api_server aus dem Panel validieren (kein Apply bei Unsinn)."""
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ApplyError("api_server: muss ein Objekt sein "
                         "(enabled/port/host)")
    out = {}
    if "enabled" in section:
        if not isinstance(section["enabled"], bool):
            raise ApplyError("api_server.enabled: muss true oder false sein")
        out["enabled"] = section["enabled"]
    if "port" in section:
        port = section["port"]
        if isinstance(port, bool) or not isinstance(port, (int, str)):
            raise ApplyError("api_server.port: muss eine Zahl 1-65535 sein")
        try:
            port = int(str(port).strip())
        except ValueError:
            raise ApplyError("api_server.port: muss eine Zahl 1-65535 sein") from None
        if not 1 <= port <= 65535:
            raise ApplyError(f"api_server.port: {port} liegt ausserhalb 1-65535")
        out["port"] = port
    if "host" in section:
        host = section["host"]
        if not isinstance(host, str) or not host.strip():
            raise ApplyError("api_server.host: muss ein nicht-leerer String sein")
        out["host"] = host.strip()
    return out


def _profiles_public(profiles: dict, cfg: dict) -> dict:
    """profiles für das Panel normalisieren und fehlende Felder aus dem
    Top-Level erben (das Apply-Skript macht beim Schreiben dasselbe)."""
    base_primary = cfg.get("primary") or {}
    base_fb = cfg.get("fallback") or []
    base_pool = cfg.get("pool") or {}
    out = {}
    for name, prof in (profiles or {}).items():
        if not isinstance(prof, dict):
            continue
        prof_primary = prof.get("primary") if isinstance(prof.get("primary"), dict) else {}
        prof_pool = prof.get("pool") if isinstance(prof.get("pool"), dict) else {}
        fb = prof.get("fallback") if "fallback" in prof else base_fb
        out[str(name)] = {
            "enabled": bool(prof.get("enabled", True)),
            "primary": {"provider": str(prof_primary.get("provider")
                                       or base_primary.get("provider") or ""),
                        "model": str(prof_primary.get("model")
                                     or base_primary.get("model") or "")},
            "fallback": fb if isinstance(fb, list) else [],
            "pool": {
                "followActive": bool(prof_pool["followActive"])
                if "followActive" in prof_pool else bool(base_pool.get("followActive")),
                "onlyActiveWhenExpiring": bool(prof_pool["onlyActiveWhenExpiring"])
                if "onlyActiveWhenExpiring" in prof_pool
                else bool(base_pool.get("onlyActiveWhenExpiring")),
            },
        }
    return out


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

    # Weitere Profile: das Panel ist schreibgeschützt, also nur validieren und
    # durchreichen. Fehlen sie im Payload, werden die der aktuellen SOT
    # übernommen — sonst würde ein apply() die Profile löschen.
    current = config(force=True)
    profiles_in = payload.get("profiles")
    if not isinstance(profiles_in, dict):
        profiles_in = current.get("profiles") or {}
    profiles_out = {}
    for name, prof in profiles_in.items():
        if not isinstance(prof, dict):
            continue
        label = f"profiles.{name}"
        if prof.get("enabled") is False:
            profiles_out[str(name)] = {"enabled": False}
            continue
        p_out = {"enabled": True}
        p_in = prof.get("primary") if isinstance(prof.get("primary"), dict) else {}
        if p_in:
            p_out["primary"] = _validate(p_in, f"{label}.primary")
        # KEIN else: fehlt "primary", wird nichts geschrieben — das Profil erbt dann
        # beim Apply vom Top-Level (apply-hermes.sh: "jedes erbt vom Top-Level, was
        # es nicht selbst setzt"). Wuerde hier der geerbte Wert eingetragen, laese
        # der naechste Apply ihn als EIGENEN Wert und das Profil folgte dem
        # Top-Level nicht mehr. Genau so ist "kleve" auf glm-5.3-flash eingefroren.
        p_fb = prof.get("fallback")
        if p_fb is not None:
            items = []
            for i, f in enumerate(p_fb, start=1):
                if not isinstance(f, dict) or (not f.get("model") and not f.get("provider")):
                    continue
                items.append(_validate(f, f"{label}.fallback[{i}]"))
            p_out["fallback"] = items
        # sonst: nichts schreiben → Fallbacks werden geerbt
        if isinstance(prof.get("pool"), dict):
            p_out["pool"] = {
                "followActive": bool(prof["pool"].get("followActive")),
                "onlyActiveWhenExpiring": bool(prof["pool"].get("onlyActiveWhenExpiring")),
            }
        profiles_out[str(name)] = p_out

    pool_in = payload.get("pool") or {}
    current_pool = (current.get("pool") or {})
    # api_server bevorzugt aus dem Payload, sonst aus der aktuellen SOT
    # (das Panel schickt es evtl. noch nicht mit) — sonst wuerde ein apply()
    # den API-Server-Block loeschen bzw. auf den Default zuruecksetzen.
    api_in = _validate_api_server(payload.get("api_server"))
    api_server = _api_server_public({**_api_server_public(current.get("api_server")), **api_in})
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
        "api_server": api_server,
        "restart": bool(payload.get("restart", True)),
        "profiles": profiles_out,
    }
    new_text = json.dumps(new_cfg, indent=2, ensure_ascii=False) + "\n"

    # Like-for-like vergleichen: _public() materialisiert die Profile fuer das
    # Panel (fehlende Felder erben vom Top-Level). new_cfg materialisiert nicht,
    # damit die Vererbung in der SOT erhalten bleibt. Ein direkter Vergleich
    # beider Formen waere deshalb IMMER ungleich und wuerde bei jedem Apply einen
    # Commit erzeugen.
    changed = _public(config()) != _public(new_cfg)
    commit = None
    if changed:
        desc = ", ".join(f"{f['provider']}/{f['model']}" for f in fb_out) or "—"
        prof_desc = ", ".join(
            f"{n}=" + (f"{p['primary']['provider']}/{p['primary']['model']}"
                       if p.get("primary") else "erbt")
            for n, p in profiles_out.items()
        ) or "—"
        commit = github_api.put_files({CONFIG_PATH: new_text}, (
            "hestia: Hermes-Update\n\n"
            f"primary={primary['provider']}/{primary['model']}\n"
            f"fallback={desc}\n"
            f"pool.followActive={new_cfg['pool']['followActive']}\n"
            f"api_server={new_cfg['api_server']['enabled']}@"
            f"{new_cfg['api_server']['host']}:{new_cfg['api_server']['port']}\n"
            f"profiles={prof_desc}"
        ))
        _log(f"Hermes-SOT aktualisiert: {primary['provider']}/{primary['model']} | "
             f"fallback={desc} | profiles={prof_desc}")

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
