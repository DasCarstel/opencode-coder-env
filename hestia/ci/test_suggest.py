#!/usr/bin/env python3
"""Regressionstests für hestia/backend/suggest.py — komplett ohne Netz.

Abgedeckt (P1–P3 der Ausweich-/Validation-Umrüstung):
  a) _validate_decision/_clean_choice: ungültige Modelle/Varianten (inkl.
     heavy-"fallback") werden verworfen und nur als "warnings" gemeldet.
  b) _benchmarks_block: Benchmarks nicht verfügbar → DREI Rückgabewerte
     (Regression zum 2-vs-3-Werte-Entpack-Bug) und _build_prompt läuft durch.
  c) _require_model: unbekannte Variante hinter "#xhigh" → SettingsError.
  d) _build_prompt enthält die aktuellen Ausfall-Modelle (Prompt-Futter für
     das heavy-"fallback") und das erweiterte Antwort-Schema.

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_suggest.py
    python3 hestia/ci/test_suggest.py
"""
import importlib
import json
import os
import sys
import types

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

FALLBACKS_PATH = ".opencode/hestia-fallbacks.json"

fails = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# ── Fakes (kein Netz, kein GitHub, kein OpenCode-Server) ─────────────────────
# catalog.usable liefert (ok, res|reason); res enthält bei ok die "variants" —
# genau die Quelle, gegen die _clean_choice/_require_model prüfen.
CATALOG = {}

GITHUB_FILES = {}


def _fake_catalog():
    c = types.ModuleType("catalog")

    def usable(ref):
        m = CATALOG.get(ref)
        if m is None:
            return False, f"Modell {ref} nicht im Katalog"
        if m.get("reject"):
            return False, m["reject"]
        return True, m

    def fetch_models():
        out = []
        for ref, m in CATALOG.items():
            if m.get("reject"):
                continue
            out.append({
                "ref": ref, "name": m.get("name") or ref,
                "modelID": ref.split("/", 1)[-1],
                "providerID": ref.split("/", 1)[0],
                "status": "active", "enabled": True,
                "variants": list(m.get("variants") or []),
                "cost": m.get("cost") or {},
                "costTiers": [], "costEffective": m.get("cost") or {},
                "limit": {},
            })
        return out

    c.usable = usable
    c.fetch_models = fetch_models
    c.is_free = lambda ref: not (CATALOG.get(ref, {}).get("cost") or {}).get("input") \
        and not (CATALOG.get(ref, {}).get("cost") or {}).get("output")
    return c


def _fake_github():
    gh = types.ModuleType("github_api")
    gh.OVERRIDES_PATH = ".opencode/opencode-model-router.overrides.jsonc"
    gh.get_file = lambda path: GITHUB_FILES.get(path)
    gh.get_overrides = lambda: {"config": {"presets": {"hestia": {}}}}
    gh.put_files = lambda files, msg: {"sha": "deadbeefcafe"}
    return gh


for _name, _mod in (("github_api", _fake_github()), ("catalog", _fake_catalog()),
                    ("accounts", types.ModuleType("accounts")),
                    ("usage", types.ModuleType("usage")),
                    ("gobudget", types.ModuleType("gobudget"))):
    sys.modules[_name] = _mod


suggest_mod = importlib.import_module("suggest")


def reset_catalog():
    CATALOG.clear()


def _settings_error(ref):
    """_require_model bzw. SettingsError-Nachricht für einen Ref abfragen."""
    try:
        suggest_mod._require_model(ref)
    except suggest_mod.SettingsError as e:
        return str(e)
    return ""  # kein Fehler


def set_catalog(*entries):
    """(ref, variants[, cost])-Tupel eintragen."""
    for e in entries:
        ref, variants = e[0], e[1]
        CATALOG[ref] = {"variants": list(variants), "name": ref,
                        "cost": (e[2] if len(e) > 2 else {})}


# ── a) _validate_decision / _clean_choice ────────────────────────────────────
print("\n[a1] heavy-fallback gültig → bleibt erhalten")
set_catalog(
    ("opencode-go/deepseek-v4.1-flash", ["low", "medium", "high"]),
    ("opencode/cat-jam-free", ["high"]),
    ("opencode-go/glm-5.3", ["low", "high", "max"]),
    ("opencode-go/qwen3.8-max", ["max"], {"input": 0.5}),
)
d = {
    "reasoning": "test",
    "fast": {"paid": {"model": "opencode-go/deepseek-v4.1-flash", "variant": "low", "reason": "r"},
             "free": {"model": "opencode/cat-jam-free", "variant": "high", "reason": "r"}},
    "medium": {"paid": {"model": "opencode-go/deepseek-v4.1-flash", "variant": "high", "reason": "r"},
               "free": {"model": "opencode/cat-jam-free", "variant": "high", "reason": "r"}},
    "heavy": {"paid": {"model": "opencode-go/glm-5.3", "variant": "max", "reason": "r"},
              "fallback": {"model": "opencode-go/qwen3.8-max", "variant": "max", "reason": "r"}},
}
out = suggest_mod._validate_decision(json.loads(json.dumps(d)))
check("keine warnings", "warnings" not in out, str(out.get("warnings")))
check("heavy.fallback erhalten",
      out["heavy"]["fallback"]["model"] == "opencode-go/qwen3.8-max"
      and out["heavy"]["fallback"]["variant"] == "max", str(out["heavy"]))
