#!/usr/bin/env python3
"""suggest.py — KI-Modellvorschlag über eine OpenCode-Session (REST).

Muster wie im Hermes-'space-matrix'-Bericht dokumentiert (Server v2.0.24):
  POST /api/session                → Session anlegen (richtiger Workspace)
  POST /api/session/<sid>/prompt   → Auftrag feuern (kehrt sofort zurück)
  GET  /api/session/<sid>/message  → Antwort lesen
  GET  /api/session/active         → Ende-Erkennung (sid fehlt ⇒ fertig)
"""
import base64
import json
import os
import time
import urllib.request
import urllib.error

from catalog import fetch_models

OC_SERVER_URL = os.environ.get("OC_SERVER_URL", "http://localhost:49374").rstrip("/")
OC_SERVER_USER = os.environ.get("OC_SERVER_USER", "opencode")
OC_SERVER_PASSWORD = os.environ.get("OC_SERVER_PASSWORD", "")
OC_SUGGEST_DIR = os.environ.get(
    "OC_SUGGEST_DIR",
    "/home/carstenmueller2002/opencode-coder-env/workspaces/infrastructure/server-management",
)
OC_SUGGEST_MODEL = os.environ.get("OC_SUGGEST_MODEL", "")  # leer = Session-Default; Budget-Hinweis: fast/gleiche Tier уровня ok
POLL_INTERVAL = 6.0
POLL_TIMEOUT = 540.0


def _headers():
    tok = base64.b64encode(f"{OC_SERVER_USER}:{OC_SERVER_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {tok}", "Content-Type": "application/json"}


def _req(method: str, url: str, body: dict | None = None, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode() or "{}"
            return resp.status, json.loads(payload)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def _build_prompt() -> str:
    models = [m for m in fetch_models() if m["providerID"] == "opencode-go" and m["status"] == "active"]
    lines = []
    for m in models:
        c = m["cost"]
        v = ",".join(v for v in m["variants"] if v) or "-"
        lim = m.get("limit") or {}
        lines.append(
            f"- {m['modelID']} | {m['name']} | in ${c.get('input')}/M, out ${c.get('output')}/M"
            f" | cache r ${c.get('cacheRead')}/w ${c.get('cacheWrite')} | ctx {lim.get('context', '?')} | variants [{v}]"
        )
    catalog = "\n".join(lines)
    from github_api import get_overrides
    try:
        current = get_overrides()["config"].get("presets", {}).get("opencode-go", {})
        current_str = json.dumps(
            {k: current[k].get("model") for k in ("fast", "medium", "heavy") if k in current},
            ensure_ascii=False,
        )
    except Exception:
        current_str = "unbekannt"
    return f"""Du bist Modell-Stratege für ein Homelab (Coder-Workspaces, OpenCode Agenten-Fleet).
Wähle aus dem Live-Katalog unten die beste 3-Tier-Anordnung für das Router-Plugin
(opencode-go-Preset, alle drei Tier müssen Models DIESER Liste sein):

ROLLEN (Task-Taxonomie des Routers):
- @fast (costRatio 1): nur-lesende Exploration, Suche, grep — sehr viele kleine Dispatches, Kostentreiber #1.
- @medium (costRatio 5): Implementation, Refactoring, Tests — der Arbeitspferd-Tier.
- @heavy (costRatio 12): Architektur, komplexes Debugging (nach 2+ Fehlschlägen), Security-Review — selten, aber stark.

AKTUELLE WERTE: {current_str}

MODELL-KATALOG (opencode-go, alle active; Preis = USD pro Mio Tokens):
{catalog}

AUFGABE: Wähle je Tier das kosten-günstigste Modell, das die Rolle zuverlässig bewältigt
(cheap-but-capable statt Kompromiss-frei). Berücksichtige: Tool-Fähigkeit ist Pflicht;
@heavy braucht starkes Reasoning (max-effort Variante bevorzugen wenn vorhanden);
wähle Varianten aus der Liste. Wenn sich zwischen zwei Modellen der Preis kaum
unterscheidet, nimm das neuere/stärkere.

ANTWORTE NUR MIT folgendem JSON (keine weiteren Worte, keine Backticks):
{{"reasoning": "ein Satz Gesamtbegründung",
  "fast": {{"model": "opencode-go/<modelID>", "variant": "<variant|leer>", "reason": "<1 Satz>"}},
  "medium": {{"model": "opencode-go/<modelID>", "variant": "<variant|leer>", "reason": "<1 Satz>"}},
  "heavy": {{"model": "opencode-go/<modelID>", "variant": "<variant|leer>", "reason": "<1 Satz>"}}}}"""


def suggest() -> dict:
    if not OC_SERVER_PASSWORD:
        raise RuntimeError("OC_SERVER_PASSWORD nicht gesetzt")
    prompt = _build_prompt()
    sstatus, sres = _req("POST", f"{OC_SERVER_URL}/api/session",
                         {"location": {"directory": OC_SUGGEST_DIR},
                          "title": "Hestia Modell-Vorschlag"})
    if sstatus not in (200, 201):
        raise RuntimeError(f"Session-Anlegen fehlgeschlagen ({sstatus}): {sres}")
    sid = (sres.get("data") or sres).get("id") if isinstance(sres, dict) else None
    if not sid:
        raise RuntimeError(f"Keine Session-ID: {sres}")
    pstatus, pres = _req("POST", f"{OC_SERVER_URL}/api/session/{sid}/prompt", {"text": prompt})
    if pstatus not in (200, 201, 202):
        raise RuntimeError(f"Prompt-Feuern fehlgeschlagen ({pstatus}): {pres}")

    done_at = None
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        ast, act = _req("GET", f"{OC_SERVER_URL}/api/session/active")
        active = act.get("data", act) if isinstance(act, dict) else act
        if isinstance(active, dict):
            ids = set(active.keys())
        elif isinstance(active, list):
            ids = {s.get("id") for s in active}
        else:
            ids = set()
        if sid not in ids:
            done_at = True
            break
    if not done_at:
        raise RuntimeError("Timeout: Analyse-Session läuft weiterhin (Ergebnis später über /api/session/%s/message abrufbar)" % sid)

    mstatus, mres = _req("GET", f"{OC_SERVER_URL}/api/session/{sid}/message?limit=100")
    if mstatus != 200:
        raise RuntimeError(f"Message-Abruf fehlgeschlagen ({mstatus}): {mres}")
    raw = mres.get("data") if isinstance(mres, dict) else mres
    msgs = raw if isinstance(raw, list) else (raw.get("messages") or [] if isinstance(raw, dict) else [])
    texts = []
    for msg in msgs:
        # v2 API: {"type": "assistant", "content": [{"type": "text", "text": ...}, ...]}
        if msg.get("type") != "assistant" and msg.get("role") != "assistant":
            continue
        parts = msg.get("content") or msg.get("parts") or []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                texts.append(part["text"])
    full = "\n".join(texts)
    import re
    m = re.search(r"\{.*\}", full, re.S)
    if not m:
        raise RuntimeError(f"Kein JSON in der Antwort erkannt: {full[:300]}")
    blob = m.group(0)
    decision = json.loads(blob)
    return {"session": sid, "decision": decision, "raw": full}
