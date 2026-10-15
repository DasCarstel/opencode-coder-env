#!/usr/bin/env python3
"""gobudget.py — Monatslimits und Zeitpreise des OpenCode-Go-Abos.

Quelle ist der Block `budgets` in .opencode/hestia-benchmarks.jsonc
(HESTIA_BENCHMARKS_PATH). Eine API dafür gibt es NICHT — geprüft am 15.10.2026:
https://opencode.ai/inference/go/v1/models liefert nur id/object/created/owned_by.
Die Zahlen stehen ausschließlich in der Doku und werden hier bewusst manuell
gepflegt, mit `source` und `checked`.

Doku (opencode.ai/v2/docs/console/go):
  Limits sind MONATSDOLLAR; 5 h = 20 %, Woche = 50 %, Monat = 100 %.
  DeepSeek V4.1 Flash und V4 Pro rechnen in Peak-Zeiten den DOPPELTEN Preis
  (01:00-04:00 und 06:00-10:00 UTC, Mo-Fr; sonst Off-Peak).

Grundsatz: fehlt ein Modell im Block, gibt es KEIN Budget — das Panel zeigt dann
"—" statt einer erfundenen Zahl. Budgets gelten nur für den Provider aus dem
Block (Default "opencode-go"), damit sie nicht versehentlich auch für die
Zen-Modelle unter "opencode/..." gelten.
"""
import datetime
import os
import time

DEFAULT_WINDOWS = {"h5": 0.20, "week": 0.50, "month": 1.00}
DEFAULTS = {
    "provider": "opencode-go",
    "plan": "Go",
    "source": "",
    "checked": "",
    "windows": dict(DEFAULT_WINDOWS),
    "models": {},
    "peak": {},
}

_CACHE = {"ts": 0.0, "data": None}
CACHE_TTL = 60.0  # s — wie hestia-benchmarks.jsonc; das Panel pollt jede Minute

PEAK_DAYS = {"mon-fri": range(0, 5), "mon-sun": range(0, 7), "sat-sun": range(5, 7)}


def _log(msg: str) -> None:
    print(f"[gobudget] {msg}", flush=True)


def settings(force: bool = False) -> dict:
    """Budget-Block aus der SOT; Fehler → Defaults (wirft nie)."""
    if not force and _CACHE["data"] is not None and time.time() - _CACHE["ts"] < CACHE_TTL:
        return _CACHE["data"]
    path = os.environ.get("HESTIA_BENCHMARKS_PATH", ".opencode/hestia-benchmarks.jsonc")
    raw = {}
    try:
        import github_api
        import hjsonc
        text = github_api.get_file(path)
        raw = (hjsonc.parse(text) or {}).get("budgets") or {}
    except Exception as e:  # noqa: BLE001 — Budgets dürfen nie den Lauf kippen
        _log(f"budgets nicht lesbar ({e}) — ohne Budgets")
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for key in ("provider", "plan", "source", "checked"):
        if isinstance(raw.get(key), str) and raw[key].strip():
            out[key] = raw[key].strip()

    windows = raw.get("windows")
    if isinstance(windows, dict):
        out["windows"] = {
            k: (float(windows[k]) if isinstance(windows.get(k), (int, float)) else DEFAULT_WINDOWS[k])
            for k in DEFAULT_WINDOWS
        }

    models = raw.get("models")
    if isinstance(models, dict):
        norm = {}
        for key, val in models.items():
            key = str(key).strip()
            if not key:
                continue
            if isinstance(val, (int, float)):
                norm[key] = float(val)
            elif isinstance(val, str) and val.strip().lower() in ("unlimited", "unbegrenzt"):
                norm[key] = "unlimited"
        out["models"] = norm

    peak = raw.get("peak")
    if isinstance(peak, dict):
        out["peak"] = {
            str(k).strip(): v for k, v in peak.items()
            if str(k).strip() and isinstance(v, dict)
        }

    _CACHE.update(ts=time.time(), data=out)
    return out


def split_ref(ref: str) -> tuple:
    """("opencode-go", "claude-haiku-5-5") aus "opencode-go/claude-haiku-5-5"."""
    parts = str(ref or "").split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", parts[0]


