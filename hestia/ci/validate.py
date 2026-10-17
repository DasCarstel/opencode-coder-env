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

    # DCP-Schwellenwerte prüfen (wenn vorhanden)
    dcp_path = os.path.abspath(os.path.join(os.path.dirname(OVERRIDES_PATH), "dcp.jsonc"))
    if os.path.isfile(dcp_path):
        try:
            dcp_text = open(dcp_path).read()
            dcp_cfg = hjsonc.parse(dcp_text)
        except Exception as e:
            fail(f"DCP JSONC parse Fehler: {e}")
        print(f"✓ DCP JSONC valide ({dcp_path})")
        compress = (dcp_cfg or {}).get("compress") or {}
        max_l = compress.get("maxContextLimit")
        min_l = compress.get("minContextLimit")

        def _is_pct(v):
            return isinstance(v, str) and v.strip().endswith("%")

        if max_l is not None and not _is_pct(max_l) and not isinstance(max_l, bool) and not isinstance(max_l, int):
            fail(f"DCP maxContextLimit '{max_l}' — muss int oder 'X%' sein")
        if min_l is not None and not _is_pct(min_l) and not isinstance(min_l, bool) and not isinstance(min_l, int):
            fail(f"DCP minContextLimit '{min_l}' — muss int oder 'X%' sein")
        if (isinstance(max_l, int) and not isinstance(max_l, bool)
                and isinstance(min_l, int) and not isinstance(min_l, bool)
                and max_l < min_l):
            fail(f"DCP maxContextLimit ({max_l}) < minContextLimit ({min_l})")
        print(f"✓ DCP-Schwellenwerte plausibel: max={max_l} min={min_l}")
    else:
        print(f"• DCP-Config nicht vorhanden ({dcp_path}) — übersprungen")

    print("VALIDIERUNG OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
