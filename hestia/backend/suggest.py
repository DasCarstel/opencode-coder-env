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

from catalog import fetch_models, usable

OC_SERVER_URL = os.environ.get("OC_SERVER_URL", "http://localhost:49374").rstrip("/")
OC_SERVER_USER = os.environ.get("OC_SERVER_USER", "opencode")
OC_SERVER_PASSWORD = os.environ.get("OC_SERVER_PASSWORD", "")
OC_SUGGEST_DIR = os.environ.get(
    "OC_SUGGEST_DIR",
    "/home/carstenmueller2002/opencode-coder-env/workspaces/infrastructure/server-management",
)
# Analyse-Modell: standardmäßig das aktuelle @heavy-Modell aus den Overrides.
# Override per OC_SUGGEST_MODEL ("provider/model#variant"); leer = Tier-Heavy.
OC_SUGGEST_MODEL = os.environ.get("OC_SUGGEST_MODEL", "").strip()
PRESET = os.environ.get("HESTIA_PRESET_PROVIDER", "opencode-go")
POLL_INTERVAL = 5.0
POLL_TIMEOUT = float(os.environ.get("OC_SUGGEST_TIMEOUT", "150"))
STALL_TIMEOUT = float(os.environ.get("OC_SUGGEST_STALL_TIMEOUT", "45"))
TRIES = int(os.environ.get("OC_SUGGEST_TRIES", "1"))
# Analyse-Sessions nach dem Lauf automatisch löschen (1 = zum Debuggen behalten)
KEEP_SESSIONS = os.environ.get("OC_SUGGEST_KEEP_SESSIONS", "0") == "1"


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


def _resolve_tier_model(tier: str, override_ref: str = "", override_variant: str = "") -> tuple:
    """Modell + Info eines Tiers aus den Overrides (None, wenn nicht nutzbar).

    override_ref/override_variant: explizit gewähltes Analyse-Modell (Panel),
    hat Vorrang vor dem committeten Tier-Modell.
    """
    ref = override_ref
    variant = override_variant
    if not ref and tier == "heavy" and OC_SUGGEST_MODEL:
        ref, _, variant = OC_SUGGEST_MODEL.partition("#")
    if not ref:
        try:
            from github_api import get_overrides
            preset = (get_overrides()["config"].get("presets") or {}).get(PRESET) or {}
            entry = preset.get(tier) or {}
            ref = (entry.get("model") or "").strip()
            variant = (entry.get("variant") or "").strip()
        except Exception:
            return None, {}
    if not ref:
        return None, {}
    ok, res = usable(ref)
    if not ok:
        return None, {}
    provider, _, mid = ref.partition("/")
    if variant and variant not in (res.get("variants") or []):
        variant = ""
    body = {"providerID": provider, "id": mid}
    if variant:
        body["variant"] = variant  # leeres variant-Feld lässt den Lauf sofort fehlschlagen
    label = ref + (f"#{variant}" if variant else "")
    return body, {"model": ref, "variant": variant or None, "label": label}


def analysis_candidates(override_ref: str = "", override_variant: str = "") -> list:
    """Kandidaten-Kette: gewähltes/@heavy -> @medium -> Session-Default.

    Ein explizit gewähltes Modell (override_ref) ersetzt den ersten Kandidaten;
    die Fallback-Kette bleibt als Sicherheitsnetz erhalten.
    """
    chain = []
    if override_ref:
        body, info = _resolve_tier_model("heavy", override_ref, override_variant)
        if body:
            chain.append((body, {"source": "panel-selection", **info}))
    if not chain:
        body, info = _resolve_tier_model("heavy")
        if body:
            chain.append((body, {"source": "tier-heavy", **info}))
    body, info = _resolve_tier_model("medium")
    if body:
        chain.append((body, {"source": "tier-medium", **info}))
    chain.append((None, {"source": "session-default", "label": "Session-Default"}))
    return chain


def resolve_analysis_model() -> tuple:
    """Erstes Analyse-Modell (=@heavy bzw. Override); Kettenlogik in analysis_candidates()."""
    chain = analysis_candidates()
    return chain[0][0], chain[0][1]


class StallError(RuntimeError):
    """Die Provider-Anfrage liefert keinen Fortschritt (0 Token / keine Updates)."""


def _active_ids() -> set:
    _, act = _req("GET", f"{OC_SERVER_URL}/api/session/active")
    active = act.get("data", act) if isinstance(act, dict) else act
    if isinstance(active, dict):
        return set(active.keys())
    if isinstance(active, list):
        return {s.get("id") for s in active}
    return set()


