#!/usr/bin/env python3
"""dcp.py — DCP (Dynamic Context Pruning) Schwellenwerte als SOT.

Die Schwellenwerte (maxContextLimit / minContextLimit / modelMaxLimits /
modelMinLimits) liegen in .opencode/dcp.jsonc und werden vom Panel geschrieben.
Sie wirken nach dem naechsten Container-Restart (Rollout), weil DCP die Config
beim Start liest.
"""
import json
import os

import github_api
import hjsonc

DCP_SETTINGS_PATH = os.environ.get("HESTIA_DCP_SETTINGS_PATH", ".opencode/dcp.jsonc")

# Grenzen fuer die Panel-Eingaben
LIMIT_RANGE = (0, 10_000_000)


class SettingsError(Exception):
    """Ungueltige Nutzereingabe fuer die DCP-Schwellenwerte."""


def _log(msg):
    print(f"[hestia-dcp] {msg}", flush=True)


def _is_percent(v) -> bool:
    return isinstance(v, str) and v.strip().endswith("%")


def _validate_limit(value, name: str):
    """Akzeptiert int oder 'X%' (String)."""
    if _is_percent(value):
        # Prozent-String: grob pruefen
        num = value.strip().rstrip("%").strip()
        try:
            n = float(num)
        except (TypeError, ValueError):
            raise SettingsError(f"'{name}': Prozentwert '{value}' ungueltig")
        if not (0 <= n <= 100):
            raise SettingsError(f"'{name}': Prozentwert {n} außerhalb 0..100")
        return value.strip()
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"'{name}': muss eine ganze Zahl oder 'X%' sein")
    if not (LIMIT_RANGE[0] <= value <= LIMIT_RANGE[1]):
        raise SettingsError(f"'{name}': {value} außerhalb {LIMIT_RANGE[0]}..{LIMIT_RANGE[1]}")
    return value


def read_settings() -> dict:
    """DCP-SOT lesen und normalisieren. Fehlende/kaputte Datei → Defaults."""
    data = {}
    try:
        raw = github_api.get_file(DCP_SETTINGS_PATH)
        if raw:
            data = hjsonc.parse(raw) or {}
    except Exception as e:  # noqa: BLE001
        _log(f"settings: {DCP_SETTINGS_PATH} nicht lesbar ({e}) — Defaults")
        data = {}
    if not isinstance(data, dict):
        data = {}
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = True
    compress = data.get("compress")
    if not isinstance(compress, dict):
        compress = {}
    max_limit = compress.get("maxContextLimit", 100000)
    min_limit = compress.get("minContextLimit", 50000)
    model_max = compress.get("modelMaxLimits")
    model_min = compress.get("modelMinLimits")
    if not isinstance(model_max, dict):
        model_max = {}
    if not isinstance(model_min, dict):
        model_min = {}
    # Normalisieren: Prozent-Strings erlaubt, Zahlen als int
    try:
        max_limit = _validate_limit(max_limit, "maxContextLimit")
    except SettingsError:
        max_limit = 100000
    try:
        min_limit = _validate_limit(min_limit, "minContextLimit")
    except SettingsError:
        min_limit = 50000
    return {
        "enabled": enabled,
        "maxContextLimit": max_limit,
        "minContextLimit": min_limit,
        "modelMaxLimits": model_max,
        "modelMinLimits": model_min,
    }


def _validate_model_limits(limits, name: str) -> dict:
    if limits is None:
        return {}
    if not isinstance(limits, dict):
        raise SettingsError(f"'{name}': muss ein Objekt sein")
    out = {}
    for key, val in limits.items():
        if not isinstance(key, str) or "/" not in key:
            raise SettingsError(f"'{name}.{key}': Schluessel muss 'provider/model' sein")
        out[key] = _validate_limit(val, f"{name}.{key}")
    return out


def update_settings(payload: dict) -> dict:
    """Schwellenwerte validieren, in die DCP-SOT mergen und committen.

    Nur die Felder maxContextLimit / minContextLimit / modelMaxLimits /
    modelMinLimits werden ueberschrieben; der Rest der Datei bleibt erhalten.
    Wirkt nach dem naechsten Container-Restart (Rollout wird getriggert).
    """
    if not isinstance(payload, dict):
        raise SettingsError("Body muss ein JSON-Objekt sein")

    if "maxContextLimit" not in payload:
        raise SettingsError("'maxContextLimit' fehlt")
    if "minContextLimit" not in payload:
        raise SettingsError("'minContextLimit' fehlt")

    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SettingsError("'enabled' muss true oder false sein")

    max_limit = _validate_limit(payload["maxContextLimit"], "maxContextLimit")
    min_limit = _validate_limit(payload["minContextLimit"], "minContextLimit")

    # Wenn beide Zahlen sind: max >= min pruefen
    if not _is_percent(max_limit) and not _is_percent(min_limit):
        if max_limit < min_limit:
            raise SettingsError("'maxContextLimit' muss >= 'minContextLimit' sein")

    model_max = _validate_model_limits(
        payload.get("modelMaxLimits", {}), "modelMaxLimits")
    model_min = _validate_model_limits(
        payload.get("modelMinLimits", {}), "modelMinLimits")

    # Bestehende Datei lesen, nur compress-Felder mergen
    raw = ""
    data = {}
    try:
        raw = github_api.get_file(DCP_SETTINGS_PATH) or ""
        if raw:
            data = hjsonc.parse(raw) or {}
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["enabled"] = enabled
    compress = data.get("compress")
    if not isinstance(compress, dict):
        compress = {}
    compress["maxContextLimit"] = max_limit
    compress["minContextLimit"] = min_limit
    compress["modelMaxLimits"] = model_max
    compress["modelMinLimits"] = model_min
    data["compress"] = compress

    DCP_HEADER = ("// DCP — Kontext-Kompaktierung (Schwellenwerte, SOT des Hestia-Panels).\n"
                  "// Geschrieben ueber POST /api/dcp/settings, gelesen von DCP beim Container-Start.\n")
    text = hjsonc.serialize(data, header=DCP_HEADER)

    commit = github_api.put_files(
        {DCP_SETTINGS_PATH: text}, "hestia: DCP-Schwellenwerte aktualisiert")
    try:
        github_api.dispatch_workflow(
            os.environ.get("ROLLOUT_WORKFLOW", "rollout.yml"))
    except Exception as e:  # noqa: BLE001
        _log(f"rollout dispatch fehlgeschlagen: {e}")

    _log(f"settings: max={max_limit} min={min_limit} "
         f"modelMax={len(model_max)} modelMin={len(model_min)} "
         f"(Commit {str((commit or {}).get('sha', ''))[:8]})")
    return {
        "saved": True,
        "settings": {
            "enabled": enabled,
            "maxContextLimit": max_limit,
            "minContextLimit": min_limit,
            "modelMaxLimits": model_max,
            "modelMinLimits": model_min,
        },
        "commit": commit,
        "rollout": "CI/CD laeuft (validate + Container-Restart)",
    }


def state() -> dict:
    """Status fuer das Panel: aktuelle Schwellenwerte aus der SOT."""
    settings = read_settings()
    return {
        "path": DCP_SETTINGS_PATH,
        "settings": settings,
    }