def budget_for(ref: str, cfg: dict = None) -> dict:
    """Budget eines Modells oder None.

    {"month": 15.0, "h5": 3.0, "week": 7.5, "plan": "Go", "source": …, "checked": …}
    bzw. {"unlimited": True, …} für die Gratis-Modelle.
    """
    cfg = cfg or settings()
    provider, model_id = split_ref(ref)
    if provider != cfg["provider"]:      # Budgets gelten nur fürs Go-Abo
        return None
    limit = (cfg["models"] or {}).get(model_id)
    if limit is None:
        return None
    meta = {"plan": cfg["plan"], "source": cfg["source"], "checked": cfg["checked"]}
    if limit == "unlimited":
        return {"unlimited": True, "month": None, "h5": None, "week": None, **meta}
    windows = cfg["windows"]
    return {
        "unlimited": False,
        "month": limit,
        "h5": round(limit * windows["h5"], 4),
        "week": round(limit * windows["week"], 4),
        **meta,
    }


def _parse_spans(rule: dict) -> list:
    """"01:00-04:00" → [(60, 240), …] in Minuten seit Mitternacht (UTC)."""
    spans = []
    for item in rule.get("windows") or []:
        try:
            start, end = str(item).split("-", 1)
            sh, sm = (int(x) for x in start.split(":"))
            eh, em = (int(x) for x in end.split(":"))
            spans.append((sh * 60 + sm, eh * 60 + em))
        except Exception:  # noqa: BLE001 — kaputte Angabe ignorieren statt werfen
            continue
    return spans


def peak_rule(model_id: str, provider: str = None) -> dict:
    """Peak-Regel eines Modells (oder {}).

    `provider` grenzt die Regel auf den Provider des Budget-Blocks ein (Default
    "opencode-go"): die Zeitpreise sind eine Go-Abo-Eigenschaft und dürfen nicht
    auf die Zen-Modelle unter "opencode/..." angewendet werden — deren
    Katalogpreis ist schon der jeweils gelistete Wert.
    """
    cfg = settings()
    if provider is not None and provider != cfg["provider"]:
        return {}
    return (cfg.get("peak") or {}).get(str(model_id or ""), {}) or {}


def is_peak(model_id: str, when: datetime.datetime = None, provider: str = None) -> bool:
    """Gilt für dieses Modell JETZT der Peak-Preis? (Default: jetzt, UTC)"""
    rule = peak_rule(model_id, provider)
    if not rule:
        return False
    when = when or datetime.datetime.now(datetime.timezone.utc)
    days = str(rule.get("days") or "Mon-Fri").strip().lower()
    if when.weekday() not in PEAK_DAYS.get(days, PEAK_DAYS["mon-fri"]):
        return False
    minutes = when.hour * 60 + when.minute
    return any(start <= minutes < end for start, end in _parse_spans(rule))


def price_for(model: dict, when: datetime.datetime = None) -> dict:
    """Gültiger Preis eines Katalog-Modells — inklusive Tarifstufe und Zeitfaktor.

    `model` ist ein Eintrag aus catalog.fetch_models(). Es gilt der Tarif aus
    `costEffective` (Kontextstufe, in der wir uns praktisch immer befinden);
    bei Modellen mit Peak-Regel wird der Faktor angewendet, wenn er JETZT gilt.

    Rückgabe: {input, output, cacheRead, cacheWrite, from, peak, factor}
    """
    if not isinstance(model, dict):
        return {}
    base = model.get("costEffective") or model.get("cost") or {}
    out = {
        "input": base.get("input"),
        "output": base.get("output"),
        "cacheRead": base.get("cacheRead"),
        "cacheWrite": base.get("cacheWrite"),
        "from": base.get("from"),
        "peak": False,
        "factor": 1,
    }
    model_id = str(model.get("modelID") or "")
    provider_id = str(model.get("providerID") or "")
    rule = peak_rule(model_id, provider_id)
    if rule and is_peak(model_id, when, provider_id):
        try:
            factor = float(rule.get("factor") or 1)
        except (TypeError, ValueError):
            factor = 1.0
        if factor != 1:
            for key in ("input", "output", "cacheRead", "cacheWrite"):
                if isinstance(out[key], (int, float)):
                    out[key] = round(out[key] * factor, 6)
            out["peak"] = True
            out["factor"] = factor
    return out