def _progress(sid: str) -> tuple:
    """Fortschritts-Signatur: (tokenSumme, updated-Zeit)."""
    st, res = _req("GET", f"{OC_SERVER_URL}/api/session/{sid}")
    if st != 200:
        return (0, 0)
    d = res.get("data", res) if isinstance(res, dict) else res
    tok = d.get("tokens") or {}
    cache = tok.get("cache") or {}
    total = (tok.get("input") or 0) + (tok.get("output") or 0) + (tok.get("reasoning") or 0) + (cache.get("read") or 0)
    updated = ((d.get("time") or {}).get("updated")) or 0
    return (total, updated)


def _interrupt(sid: str):
    _req("POST", f"{OC_SERVER_URL}/api/session/{sid}/interrupt")


def _delete_session(sid: str):
    """Analyse-Session vollständig entfernen (kein Session-Müll im Workspace)."""
    if KEEP_SESSIONS:
        return False
    try:
        _interrupt(sid)  # falls noch laufend
    except Exception:
        pass
    try:
        st, _ = _req("DELETE", f"{OC_SERVER_URL}/api/session/{sid}")
        return st in (200, 202, 204)
    except Exception:
        return False


def _attempt(prompt: str, model_body, used_info: dict) -> dict:
    sstatus, sres = _req("POST", f"{OC_SERVER_URL}/api/session",
                         {"location": {"directory": OC_SUGGEST_DIR},
                          "title": "Hestia Modell-Vorschlag"})
    if sstatus not in (200, 201):
        raise RuntimeError(f"Session-Anlegen fehlgeschlagen ({sstatus}): {sres}")
    sid = (sres.get("data") or sres).get("id") if isinstance(sres, dict) else None
    if not sid:
        raise RuntimeError(f"Keine Session-ID: {sres}")

    try:
        if model_body:
            mstatus, _ = _req("POST", f"{OC_SERVER_URL}/api/session/{sid}/model", {"model": model_body})
            if mstatus not in (200, 201, 204):
                used_info.clear()
                used_info.update({"source": "session-default", "note": f"Modell-Umschaltung fehlgeschlagen ({mstatus})"})
        if used_info.get("model"):
            used_info["label"] = used_info["model"] + (f"#{used_info['variant']}" if used_info.get("variant") else "")
        else:
            used_info["label"] = "Session-Default"

        pstatus, pres = _req("POST", f"{OC_SERVER_URL}/api/session/{sid}/prompt", {"text": prompt})
        if pstatus not in (200, 201, 202):
            raise RuntimeError(f"Prompt-Feuern fehlgeschlagen ({pstatus}): {pres}")

        deadline = time.time() + POLL_TIMEOUT
        last_progress = time.time()
        last_sig = _progress(sid)
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            if sid not in _active_ids():
                break  # fertig
            sig = _progress(sid)
            if sig != last_sig:
                last_sig, last_progress = sig, time.time()
            elif time.time() - last_progress > STALL_TIMEOUT:
                raise StallError(
                    f"Keine Provider-Antwort seit {int(STALL_TIMEOUT)}s — "
                    "modell-/provider-seitiger Hänger."
                )
        else:
            raise RuntimeError(f"Timeout nach {int(POLL_TIMEOUT)}s — bitte erneut versuchen.")

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
        decision = json.loads(m.group(0))
        deleted = _delete_session(sid)
        return {"session": sid, "decision": decision, "raw": full,
                "sessionDeleted": bool(deleted)}
    except Exception:
        _delete_session(sid)
        raise


def suggest(override_ref: str = "", override_variant: str = "", progress=None) -> dict:
    """KI-Vorschlag. progress(dict) wird über den Live-Zustand informiert
    (Modell, Versuch, Hinweis) — für die Anzeige im Panel während des Laufs."""
    if not OC_SERVER_PASSWORD:
        raise RuntimeError("OC_SERVER_PASSWORD nicht gesetzt")
    prompt = _build_prompt()
    candidates = analysis_candidates(override_ref, override_variant)

    def report(**kw):
        if progress:
            try:
                progress(kw)
            except Exception:
                pass

    errors = []
    for idx, (model_body, info) in enumerate(candidates):
        label = info.get("label", "?")
        report(model=label, source=info.get("source"), attempt=idx + 1, total=len(candidates),
               phase="start", note=None)
        try:
            res = _attempt(prompt, model_body, info)
            res["analyzedWith"] = info
            res["attempt"] = idx + 1
            if idx > 0:
                info["note"] = (
                    f"Fallback (vorher: {', '.join(c[1].get('label', '?') for c in candidates[:idx])} "
                    "antwortete nicht)"
                )
            report(model=label, source=info.get("source"), attempt=idx + 1, total=len(candidates),
                   phase="done", note=info.get("note"))
            return res
        except Exception as e:  # noqa: BLE001 — nächster Kandidat
            errors.append(f"{label}: {e}")
            report(model=label, source=info.get("source"), attempt=idx + 1, total=len(candidates),
                   phase="aborted", note=str(e)[:200])
            continue
    report(phase="failed", note=" | ".join(errors)[:300])
    raise RuntimeError("Analyse fehlgeschlagen — " + " | ".join(errors))

