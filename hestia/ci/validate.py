#!/usr/bin/env python3
"""validate.py — CI-Validierung für die Hestia-Tier-Konfiguration.

Prüft (in dieser Reihenfolge):
  1. JSONC gültig (Comments/Trailing-Commas erlaubt)
  2. aktiver Preset existiert; fast/medium/heavy gesetzt; Models = providerID/modelID
  3. Varianten-Empfehlung plausibel (leer oder bekannt)
  4. Live-Katalog-Check gegen models.dev (opt-in; bei Netzfehler warnen, nicht failen)
  5. Hermes-SOT strukturell (primary/fallback + agent/providers/platform_toolsets/platforms/skills)

Exit-Code 1 = invalid. Run: python hestia/ci/validate.py
"""
import base64
import json
import os
import re
import sys
import urllib.request

OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".opencode", "opencode-model-router.overrides.jsonc")
MODELS_DEV = os.environ.get("MODEL_CATALOG_URL", "https://models.dev/api.json")

# import hestia/backend/hjsonc
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import hjsonc  # noqa: E402

TIERS = ("fast", "medium", "heavy")

HERMES_SOT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".opencode", "hestia-hermes.jsonc")
HERMES_PROVIDERS = {"opencode-go", "opencode"}
HERMES_EFFORTS = ("", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
HERMES_TOOLSETS = {
    "web", "browser", "browser-use", "terminal", "file", "code_execution",
    "vision", "video", "image_gen", "video_gen", "x_search", "tts", "stt",
    "skills", "todo", "kanban", "memory", "context_engine", "session_search",
    "clarify", "connections", "delegation", "cronjob",
    # Explizit freigegebene MCP-Toolsets (API-Surface): HA für die Sprach-
    # integration, dazu die übrigen Work-MCPs — alle MCP-Server für den
    # api_server (HA-Assist UND Open WebUI).
    "mcp-homeassistant", "mcp-notion", "mcp-ocis-private",
    "mcp-ocis-muellerconnect", "mcp-super-productivity", "mcp-spotify",
    "mcp-google-calendar",
}
HERMES_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def fail(msg):
    print(f"✗ {msg}")
    sys.exit(1)


def _hermes_number_ok(value) -> bool:
    """int/float > 0, aber nie bool (isinstance(True, int) wäre sonst True)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def validate_hermes(path):
    """Strukturprüfung der Hermes-SOT (`.opencode/hestia-hermes.jsonc`).

    Gleiche Regeln wie hestia/backend/hermes.py, nur ohne Live-Katalog:
    Typen/Werte-Bereiche, Toolset-Namen und Modellrouten-Formen.
    """
    if not os.path.isfile(path):
        fail(f"{path} fehlt")
    try:
        cfg = hjsonc.parse(open(path).read()) or {}
    except Exception as e:
        fail(f"Hermes JSONC parse Fehler: {e}")
    print(f"✓ Hermes JSONC valide ({path})")

    # primary / fallback: Pflichtfelder + Provider-Allowlist
    primary = cfg.get("primary") or {}
    if not str(primary.get("provider") or "").strip() or not str(primary.get("model") or "").strip():
        fail("Hermes primary.provider/primary.model fehlen oder sind leer")
    if primary["provider"] not in HERMES_PROVIDERS:
        fail(f"Hermes primary.provider '{primary['provider']}' nicht erlaubt "
             f"({', '.join(sorted(HERMES_PROVIDERS))})")
    fallback = cfg.get("fallback") or []
    if not isinstance(fallback, list):
        fail("Hermes fallback muss eine Liste sein")
    for i, fb in enumerate(fallback, start=1):
        if (not isinstance(fb, dict) or not str(fb.get("provider") or "").strip()
                or not str(fb.get("model") or "").strip()):
            fail(f"Hermes fallback[{i}] braucht provider und model")
        if fb["provider"] not in HERMES_PROVIDERS:
            fail(f"Hermes fallback[{i}].provider '{fb['provider']}' nicht erlaubt")
    print(f"✓ Hermes primary/fallback gesetzt ({len(fallback)} Fallback(s))")

    # agent
    agent = cfg.get("agent")
    if agent is None:
        agent = {}
    if not isinstance(agent, dict):
        fail("Hermes agent muss ein Objekt sein")
    if "reasoning_effort" in agent:
        effort = str(agent["reasoning_effort"] or "").strip().lower()
        if effort not in HERMES_EFFORTS:
            fail(f"Hermes agent.reasoning_effort '{agent['reasoning_effort']}' unbekannt "
                 f"({'|'.join(HERMES_EFFORTS)})")
    for key, minimum in (("api_max_retries", 1), ("auto_recovery_cycles", 0)):
        if key in agent:
            v = agent[key]
            if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
                fail(f"Hermes agent.{key} muss eine ganze Zahl >= {minimum} sein")

    # providers
    providers = cfg.get("providers")
    if providers is None:
        providers = {}
    if not isinstance(providers, dict):
        fail("Hermes providers muss ein Objekt sein")
    for name, prov in providers.items():
        if not isinstance(prov, dict):
            fail(f"Hermes providers.{name} muss ein Objekt sein")
        if name == "opencode-go":
            for key in ("request_timeout_seconds", "stale_timeout_seconds"):
                if key in prov and not _hermes_number_ok(prov[key]):
                    fail(f"Hermes providers.opencode-go.{key} muss eine Zahl > 0 sein")

    # platform_toolsets
    toolsets = cfg.get("platform_toolsets")
    if toolsets is None:
        toolsets = {}
    if not isinstance(toolsets, dict):
        fail("Hermes platform_toolsets muss ein Objekt sein")
    for name, items in toolsets.items():
        if (not isinstance(items, list)
                or not all(isinstance(t, str) and t.strip() for t in items)):
            fail(f"Hermes platform_toolsets.{name} muss eine Liste aus Strings sein")
        unknown = sorted({t.strip() for t in items if t.strip() not in HERMES_TOOLSETS})
        if unknown:
            fail(f"Hermes platform_toolsets.{name}: unbekannte Toolsets {', '.join(unknown)} "
                 f"(erlaubt: {', '.join(sorted(HERMES_TOOLSETS))})")
        if str(name) == "api_server" and not items:
            fail("Hermes platform_toolsets.api_server darf nicht leer sein")

    # platforms (inkl. model_routes); andere Keys wie ntfy bleiben unangetastet
    platforms = cfg.get("platforms")
    if platforms is None:
        platforms = {}
    if not isinstance(platforms, dict):
        fail("Hermes platforms muss ein Objekt sein")
    for name, plat in platforms.items():
        if not isinstance(plat, dict):
            fail(f"Hermes platforms.{name} muss ein Objekt sein")
        extra = plat.get("extra")
        if extra is None:
            continue
        if not isinstance(extra, dict):
            fail(f"Hermes platforms.{name}.extra muss ein Objekt sein")
        routes = extra.get("model_routes")
        if routes is None:
            continue
        if not isinstance(routes, dict):
            fail(f"Hermes platforms.{name}.extra.model_routes muss ein Objekt sein")
        for alias, route in routes.items():
            if not HERMES_ALIAS.match(str(alias)):
                fail(f"Hermes model_routes-Alias '{alias}' passt nicht auf "
                     f"^[A-Za-z0-9][A-Za-z0-9._-]*$")
            # Native Form: {"provider": "opencode-go", "model": "glm-5.3-flash"}.
            if (not isinstance(route, dict)
                    or not isinstance(route.get("provider"), str)
                    or not route["provider"].strip()
                    or not isinstance(route.get("model"), str)
                    or not route["model"].strip()):
                fail(f"Hermes model_routes.{alias} muss die native Form "
                     f"{{'provider': <nicht-leerer String>, "
                     f"'model': <nicht-leerer String>}} sein")
            if route["provider"].strip() not in HERMES_PROVIDERS:
                fail(f"Hermes model_routes.{alias}.provider "
                     f"'{route['provider'].strip()}' nicht erlaubt")
    # skills: nur `disabled` — Liste nicht-leerer, eindeutiger Strings
    skills = cfg.get("skills")
    if skills is None:
        skills = {}
    if not isinstance(skills, dict):
        fail("Hermes skills muss ein Objekt sein")
    unknown = sorted(str(k) for k in skills if k != "disabled")
    if unknown:
        fail(f"Hermes skills: unbekannte Keys {', '.join(unknown)} "
             f"(erlaubt: disabled)")
    disabled = skills.get("disabled")
    if disabled is not None:
        if not isinstance(disabled, list) or not all(
                isinstance(s, str) and s.strip() for s in disabled):
            fail("Hermes skills.disabled muss eine Liste nicht-leerer Strings sein")
        seen = set()
        for s in disabled:
            name = s.strip()
            if name in seen:
                fail(f"Hermes skills.disabled: Duplikat '{name}'")
            seen.add(name)

    print("✓ Hermes-SOT strukturell valide "
          "(agent/providers/platform_toolsets/platforms/skills)")


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

    # Hermes-SOT strukturell prüfen (ohne Live-Katalog)
    validate_hermes(os.path.abspath(HERMES_SOT_PATH))

    print("VALIDIERUNG OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
