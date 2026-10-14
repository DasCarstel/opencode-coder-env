#!/usr/bin/env python3
"""catalog.py — Live-Modell-Katalog aus dem lokalen OpenCode-Server (/api/model).

Der OpenCode-Server im Infrastructure-Container (v2.0.24+, Basic Auth) liefert
alle konfigurierten Provider+Modelle inkl. Preis (USD/Mio Token), Cache-Preise,
Kontext-Limits, Varianten (Effort) und Status. Neue OpenCode-Go-Modelle
erscheinen damit automatisch — ohne Modellpflege im Panel.
"""
import json
import os
import re
import urllib.request
import urllib.error
import base64
import time

OC_SERVER_URL = os.environ.get("OC_SERVER_URL", "http://localhost:49374").rstrip("/")
OC_SERVER_USER = os.environ.get("OC_SERVER_USER", "opencode")
OC_SERVER_PASSWORD = os.environ.get("OC_SERVER_PASSWORD", "")


def _basic_headers():
    if not OC_SERVER_PASSWORD:
        raise RuntimeError("OC_SERVER_PASSWORD nicht gesetzt (OpenBao secret/data/mcp/hermes-opencode-service)")
    tok = base64.b64encode(f"{OC_SERVER_USER}:{OC_SERVER_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


def _get(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers=_basic_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


_CACHE = {"ts": 0.0, "data": None}
CACHE_TTL = 60.0  # s

# Modelle mit Trainings-Klausel tragen dieses Namens-Suffix. Das ist eine
# Heuristik: der Katalog liefert dafuer KEIN Feld (geprueft am 14.10.2026 -
# gemini-4-argon-contributor steht dort mit status=active/enabled=true wie jedes
# andere Modell). Provider weisen diese Modelle mit "This Go model trains on
# request data" ab, solange der Workspace-Toggle aus ist.
TRAINING_SUFFIX = "-contributor"


def fetch_models(force: bool = False) -> list:
    """Normalisierter Katalog: [{providerID, modelID, name, released, status,
    enabled, variants[], cost{input,output,cacheRead,cacheWrite}, limit{context,output}}]"""
    if not force and _CACHE["data"] and time.time() - _CACHE["ts"] < CACHE_TTL:
        return _CACHE["data"]
    d = _get(f"{OC_SERVER_URL}/api/model")
    raw = d.get("data", d) if isinstance(d, dict) else d
    out = []
    for m in raw:
        cost = (m.get("cost") or [{}])[0] if m.get("cost") else {}
        cache = cost.get("cache", {}) or {}
        out.append({
            "modelID": m.get("modelID"),
            "providerID": m.get("providerID"),
            "ref": f"{m.get('providerID')}/{m.get('modelID')}",
            "name": m.get("name"),
            "released": ((m.get("time") or {}).get("released") or 0) // 1000,
            "status": m.get("status"),
            "enabled": bool(m.get("enabled", True)),
            "variants": [v.get("id") for v in (m.get("variants") or [])],
            "cost": {
                "input": cost.get("input"),
                "output": cost.get("output"),
                "cacheRead": cache.get("read"),
                "cacheWrite": cache.get("write"),
            },
            "limit": m.get("limit") or {},
        })
    out.sort(key=lambda x: (x["providerID"], x["released"] or 0), reverse=True)
    _CACHE.update(ts=time.time(), data=out)
    return out


def find_model(model_ref: str):
    for m in fetch_models():
        if m["ref"].lower() == model_ref.lower():
            return m
    return None


def usable(model_ref: str) -> tuple:
    """(ok, reason) — Status aktiv, enabled und keine Trainings-Klausel.

    Die Trainings-Klausel ist im Katalog nicht als Feld vorhanden; Provider
    kennzeichnen solche Modelle aber im Namen (Suffix "-contributor"). Ohne diese
    Pruefung bietet das Panel ein Modell an, das der Provider mit
    "This Go model trains on request data" abweist — genau der Fall vom 14.10.2026.
    """
    m = find_model(model_ref)
    if not m:
        return False, f"Modell {model_ref} nicht im Katalog"
    if m["status"] != "active":
        return False, f"Modell {model_ref} hat Status '{m['status']}' (nur 'active' erlaubt)"
    if not m["enabled"]:
        return False, f"Modell {model_ref} ist disabled"
    model_id = m.get("modelID") or ""
    if model_id.endswith(TRAINING_SUFFIX):
        return False, (
            f"Modell {model_ref} traegt die Trainings-Klausel "
            f"(Suffix '{TRAINING_SUFFIX}') und wird vom Provider abgewiesen"
        )
    return True, m


def is_free(model_ref: str) -> bool:
    """True, wenn das Modell kostenlos ist (Eingabe- UND Ausgabepreis 0)."""
    m = find_model(model_ref)
    if not m:
        return False
    cost = m.get("cost") or {}
    return not cost.get("input") and not cost.get("output")


def free_paid_counterpart(model_ref: str, prefer_provider: str = "opencode-go"):
    """Bezahltes Gegenstück zu einem Gratis-Modell finden (Heuristik).

    Gratis-Modelle tragen im Katalog üblicherweise das Suffix "-free"
    (z. B. "cat-jam-free"). Das Gegenstück ist das Modell mit derselben
    modelID ohne Suffix — sofern es einen Preis hat. Bevorzugt wird der
    gleiche Provider, sonst der Preset-Provider ("opencode-go"), sonst der
    erste Kandidat. Rückgabe: Modell-Dict (inkl. "ref") oder None.
    """
    m = find_model(model_ref)
    if not m or not is_free(model_ref):
        return None
    model_id = m["modelID"] or ""
    base = re.sub(r"-free$", "", model_id)
    if base == model_id:            # kein "-free"-Suffix → keine Ableitung möglich
        return None
    cands = [c for c in fetch_models()
             if c["modelID"] == base and (c["cost"].get("input") or c["cost"].get("output"))]
    if not cands:
        return None
    for c in cands:                 # 1. gleicher Provider
        if c["providerID"] == m["providerID"]:
            return c
    for c in cands:                 # 2. Preset-Provider
        if c["providerID"] == prefer_provider:
            return c
    return cands[0]