check("heavy.free existiert nicht mehr",
      out["heavy"].get("free") is None, str(out["heavy"].keys()))
check("reasoning weitergereicht", out["reasoning"] == "test", str(out.get("reasoning")))

print("\n[a2] fallback-Modell nicht im Katalog → verworfen + warning")
d2 = json.loads(json.dumps(d))
d2["heavy"]["fallback"] = {"model": "opencode-go/phantom-modell", "variant": "", "reason": "r"}
out = suggest_mod._validate_decision(d2)
check("heavy.fallback verworfen", "fallback" not in out["heavy"], str(out["heavy"]))
check("warning für heavy.fallback",
      any(w.startswith("heavy.fallback verworfen") for w in out.get("warnings")),
      str(out.get("warnings")))
check("heavy.paid blieb erhalten", out["heavy"]["paid"]["model"] == "opencode-go/glm-5.3",
      str(out["heavy"]))

print("\n[a3] fallback-Variante nicht live → verworfen + warning (xhigh-Fall)")
d3 = json.loads(json.dumps(d))
d3["heavy"]["fallback"] = {"model": "opencode-go/glm-5.3", "variant": "xhigh", "reason": "r"}
out = suggest_mod._validate_decision(d3)
check("heavy.fallback verworfen", "fallback" not in out["heavy"], str(out["heavy"]))
check("warning nennt xhigh",
      any("xhigh" in w for w in out.get("warnings")), str(out.get("warnings")))

print("\n[a4] fast/medium: ungültiges Modell/Variante → verworfen + warning")
d4 = json.loads(json.dumps(d))
d4["fast"]["paid"] = {"model": "opencode-go/phantom-modell", "variant": "", "reason": "r"}
d4["medium"]["free"] = {"model": "opencode/cat-jam-free", "variant": "xhigh", "reason": "r"}
out = suggest_mod._validate_decision(d4)
check("fast.paid verworfen", "paid" not in out["fast"], str(out["fast"]))
check("medium.free verworfen", "free" not in out["medium"], str(out["medium"]))
check("beide warnings vorhanden",
      any(w.startswith("fast.paid") for w in out.get("warnings"))
      and any(w.startswith("medium.free") for w in out.get("warnings")),
      str(out.get("warnings")))

# ── b) _benchmarks_block degradation ──────────────────────────────────────────
print("\n[b1] Benchmarks nicht verfügbar → drei Werte statt ValueError")
bad = types.ModuleType("benchmarks")


def _boom(*a, **k):
    raise RuntimeError("Benchmarks offline")


bad.read_settings = _boom
sys.modules["benchmarks"] = bad
sys.modules.pop("suggest", None)
suggest_mod = importlib.import_module("suggest")
rows, meta, effort = suggest_mod._benchmarks_block([{"ref": "opencode-go/glm-5.3"}])
check("drei Rückgabewerte", (rows, meta, effort) == ({}, None, {}), str((rows, meta, effort)))

print("\n[b2] _build_prompt läuft ohne Benchmarks durch (Degradation)")
reset_catalog()
set_catalog(
    ("opencode-go/deepseek-v4.1-flash", ["low", "medium", "high"],
     {"input": 0.1, "output": 0.2, "cacheRead": 0.0, "cacheWrite": 0.1}),
    ("opencode/cat-jam-free", [], {}),
)
GITHUB_FILES[FALLBACKS_PATH] = json.dumps({
    "fast": {"model": "opencode-go/deepseek-v4.1-flash", "variant": ""},
    "medium": {"model": "opencode-go/glm-5.3-flash", "variant": ""},
    "heavy": {"model": "opencode-go/qwen3.8-max", "variant": "xhigh"},
})
prompt = suggest_mod._build_prompt()
check("Prompt erzeugt (kein ValueError)", isinstance(prompt, str) and len(prompt) > 500,
      f"len={len(prompt) if isinstance(prompt, str) else prompt}")
check("Fallback-Block im Prompt", "AKTUELLE AUSFALL-MODELLE:" in prompt,
      prompt[prompt.find("AKTUELLE WERTE"):][:200] if "AKTUELLE WERTE" in prompt else "")
check("heavy-Ausfall-Modell im Prompt", "qwen3.8-max" in prompt, "")
check("Schema enthält heavy-fallback", '"fallback"' in prompt, "")

# ── c) _require_model ─────────────────────────────────────────────────────────
print("\n[c1] _require_model: unbekannte Variante → SettingsError")
reset_catalog()
set_catalog(("opencode/space-bunny-free", ["low", "high"]))
try:
    suggest_mod._require_model("opencode/space-bunny-free#xhigh")
    check("SettingsError geworfen", False, "kein Fehler")
except suggest_mod.SettingsError as e:
    check("SettingsError geworfen", True, str(e))
    check("Meldung nennt Variante und Modell", "xhigh" in str(e)
          and "space-bunny-free" in str(e), str(e))
check("gültige Variante bleibt", suggest_mod._require_model(
    "opencode/space-bunny-free#high") == "opencode/space-bunny-free#high", "")
check("leer bleibt leer", suggest_mod._require_model("") == "", "")
err = _settings_error("opencode/phantom#high")
check("unbekanntes Modell → SettingsError", "nicht im Katalog" in err, err)

print()
print(f"FEHLGESCHLAGEN: {len(fails)}" + (f" -> {fails}" if fails else ""))
sys.exit(1 if fails else 0)
