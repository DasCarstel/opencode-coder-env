#!/usr/bin/env python3
"""Regressionstests für hestia/backend/usage.py — komplett ohne Netz.

Abgedeckt: Aggregation der Sitzungen je Modell, Fenstergrenzen (5 h / 7 Tage /
30 Tage), Ampel-Prozente gegen die Budgets aus gobudget, Schwellen (ok/warn/
gesperrt), Modelle ohne Limit (Kostenzeile) und Gratis-Modelle, Sortierung und
Auszeichnung der Prompt-Zeilen sowie Ausfallsicherheit und Cache.

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_usage.py
    python3 hestia/ci/test_usage.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import catalog  # noqa: E402
import github_api  # noqa: E402
import gobudget  # noqa: E402
import usage  # noqa: E402

fails = []
calls = {"n": 0}

NOW = int(time.time() * 1000)
H = 3600 * 1000
D = 24 * H

SOT = {
    "budgets": {
        "provider": "opencode-go",
        "plan": "Go",
        "source": "s",
        "checked": "2026-10-15",
        "windows": {"h5": 0.20, "week": 0.50, "month": 1.00},
        "models": {
            "claude-haiku-5-5": 15,       # 5 h = $3
            "deepseek-v4.1-flash": 80,    # 5 h = $16
            "cat-jam-free": "unlimited",
        },
        "peak": {},
    }
}


def check(label, cond, extra=""):
    mark = "ok  " if cond else "FAIL"
    print(f"  {mark} {label}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(label)


def session(provider, model, cost, age_ms, tokens=1000, variant="high"):
    return {
        "id": f"ses_{provider}_{model}_{age_ms}",
        "model": {"providerID": provider, "id": model, "variant": variant},
        "cost": cost,
        "tokens": {"input": tokens, "output": 100, "reasoning": 50,
                   "cache": {"read": 2000, "write": 500}},
        "time": {"created": NOW - age_ms - H, "updated": NOW - age_ms},
    }


def use(payload):
    """Sitzungsquelle umbiegen (kein Netz) und Caches leeren."""
    calls["n"] = 0

    def fake_get(url, timeout=15):
        calls["n"] += 1
        if payload is None:
            raise RuntimeError("Server nicht erreichbar")
        return {"data": payload, "cursor": {"next": None}}

    catalog._get = fake_get
    usage._CACHE.update(ts=0.0, sessions=[], agg={})
    github_api.get_file = lambda path, *a, **kw: json.dumps(SOT)
    gobudget._CACHE.update(ts=0.0, data=None)


SESSIONS = [
    session("opencode-go", "claude-haiku-5-5", 1.50, 1 * H),    # 5 h: 1.50 → 50 % von $3
    session("opencode-go", "claude-haiku-5-5", 1.50, 2 * H),    # 5 h: 3.00 → 100 % (gesperrt)
    session("opencode-go", "claude-haiku-5-5", 3.00, 3 * D),    # Woche: 6.00 → 60 %
    session("opencode-go", "deepseek-v4.1-flash", 2.00, 4 * H),  # 5 h: 2.00/16 = 12,5 %
    session("opencode-go", "deepseek-v4.1-flash", 5.00, 40 * D),  # älter als 30 Tage → zählt nicht
    session("opencode", "deepseek-v4.1-flash-free", 9.99, 1 * H),  # Zen: kein Abo-Limit
    session("opencode", "space-bunny-free", 0.0, 1 * H),
]

print("== 1) Aggregation je Modell ==")
use(SESSIONS)
agg = usage.aggregate()
check("4 Modelle erkannt", len(agg) == 4, str(sorted(agg)))
haiku = agg["opencode-go/claude-haiku-5-5"]
check("Haiku: 3 Sitzungen", haiku["sessions"] == 3, str(haiku["sessions"]))
check("Haiku: 5 h = 3.00 (alte Sitzung fehlt)", round(haiku["cost"]["h5"], 4) == 3.0, str(haiku["cost"]))
check("Haiku: Woche = 6.00", round(haiku["cost"]["week"], 4) == 6.0, str(haiku["cost"]))
check("Haiku: 30 Tage = 6.00", round(haiku["cost"]["month"], 4) == 6.0, str(haiku["cost"]))
check("Tokens summiert (3×3650)", haiku["tokens"] == 3 * (1150 + 2500), str(haiku["tokens"]))
ds = agg["opencode-go/deepseek-v4.1-flash"]
check("DeepSeek: 5 h = 2.00", round(ds["cost"]["h5"], 4) == 2.0, str(ds["cost"]))
check("DeepSeek: 40 Tage alter Eintrag zählt nur dort nicht",
      round(ds["cost"]["month"], 4) == 2.0, str(ds["cost"]))
check("DeepSeek: 2 Sitzungen gezählt", ds["sessions"] == 2, str(ds["sessions"]))

print("== 2) Fenstergrenzen genau ==")
use([session("opencode-go", "deepseek-v4.1-flash", 1.0, 5 * H + 60_000),   # knapp älter als 5 h
     session("opencode-go", "deepseek-v4.1-flash", 1.0, 5 * H - 60_000),   # knapp innerhalb
     session("opencode-go", "deepseek-v4.1-flash", 1.0, 8 * D),            # knapp älter als 7 Tage
     session("opencode-go", "deepseek-v4.1-flash", 1.0, 31 * D)])          # älter als 30 Tage
c = usage.aggregate()["opencode-go/deepseek-v4.1-flash"]["cost"]
check("5 h: nur die frische Sitzung", round(c["h5"], 4) == 1.0, str(c))
check("Woche: 2 Sitzungen", round(c["week"], 4) == 2.0, str(c))
check("30 Tage: 3 Sitzungen", round(c["month"], 4) == 3.0, str(c))

print("== 3) Ampel: Schwellen ==")
use(SESSIONS)
a = usage.ampel("opencode-go/claude-haiku-5-5")
check("Haiku: 5 h = 100 %", a["pct"]["h5"] == 100.0, str(a["pct"]))
check("Haiku: Woche = 80 % (5-h-Sitzungen zählen mit)", a["pct"]["week"] == 80.0, str(a["pct"]))
check("Haiku: 30 Tage = 40 %", a["pct"]["month"] == 40.0, str(a["pct"]))
check("Haiku: engstes Fenster 5 h", a["window"] == "h5" and a["tightest"] == 100.0, str(a["window"]))
check("Haiku: Zustand gesperrt", a["state"] == "blocked", a["state"])
a = usage.ampel("opencode-go/deepseek-v4.1-flash")
check("DeepSeek: 5 h = 12,5 %", a["pct"]["h5"] == 12.5, str(a["pct"]))
check("DeepSeek: Zustand ok", a["state"] == "ok", a["state"])

use([session("opencode-go", "claude-haiku-5-5", 2.40, 1 * H)])
a = usage.ampel("opencode-go/claude-haiku-5-5")
check("80 % → warn", a["state"] == "warn" and a["tightest"] == 80.0, str(a))
use([session("opencode-go", "claude-haiku-5-5", 2.39, 1 * H)])
check("79,7 % → ok", usage.ampel("opencode-go/claude-haiku-5-5")["state"] == "ok")

print("== 4) Ohne Limit und unbegrenzt ==")
use(SESSIONS)
a = usage.ampel("opencode/deepseek-v4.1-flash-free")
check("Zen: Zustand nolimit", a["state"] == "nolimit", a["state"])
check("Zen: keine Prozente", a["pct"] == {}, str(a["pct"]))
check("Zen: Kosten trotzdem da", round(a["cost"]["h5"], 2) == 9.99, str(a["cost"]))
a = usage.ampel("opencode-go/cat-jam-free")
check("Gratis-Modell: unlimited", a["state"] == "unlimited", a["state"])
check("Gratis-Modell: keine Prozente", a["pct"] == {}, str(a["pct"]))
check("unbekanntes Modell: ohne Daten unknown",
      usage.ampel("opencode-go/gibt-es-nicht")["state"] == "unknown")
check("unbekanntes Modell: keine Sitzungen",
      usage.ampel("opencode-go/gibt-es-nicht")["sessions"] == 0)

print("== 5) Prompt-Zeilen ==")
use(SESSIONS)
refs = ["opencode-go/claude-haiku-5-5", "opencode-go/deepseek-v4.1-flash",
        "opencode/deepseek-v4.1-flash-free", "opencode/space-bunny-free",
        "opencode-go/mimo-v2.6-flash"]  # letzteres ohne Sitzungen → keine Zeile
block = usage.prompt_block(refs)
lines = block.splitlines()
check("3 Zeilen (ungenutztes Gratis-Modell fällt raus)", len(lines) == 3, block)
check("gesperrtes Modell zuerst", lines[0].startswith("- opencode-go/claude-haiku-5-5"), lines[0])
check("GESPERRT markiert", "GESPERRT" in lines[0], lines[0])
check("Prozente aller Fenster", "5 h 100 %" in lines[0] and "Woche 80 %" in lines[0]
      and "30 Tage 40 %" in lines[0], lines[0])
check("Modell ohne Sitzungen fehlt", "mimo-v2.6-flash" not in block)
check("ungenutztes Modell (Kosten 0) fehlt", "space-bunny-free" not in block)
zen = [l for l in lines if "deepseek-v4.1-flash-free" in l]
check("Zen-Modell als Kostenzeile", zen and "kein Abo-Limit" in zen[0] and "$9.99" in zen[0],
      zen[0] if zen else "—")
check("leere Auswahl → leere Zeichenkette", usage.prompt_block(["opencode-go/gibt-es-nicht"]) == "")

print("== 6) Ausfallsicherheit und Cache ==")
use(SESSIONS)
usage.aggregate(force=True)
before = calls["n"]
usage.aggregate()
check("zweiter Aufruf aus dem Cache", calls["n"] == before, f"{before} → {calls['n']}")
usage.aggregate(force=True)
check("force lädt neu", calls["n"] == before + 1, str(calls["n"]))

use(None)  # Server nicht erreichbar
check("nicht erreichbar → leere Aggregation", usage.aggregate(force=True) == {})
a = usage.ampel("opencode-go/claude-haiku-5-5")
check("ohne Daten keine Vortäuschung: unknown", a["sessions"] == 0 and a["state"] == "unknown" and a["pct"] == {}, str(a["state"]))
check("Prompt ohne Daten leer", usage.prompt_block(["opencode-go/claude-haiku-5-5"]) == "")

use([{"id": "kaputt", "model": None}, {"model": {"id": "", "providerID": "x"}}, "quatsch"])
check("kaputte Einträge ignoriert", usage.aggregate(force=True) == {}, str(usage.aggregate()))

use([{"model": {"providerID": "opencode-go", "id": "claude-haiku-5-5"},
      "cost": "unsinn", "tokens": {"input": None, "cache": None},
      "time": {"updated": None}}])
a = usage.ampel("opencode-go/claude-haiku-5-5")
check("ungültige Zahlen → 0 statt Absturz", a["cost"]["h5"] == 0.0 and a["sessions"] == 1, str(a))

print()
print(f"FEHLGESCHLAGEN: {len(fails)}" + (f" -> {fails}" if fails else ""))
sys.exit(1 if fails else 0)
