#!/usr/bin/env python3
"""derive-agents.py — Primary-Agents (build/plan) aus dem @medium-Tier ableiten.

Wird bei jedem Container-Start von startup.sh aufgerufen.

Regeln:
  build = @medium mit Standardvariante
  plan  = @medium mit HÖCHSTER Effort-Variante (falls das Modell Varianten hat)

Quellen:
  - Tier-Konfiguration: $REPO_DIR/.opencode/opencode-model-router.overrides.jsonc
  - Varianten-Liste:     lokaler OpenCode-Server /api/model (Basic-Auth)
Ziel:
  - merge in /root/.config/opencode/opencode.json unter "agents"
    (nur "model" — System-Prompts der Built-ins bleiben unangetastet)
"""
import base64
import json
import os
import sys
import time
import urllib.request

REPO = os.environ.get("REPO_DIR", "/home/carstenmueller2002/opencode-coder-env")
OVERRIDES = os.path.join(REPO, ".opencode", "opencode-model-router.overrides.jsonc")
GLOBAL_CFG = os.environ.get("OPENCODE_GLOBAL_CFG", "/root/.config/opencode/opencode.json")
SERVER = os.environ.get("OC_SERVER_URL", "http://127.0.0.1:49374").rstrip("/")
SERVER_USER = os.environ.get("OC_SERVER_USER", "opencode")
PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD", "")

# Effort-Rangfolge für die "höchste" Variante
RANK = {"minimal": 1, "none": 2, "low": 3, "medium": 4, "thinking": 4,
        "high": 5, "xhigh": 6, "max": 7}


def log(msg):
    print(f"  {msg}", flush=True)


def variants_of(ref, retries=12, pause=4):
    """Varianten-Liste des Modells vom lokalen OpenCode-Server holen."""
    if not PASSWORD:
        return []
    auth = base64.b64encode(f"{SERVER_USER}:{PASSWORD}".encode()).decode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"{SERVER}/api/model",
                headers={"Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            models = data.get("data", data) if isinstance(data, dict) else data
            for m in models:
                if f"{m.get('providerID')}/{m.get('modelID')}" == ref:
                    return [v.get("id") for v in (m.get("variants") or []) if v.get("id")]
            return []
        except Exception:
            if attempt < retries - 1:
                time.sleep(pause)
    return []


def main():
    if not os.path.isfile(OVERRIDES):
        log("⚠ Overrides-Datei fehlt — Primary-Agents unverändert")
        return 0
    sys.path.insert(0, os.path.join(REPO, "hestia", "backend"))
    try:
        import hjsonc
        cfg = hjsonc.parse(open(OVERRIDES).read())
    except Exception as e:
        log(f"⚠ Overrides nicht lesbar ({e}) — Primary-Agents unverändert")
        return 0

    preset = (cfg.get("presets") or {}).get(cfg.get("activePreset") or "", {})
    medium = preset.get("medium") or {}
    ref = (medium.get("model") or "").strip()
    if not ref:
        log("⚠ Kein @medium-Modell gefunden — Primary-Agents unverändert")
        return 0

    med_variant = (medium.get("variant") or "").strip()
    variants = variants_of(ref)
    ranked = sorted([v for v in variants if v in RANK], key=lambda v: RANK[v])
    highest = ranked[-1] if ranked else ""

    build_model = ref + (f"#{med_variant}" if med_variant else "")
    plan_model = ref + (f"#{highest}" if highest else "")

    try:
        with open(GLOBAL_CFG) as f:
            global_cfg = json.load(f)
    except Exception:
        global_cfg = {"$schema": "https://opencode.ai/config.json"}
    agents = global_cfg.setdefault("agents", {})
    agents["build"] = {"model": build_model}
    agents["plan"] = {"model": plan_model}
    with open(GLOBAL_CFG, "w") as f:
        json.dump(global_cfg, f, indent=2, ensure_ascii=False)
    log(f"✓ Primary-Agents gesetzt: build={build_model} | plan={plan_model}"
        + ("" if highest else " (Modell ohne Varianten)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
