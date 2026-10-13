#!/usr/bin/env python3
"""derive-agents.py — Primary-Agents (build/plan) in die globale OpenCode-Config mergen.

Wird bei jedem Container-Start von startup.sh aufgerufen.

Quelle (vom Hestia-Panel gepflegt, ein Commit):
  $REPO_DIR/.opencode/hestia-primary-agents.json
    { "build": {"model": "opencode-go/…", "variant": ""},
      "plan":  {"model": "opencode-go/…", "variant": "max"} }

Darin stehen die **effektiven** Modelle: entweder manuell im Panel gewählt oder
aus @medium hergeleitet (build = Standardvariante, plan = höchste Variante) —
die Ableitung passiert im Panel, das den Live-Katalog kennt.

Fallback: fehlt die Datei, wird aus @medium abgeleitet (Varianten via /api/model).
Ziel: merge in /root/.config/opencode/opencode.json unter "agents"
      (nur "model" — System-Prompts der Built-ins bleiben unangetastet).
"""
import base64
import json
import os
import sys
import time
import urllib.request

REPO = os.environ.get("REPO_DIR", "/home/carstenmueller2002/opencode-coder-env")
AGENTS_SRC = os.path.join(REPO, ".opencode", "hestia-primary-agents.json")
OVERRIDES = os.path.join(REPO, ".opencode", "opencode-model-router.overrides.jsonc")
GLOBAL_CFG = os.environ.get("OPENCODE_GLOBAL_CFG", "/root/.config/opencode/opencode.json")
SERVER = os.environ.get("OC_SERVER_URL", "http://127.0.0.1:49374").rstrip("/")
SERVER_USER = os.environ.get("OC_SERVER_USER", "opencode")
PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
RANK = {"minimal": 1, "none": 2, "low": 3, "medium": 4, "thinking": 4,
        "high": 5, "xhigh": 6, "max": 7}


def log(msg):
    print(f"  {msg}", flush=True)


def _ref(entry: dict) -> str:
    model = (entry or {}).get("model") or ""
    variant = (entry or {}).get("variant") or ""
    return model + (f"#{variant}" if variant else "")


def variants_of(ref, retries=6, pause=4):
    """Varianten-Liste vom lokalen OpenCode-Server (nur für den Fallback nötig)."""
    if not PASSWORD:
        return []
    auth = base64.b64encode(f"{SERVER_USER}:{PASSWORD}".encode()).decode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{SERVER}/api/model",
                                         headers={"Authorization": f"Basic {auth}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            for m in (data.get("data", data) if isinstance(data, dict) else data):
                if f"{m.get('providerID')}/{m.get('modelID')}" == ref:
                    return [v.get("id") for v in (m.get("variants") or []) if v.get("id")]
            return []
        except Exception:
            if attempt < retries - 1:
                time.sleep(pause)
    return []


def from_source_file():
    """Effektive Agenten aus der Panel-Datei (bevorzugt)."""
    if not os.path.isfile(AGENTS_SRC):
        return None
    try:
        data = json.load(open(AGENTS_SRC))
    except Exception as e:
        log(f"⚠ {AGENTS_SRC} nicht lesbar ({e})")
        return None
    out = {}
    for agent in ("build", "plan"):
        entry = data.get(agent) or {}
        if (entry.get("model") or "").strip():
            out[agent] = {"model": _ref(entry)}
    return out or None


def from_medium():
    """Fallback: aus @medium ableiten (build=Standard, plan=höchste Variante)."""
    if not os.path.isfile(OVERRIDES):
        return None
    sys.path.insert(0, os.path.join(REPO, "hestia", "backend"))
    try:
        import hjsonc
        cfg = hjsonc.parse(open(OVERRIDES).read())
    except Exception as e:
        log(f"⚠ Overrides nicht lesbar ({e})")
        return None
    medium = ((cfg.get("presets") or {}).get(cfg.get("activePreset") or "", {}) or {}).get("medium") or {}
    ref = (medium.get("model") or "").strip()
    if not ref:
        return None
    var = (medium.get("variant") or "").strip()
    ranked = sorted([v for v in variants_of(ref) if v in RANK], key=lambda v: RANK[v])
    highest = ranked[-1] if ranked else ""
    return {
        "build": {"model": ref + (f"#{var}" if var else "")},
        "plan": {"model": ref + (f"#{highest}" if highest else "")},
    }


def main():
    agents = from_source_file()
    origin = "Panel-Datei"
    if not agents:
        agents = from_medium()
        origin = "Fallback: aus @medium abgeleitet"
    if not agents:
        log("⚠ Keine Quelle für Primary-Agents — unverändert")
        return 0

    try:
        with open(GLOBAL_CFG) as f:
            global_cfg = json.load(f)
    except Exception:
        global_cfg = {"$schema": "https://opencode.ai/config.json"}
    block = global_cfg.setdefault("agents", {})
    for agent, entry in agents.items():
        block[agent] = {"model": entry["model"]}
    with open(GLOBAL_CFG, "w") as f:
        json.dump(global_cfg, f, indent=2, ensure_ascii=False)
    log(f"✓ Primary-Agents ({origin}): "
        + " | ".join(f"{a}={agents[a]['model']}" for a in sorted(agents)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
