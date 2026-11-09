#!/usr/bin/env python3
"""Wardrobe-Tab: Tokenpreise + Go-Monatslimits für die aktiven Wardrobe-Modelle.

Quellen:
- Tokenpreise (live): https://models.dev/api.json, Provider-Key `opencode-go`
  (pro 1M Tokens; models.dev verlangt wie opencode.ai eine Custom-UA —
  der Default-UA von urllib wird abgelehnt, daher `my-coding-agent/1.0`).
- Monatslimits: Block `budgets` in .opencode/hestia-benchmarks.jsonc,
  manuell gepflegt (siehe gobudget.settings()) — bewusst KEIN Hardcode
  hier, damit es nur eine Quelle der Wahrheit gibt.

Funktionen sind testbar ohne Netz (`parse_costs`, `merge_payload`) — der
Server-Endpunkt /api/wardrobe/costs ruft nur `payload()` auf.
"""
import datetime
import json
import time
import urllib.error
import urllib.request

PRICE_URL = "https://models.dev/api.json"
PROVIDER = "opencode-go"
# models.dev/operator-Inferenz-Dienste lehnen den Default-urllib-UA ab (vgl.
# accounts.py) — gleicher UA wie der OpenCode-Agent.
PRICE_UA = "my-coding-agent/1.0"
PRICE_TIMEOUT = 10  # Sekunden
PRICE_TTL = 600.0   # Sekunden
# checked-Stempel für die Limits — Stand der letzten manuellen Pflege
# des `budgets`-Blocks (Go-Doku).
LIMITS_CHECKED = "2026-11-09"

PRICE_CACHE = {"data": None, "ts": 0.0}

# Aktive Wardrobe-Modelle (Reihenfolge = Tabellen-Reihenfolge im Panel).
# badge/note: kurze Beschreibung, wofür das Modell im Wardrobe dient.
ACTIVE_MODELS = [
    {"id": "glm-5.3-flash", "badge": "aktiv",
     "note": "Tagging + Outfit-Text"},
    {"id": "gpt-6-luna", "badge": None, "note": None},
    {"id": "gemini-4-argon", "badge": None, "note": None},
    {"id": "mimo-v2.6-flash", "badge": None, "note": None},
    {"id": "deepseek-v4.1-flash", "badge": None, "note": None},
    {"id": "minimax-m3.1-flash", "badge": None, "note": None},
    {"id": "longcat-2.0", "badge": None, "note": None},
    {"id": "claude-haiku-5-5", "badge": None, "note": None},
    {"id": "qwen3.8-max", "badge": None, "note": None},
    {"id": "kimi-k2.7-code", "badge": None, "note": None},
]

# Statischer Info-Block für den Tab (Wardrobe-Architektur, gemessene Kosten).
WARDROBE_INFO = {
    "active": "Tagging + Outfit-Text: glm-5.3-flash (via wardrobe-shim → opencode.ai/inference/go)",
    "fallback": "gemini-flash-latest (automatisch, bei Go-Fehlern)",
    "image_render": "Gemini Nano Banana (~0.05 $/Bild, nicht Go)",
    "measured": (
        "Typisches Tagging ≤ 0.0003 $, typischer Vorschlagslauf ≤ 0.001 $ "
        "(gemessen 2026-11-09, echte Prompts)"
    ),
}


def parse_costs(raw: dict) -> dict:
    """api.json-Stringparsing: dict provider→{models:{id:{cost…}}} → dict.

    Rückgabe: model_id → {"input": float, "output": float, "cache_read": float}
    (Preise je 1M Tokens). Modelle ohne cost-Block werden übersprungen.
    """
    prov = ((raw or {}).get(PROVIDER) or {})
    models = prov.get("models") or {}
    out = {}
    if not isinstance(models, dict):
        return out
    for mid, m in models.items():
        if not isinstance(m, dict):
            continue
        cost = m.get("cost")
        if not isinstance(cost, dict):
            continue
        entry = {}
        for key in ("input", "output", "cache_read"):
            val = cost.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                entry[key] = float(val)
        if entry:
            out[str(mid)] = entry
    return out


def fetch_model_costs() -> dict | None:
    """Live-Preise von models.dev; Fehler → None (Panel zeigt dann „—").

    Cache modulseitig 600 s — der Tab ist eher Info als Trigger.
    """
    if PRICE_CACHE["data"] is not None and time.time() - PRICE_CACHE["ts"] < PRICE_TTL:
        return PRICE_CACHE["data"]
    try:
        req = urllib.request.Request(PRICE_URL, headers={"User-Agent": PRICE_UA})
        with urllib.request.urlopen(req, timeout=PRICE_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8", "replace"))
        costs = parse_costs(raw)
        if not costs:
            return None
        PRICE_CACHE.update(ts=time.time(), data=costs)
        return costs
    except (urllib.error.URLError, OSError, ValueError, AttributeError, TypeError) as e:
        # bewusst leise: Preisfehler dürfen den Tab nie kippen
        print(f"[wardrobe_costs] Preisabruf fehlgeschlagen: {e}", flush=True)
        return None


def monthly_limits() -> dict:
    """Go-Monatslimits aus gobudget.settings() — NICHT sonstwo pflegen.

    Rückgabe: model_id → float | "unlimited"; nicht gelistete Modelle fehlen
    hier einfach (→ im Payload null). gobudget wirft nie; bei Load-Fehler
    kommt {} zurück und alle Limits sind null.
    """
    try:
        import gobudget
        cfg = gobudget.settings()
        models = (cfg or {}).get("models") or {}
        return {str(k): v for k, v in models.items()
                if isinstance(v, (int, float)) or v == "unlimited"}
    except Exception as e:  # noqa: BLE001
        print(f"[wardrobe_costs] Limits nicht lesbar: {e}", flush=True)
        return {}


def fetch_price_ts() -> str | None:
    """ISO-Zeitstempel, wann die Preise zuletzt geladen wurden (oder None)."""
    if PRICE_CACHE["ts"] > 0:
        return datetime.datetime.fromtimestamp(PRICE_CACHE["ts"]).isoformat(timespec="seconds")
    return None


def merge_payload(prices: dict | None, limits: dict | None, fetched: str | None = None) -> dict:
    """Reines Merge (ohne Netz) vom Kostendict + Limits → API-Payload.

    prices: model_id → {input/output/cache_read}; limits: model_id → float|"unlimited".
    Grundlage: ACTIVE_MODELS (soft-gefiltert). Preise/Limit pro Modell können
    null sein — das Panel rendert dann einfach „—".
    """
    prices = prices or {}
    limits = limits or {}
    models = []
    for spec in ACTIVE_MODELS:
        mid = spec["id"]
        models.append({
            "id": mid,
            "badge": spec["badge"],
            "note": spec["note"],
            "prices": prices.get(mid) or None,
            "limit": limits.get(mid) if limits else None,
        })
    return {
        "models": models,
        "wardrobe": WARDROBE_INFO,
        "meta": {
            "prices_source": "models.dev",
            "prices_fetched": fetched,
            "limits_source": "hestia-benchmarks.jsonc budgets",
            "limits_checked": LIMITS_CHECKED,
        },
    }


def payload() -> dict:
    """API-Nutzlast für GET /api/wardrobe/costs (best effort, wirft nie an)."""
    prices = fetch_model_costs()
    limits = monthly_limits()
    return merge_payload(prices, limits, fetched=fetch_price_ts())


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(payload(), indent=2, ensure_ascii=False))
