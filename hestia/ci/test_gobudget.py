#!/usr/bin/env python3
"""Regressionstests für hestia/backend/gobudget.py — komplett ohne Netz.

Abgedeckt: Lesen des `budgets`-Blocks aus der SOT (inkl. Defaults bei fehlender
oder kaputter Datei), Monatslimits und abgeleitete Fenster (5 h = 20 %, Woche =
50 %), Gratis-Modelle ("unlimited"), Provider-Abgrenzung (Go-Abo vs. Zen),
Peak-Fenster (UTC, Mo–Fr, inkl. Grenzen), Provider-Abgrenzung der Peak-Regel und
der gültige Preis inkl. Kontextstufe und Zeitfaktor.

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_gobudget.py
    python3 hestia/ci/test_gobudget.py
"""
import datetime
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import github_api  # noqa: E402
import gobudget  # noqa: E402

fails = []
reads = {"n": 0}


def check(label, cond, extra=""):
    mark = "ok  " if cond else "FAIL"
    print(f"  {mark} {label}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(label)


SOT = {
    "enabled": True,
    "budgets": {
        "provider": "opencode-go",
        "plan": "Go",
        "source": "https://opencode.ai/v2/docs/console/go",
        "checked": "2026-10-15",
        "windows": {"h5": 0.20, "week": 0.50, "month": 1.00},
        "models": {
            "claude-haiku-5-5": 15,
            "deepseek-v4.1-flash": 80,
            "cat-jam-free": "unlimited",
        },
        "peak": {
            "deepseek-v4.1-flash": {"factor": 2, "windows": ["01:00-04:00", "06:00-10:00"],
                                    "days": "Mon-Fri", "tz": "UTC"},
        },
    },
}


def use_sot(payload=None, raw_text=None):
    """SOT-Quelle umbiegen (kein Netz) und Caches leeren."""
    reads["n"] = 0

    def fake_get_file(path, *a, **kw):
        reads["n"] += 1
        if raw_text is not None:
            return raw_text
        if payload is None:
            raise RuntimeError("SOT nicht lesbar")
        return json.dumps(payload)

    github_api.get_file = fake_get_file
    gobudget._CACHE.update(ts=0.0, data=None)


def when(y, mo, d, h, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=datetime.timezone.utc)


# 2026-10-15 ist ein Donnerstag, 2026-10-17 ein Samstag.
DON_PEAK = when(2026, 10, 15, 2, 30)
DON_TAG = when(2026, 10, 15, 12, 0)
SAM_PEAK = when(2026, 10, 17, 2, 30)

print("== 1) Budget-Block lesen ==")
use_sot(SOT)
cfg = gobudget.settings(force=True)
check("Provider", cfg["provider"] == "opencode-go", cfg["provider"])
check("Plan", cfg["plan"] == "Go", cfg["plan"])
check("Quelle übernommen", cfg["source"].endswith("/console/go"), cfg["source"])
check("Prüfdatum übernommen", cfg["checked"] == "2026-10-15", cfg["checked"])
check("Fenster", cfg["windows"] == {"h5": 0.20, "week": 0.50, "month": 1.00}, str(cfg["windows"]))
check("3 Modelle", len(cfg["models"]) == 3, str(cfg["models"]))
check("2 Peak-Regeln", len(cfg["peak"]) == 1, str(cfg["peak"]))

print("== 2) Monatslimit und abgeleitete Fenster ==")
b = gobudget.budget_for("opencode-go/claude-haiku-5-5")
check("Haiku: 15/Monat", b and b["month"] == 15.0, str(b))
check("Haiku: 5 h = 20 %", b and b["h5"] == 3.0, str(b))
check("Haiku: Woche = 50 %", b and b["week"] == 7.5, str(b))
b = gobudget.budget_for("opencode-go/deepseek-v4.1-flash")
check("DeepSeek: 80/Monat", b and b["month"] == 80.0, str(b))
check("DeepSeek: 5 h = 16", b and b["h5"] == 16.0, str(b))

print("== 3) Gratis-Modelle, unbekannte Modelle, fremder Provider ==")
b = gobudget.budget_for("opencode-go/cat-jam-free")
check("Gratis-Modell unbegrenzt", b and b.get("unlimited") is True, str(b))
check("Gratis-Modell ohne Zahl", b and b["month"] is None, str(b))
check("unbekanntes Modell → kein Budget", gobudget.budget_for("opencode-go/gibt-es-nicht") is None)
check("anderer Provider → kein Budget", gobudget.budget_for("opencode/claude-haiku-5-5") is None)
check("ohne Provider-Präfix → kein Budget", gobudget.budget_for("claude-haiku-5-5") is None)

print("== 4) Peak-Fenster (UTC, Mo-Fr) ==")
check("Do 02:30 → Peak", gobudget.is_peak("deepseek-v4.1-flash", DON_PEAK, "opencode-go") is True)
check("Do 01:00 → Peak (linke Grenze)", gobudget.is_peak("deepseek-v4.1-flash", when(2026, 10, 15, 1, 0), "opencode-go") is True)
check("Do 03:59 → Peak", gobudget.is_peak("deepseek-v4.1-flash", when(2026, 10, 15, 3, 59), "opencode-go") is True)
check("Do 04:00 → Off-Peak (rechte Grenze)", gobudget.is_peak("deepseek-v4.1-flash", when(2026, 10, 15, 4, 0), "opencode-go") is False)
check("Do 05:59 → Off-Peak (Lücke)", gobudget.is_peak("deepseek-v4.1-flash", when(2026, 10, 15, 5, 59), "opencode-go") is False)
check("Do 06:00 → Peak", gobudget.is_peak("deepseek-v4.1-flash", when(2026, 10, 15, 6, 0), "opencode-go") is True)
check("Do 09:59 → Peak", gobudget.is_peak("deepseek-v4.1-flash", when(2026, 10, 15, 9, 59), "opencode-go") is True)
check("Do 10:00 → Off-Peak", gobudget.is_peak("deepseek-v4.1-flash", when(2026, 10, 15, 10, 0), "opencode-go") is False)
check("Do 12:00 → Off-Peak", gobudget.is_peak("deepseek-v4.1-flash", DON_TAG, "opencode-go") is False)
check("Sa 02:30 → Off-Peak", gobudget.is_peak("deepseek-v4.1-flash", SAM_PEAK, "opencode-go") is False)
check("Modell ohne Regel → kein Peak", gobudget.is_peak("claude-haiku-5-5", DON_PEAK, "opencode-go") is False)
check("Zen-Provider → keine Peak-Regel", gobudget.peak_rule("deepseek-v4.1-flash", "opencode") == {})
check("Go-Provider → Peak-Regel", bool(gobudget.peak_rule("deepseek-v4.1-flash", "opencode-go")))

print("== 5) Gültiger Preis: Tarifstufe + Zeitfaktor ==")
haiku = {
    "ref": "opencode-go/claude-haiku-5-5", "modelID": "claude-haiku-5-5", "providerID": "opencode-go",
    "cost": {"input": 0.10, "output": 0.50, "cacheRead": 0.01, "cacheWrite": 0.125},
    "costTiers": [{"from": 100000, "input": 0.50, "output": 2.50,
                   "cacheRead": 0.05, "cacheWrite": 0.625}],
    "costEffective": {"from": 100000, "input": 0.50, "output": 2.50,
                      "cacheRead": 0.05, "cacheWrite": 0.625},
}
p = gobudget.price_for(haiku, DON_PEAK)
check("Haiku: gültiger Tarif (>100K)", (p["input"], p["output"]) == (0.50, 2.50), str(p))
check("Haiku: Stufengrenze mitgeliefert", p["from"] == 100000, str(p))
check("Haiku: kein Zeitfaktor", p["peak"] is False and p["factor"] == 1, str(p))

ds = {
    "ref": "opencode-go/deepseek-v4.1-flash", "modelID": "deepseek-v4.1-flash",
    "providerID": "opencode-go",
    "cost": {"input": 0.15, "output": 0.60, "cacheRead": 0.003, "cacheWrite": 0},
    "costTiers": [],
    "costEffective": {"from": None, "input": 0.15, "output": 0.60,
                      "cacheRead": 0.003, "cacheWrite": 0},
}
p = gobudget.price_for(ds, DON_PEAK)
check("DeepSeek Peak: doppelter Preis", (p["input"], p["output"]) == (0.30, 1.20), str(p))
check("DeepSeek Peak: Faktor 2 markiert", p["peak"] is True and p["factor"] == 2, str(p))
p = gobudget.price_for(ds, DON_TAG)
check("DeepSeek Off-Peak: Listenpreis", (p["input"], p["output"]) == (0.15, 0.60), str(p))
check("DeepSeek Off-Peak: unmarkiert", p["peak"] is False, str(p))

zen = dict(ds, ref="opencode/deepseek-v4.1-flash", providerID="opencode",
           cost={"input": 0.30, "output": 1.20, "cacheRead": 0.006, "cacheWrite": 0},
           costEffective={"from": None, "input": 0.30, "output": 1.20,
                          "cacheRead": 0.006, "cacheWrite": 0})
p = gobudget.price_for(zen, DON_PEAK)
check("Zen-Modell: KEIN Zeitfaktor", (p["input"], p["output"]) == (0.30, 1.20), str(p))
check("leeres Modell → {}", gobudget.price_for({}, DON_PEAK)["input"] is None)
check("None → {}", gobudget.price_for(None, DON_PEAK) == {})

print("== 6) Cache und Ausfallsicherheit ==")
use_sot(SOT)
gobudget.settings(force=True)
before = reads["n"]
gobudget.settings()
check("zweiter Aufruf kommt aus dem Cache", reads["n"] == before, f"{before} → {reads['n']}")
gobudget.settings(force=True)
check("force liest neu", reads["n"] == before + 1, str(reads["n"]))

use_sot(payload=None)  # wirft beim Lesen
cfg = gobudget.settings(force=True)
check("nicht lesbare SOT → Defaults", cfg["models"] == {} and cfg["provider"] == "opencode-go", str(cfg))
check("kein Budget bei Ausfall", gobudget.budget_for("opencode-go/claude-haiku-5-5") is None)
check("kein Peak bei Ausfall", gobudget.is_peak("deepseek-v4.1-flash", DON_PEAK) is False)

use_sot(raw_text="{ das ist kein JSONC }")
cfg = gobudget.settings(force=True)
check("kaputte SOT → Defaults (kein Absturz)", cfg["models"] == {}, str(cfg))

use_sot({"budgets": {"models": {"x": "unsinn", "y": 15}, "peak": "kaputt"}})
cfg = gobudget.settings(force=True)
check("ungültige Modellwerte verworfen", cfg["models"] == {"y": 15.0}, str(cfg["models"]))
check("kaputtes peak ignoriert", cfg["peak"] == {}, str(cfg["peak"]))
check("Plan fällt auf Default zurück", cfg["plan"] == "Go", cfg["plan"])

use_sot({"budgets": {"provider": "opencode-go", "models": {"z": 60}}})
cfg = gobudget.settings(force=True)
check("eigene Fenster fehlen → Doku-Defaults",
      cfg["windows"] == {"h5": 0.20, "week": 0.50, "month": 1.00}, str(cfg["windows"]))
check("Wert 60 → h5 12", gobudget.budget_for("opencode-go/z")["h5"] == 12.0)

# Repo-SOT gegenprüfen (nur wenn vorhanden): echte Werte aus dem Budget-Block.
sot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                        ".opencode", "hestia-benchmarks.jsonc")
if os.path.exists(sot_path):
    print("== 7) Echte Repo-SOT ==")
    text = open(sot_path, encoding="utf-8").read()
    use_sot(raw_text=text)
    cfg = gobudget.settings(force=True)
    check("Repo-SOT lesbar", cfg["provider"] == "opencode-go" and cfg["checked"], str(cfg["checked"]))
    check("Repo-SOT: Haiku 15", (gobudget.budget_for("opencode-go/claude-haiku-5-5") or {}).get("month") == 15.0)
    check("Repo-SOT: DeepSeek Flash 80", (gobudget.budget_for("opencode-go/deepseek-v4.1-flash") or {}).get("month") == 80.0)
    check("Repo-SOT: DeepSeek Pro 15", (gobudget.budget_for("opencode-go/deepseek-v4-pro") or {}).get("month") == 15.0)
    check("Repo-SOT: Gratis unbegrenzt",
          (gobudget.budget_for("opencode-go/step-5-preview-free") or {}).get("unlimited") is True)
    check("Repo-SOT: alle Modelle haben einen Wert",
          all(isinstance(v, (int, float, str)) for v in cfg["models"].values()), str(cfg["models"]))
    check("Repo-SOT: Peak nur für DeepSeek", set(cfg["peak"]) == {"deepseek-v4.1-flash", "deepseek-v4-pro"},
          str(sorted(cfg["peak"])))

print()
print(f"FEHLGESCHLAGEN: {len(fails)}" + (f" -> {fails}" if fails else ""))
sys.exit(1 if fails else 0)
