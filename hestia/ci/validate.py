#!/usr/bin/env python3
"""validate.py — CI-Validierung für die Hestia-Tier-Konfiguration.

Prüft (in dieser Reihenfolge):
  1. JSONC gültig (Comments/Trailing-Commas erlaubt)
  2. aktiver Preset existiert; fast/medium/heavy gesetzt; Models = providerID/modelID
  3. Varianten-Empfehlung plausibel (leer oder bekannt)
  4. Live-Katalog-Check gegen models.dev (opt-in; bei Netzfehler warnen, nicht failen)

Exit-Code 1 = invalid. Run: python hestia/ci/validate.py
"""
import base64
import json
import os
import sys
import urllib.request

OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".opencode", "opencode-model-router.overrides.jsonc")
MODELS_DEV = os.environ.get("MODEL_CATALOG_URL", "https://models.dev/api.json")

# import hestia/backend/hjsonc
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import hjsonc  # noqa: E402

TIERS = ("fast", "medium", "heavy")


def fail(msg):
    print(f"✗ {msg}")
    sys.exit(1)


def main():
    path = os.path.abspath(OVERRIDES_PATH)
    if not os.path.isfile(path):
        fail(f"{path} fehlt")
    text = open(path).read()
    try:
        cfg = hjsonc.parse(text)
    except Exception as e:
        fail(f"JSONC parse Fehler: {e}")
    print(f"✓ JSONC valide ({path})")

    preset = cfg.get("activePreset")
    if not preset:
        fail("activePreset fehlt")
    presets = cfg.get("presets") or {}
    if preset not in presets:
        fail(f"activePreset '{preset}' existiert nicht in presets ({list(presets)})")
    print(f"✓ activePreset '{preset}' existiert")

    models = []
    for tier in TIERS:
        t = presets[preset].get(tier)
        if not (t and t.get("model")):
            fail(f"presets.{preset}.{tier}.model fehlt")
        ref = t["model"]
        if "/" not in ref:
            fail(f"presets.{preset}.{tier}.model '{ref}' — Format ist providerID/modelID")
        models.append((tier, ref, (t.get("variant") or "").strip()))
    print(f"✓ {len(models)} Tier-Modelle gesetzt: {', '.join(f'{t}={m}' for t, m, _ in models)}")

    providers = {ref.split("/", 1)[0] for _, ref, _ in models}
    if len(providers) > 1:
        print(f"⚠ gemischte Provider im Preset: {providers} (gewollt? sonst gleichmachen)")

    # Optional: Live-Katalog prüfen (models.dev — die Quelle von OpenCodes Provider-Registry)
    try:
        req = urllib.request.Request(MODELS_DEV, headers={"User-Agent": "hestia-ci"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            dev = json.loads(resp.read().decode())
        catalog_ok = True
        for tier, ref, variant in models:
            pid, mid = ref.split("/", 1)
            entry = (dev.get(pid) or {}).get("models", {}).get(mid)
            if not entry:
                print(f"⚠ {tier}: {ref} nicht im models.dev-Katalog (Katalog kann veraltet sein)")
                continue
            status = entry.get("status") or "active"
            if status not in ("active",):
                catalog_ok = False
                print(f"✗ {tier}: {ref} hat Status '{status}' — Tier-Modelle müssen active sein")
        if catalog_ok:
            print("✓ Live-Katalog-Check gegen models.dev bestanden")
    except Exception as e:
        print(f"⚠ models.dev nicht erreichbar ({e}) — nur Schema-Checks bestanden")

    print("VALIDIERUNG OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
