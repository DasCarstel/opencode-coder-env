#!/usr/bin/env python3
"""P2-Test: switch_account() / _apply_policy() mit GESTUBBTEM github_api.

Dispatch wird komplett abgefangen — es wird nichts wirklich ausgelöst.
Läuft ohne Netz/Container: python3 hestia/ci/test_switch_account.py
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import accounts  # noqa: E402
import github_api  # noqa: E402

# GitHub-Werte EXPLIZIT setzen (Muster wie das patching in test_benchmarks.py):
# github_api liest GITHUB_REF/GITHUB_REPO beim Import aus der Umgebung. Auf einem
# GitHub-Actions-Runner ist GITHUB_REF zwingend "refs/heads/<branch>" — der Test
# erwartet aber den reinen Branch-Namen ("master"), weil die Contents-/
# Dispatch-API nur den Kurz-Namen akzeptiert. Ohne dieses Pinning schlug der Test
# auf CI fehl, lokal (ohne die Variablen) war er grün.
os.environ["GITHUB_REF"] = "master"
os.environ["GITHUB_REPO"] = "DasCarstel/opencode-coder-env"
os.environ.pop("GITHUB_REPOSITORY", None)
github_api.GITHUB_REF = "master"
github_api.GITHUB_REPO = "DasCarstel/opencode-coder-env"

CFG = {
    "active": "default1",
    "policy": {"mode": "auto", "cooldownSeconds": 1800},
    "accounts": [{"id": "default1"}, {"id": "default2"}, {"id": "default3"}],
    "providers": {},
}
CALLS = []
SOT_WRITES = []

# Sicherheitsnetz wie in test_benchmarks.py: NIE den Produktionspfad
# (/app/data bzw. .opencode/hestia-accounts.jsonc im Repo) anfasen. Die SOT
# liegt produktiv nur in GitHub, aber Accounts/Hermes laden sie über einen
# konfigurierbaren Pfad — ein vergessener Stub würde sonst im Repo schreiben.
_TMP = tempfile.mkdtemp(prefix="hestia-switch-tests-")
accounts.ACCOUNTS_PATH = os.path.join(_TMP, "hestia-accounts.jsonc")


# Rohtext statt hjsonc.dumps (das gibt es nicht) — so bleibt der Weg über die
# Regex in _set_sot_active() realistisch inkl. Kommentarzeilen.
SOT_RAW = """{
  // Quelle der Wahrheit fuer das aktive OpenCode-Go-Konto.
  "active": "default1",
  "policy": {"mode": "auto", "cooldownSeconds": 1800},
  "accounts": [{"id": "default1"}, {"id": "default2"}, {"id": "default3"}],
  "providers": {}
}
"""


def fake_get_file(path, *a, **kw):
    """SOT-Lesen stubben (wird von _config() UND _set_sot_active() benutzt)."""
    return SOT_RAW


def fake_put_files(files, msg, *a, **kw):
    """SOT-Schreiben abfangen — kein Commit, kein Netz, keine Schreibzugriffe."""
    SOT_WRITES.append({"files": files, "msg": msg})
    return {"commit": "deadbeef"}


def fake_dispatch(workflow, ref=None, inputs=None):
    CALLS.append({"workflow": workflow, "ref": ref, "inputs": inputs})
    return {"dispatched": True, "workflow": workflow, "ref": ref or "master"}


# Original-Funktion VOR dem Patch festhalten (accounts.github_api ist dasselbe
# Modulobjekt wie github_api — der Patch wirkt sonst global).
real_dispatch_workflow = github_api.dispatch_workflow
accounts._config = lambda force=False: CFG
accounts.github_api.get_file = fake_get_file
accounts.github_api.put_files = fake_put_files
accounts.github_api.dispatch_workflow = fake_dispatch


def reset():
    """Zustand UND die Aufzeichnungen der Stubs zurücksetzen."""
    CALLS.clear()
    SOT_WRITES.clear()


def reset():
    CALLS.clear()
    accounts.STATE.update({
        "accounts": [
            {"id": "default1", "hasKey": True, "ok": True, "error": None},
            {"id": "default2", "hasKey": True, "ok": True, "error": None},
            {"id": "default3", "hasKey": False, "ok": False, "error": "kein Key im Env"},
        ],
        "events": [], "lastSwitch": None, "switching": False, "interval": 300,
        "mode": "auto", "recommendation": {"switchRecommended": False, "target": None, "reason": ""},
    })


fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  [{extra}]" if extra else ""))
    if not cond:
        fails.append(label)


print("== 1) unbekanntes Ziel ==")
reset()
r = accounts.switch_account("gibtsnicht")
check("switched=False", r.get("switched") is False)
check("Fehler 'unbekanntes Konto'", "unbekanntes Konto" in (r.get("error") or ""), r.get("error"))
check("kein Dispatch", CALLS == [])

print("== 2) Ziel ohne Key / nicht abfragbar ==")
reset()
r = accounts.switch_account("default3")
check("kein Key -> Fehler", "kein Key" in (r.get("error") or ""), r.get("error"))
check("kein Dispatch", CALLS == [])

print("== 3) Erfolg setzt lastSwitch + Event + Inputs ==")
reset()
before = time.time()
r = accounts.switch_account("default2", "Test", by="manual")
check("switched=True", r.get("switched") is True, str(r.get("error")))
check("target", r.get("target") == "default2")
check("by", r.get("by") == "manual")
check("dispatched im Ergebnis", r.get("dispatched", {}).get("dispatched") is True)
check("lastSwitch gesetzt", accounts.STATE["lastSwitch"] and accounts.STATE["lastSwitch"] >= before)
check("switching zurückgesetzt", accounts.STATE["switching"] is False)
check("genau 1 Dispatch", len(CALLS) == 1)
check("genau 1 SOT-Schreiben", len(SOT_WRITES) == 1)
check("commit zurückgegeben", r.get("commit") == "deadbeef", str(r.get("commit")))
# CALL_DEREF statt CALLS[0]: leerer Dispatch-Container -> "" (sauberer FAIL),
# nicht IndexError (der war der ursprüngliche Bug-Abbruch des Tests).
CALL_DEREF = CALLS[0] if CALLS else {}
check("Workflow = switch-account.yml", CALL_DEREF.get("workflow") == "switch-account.yml", str(CALL_DEREF.get("workflow")))
check("inputs korrekt", CALL_DEREF.get("inputs") == {"account": "default2", "reason": "Test"}, str(CALL_DEREF.get("inputs")))
check("SOT enthält active=default2", '"active": "default2"' in str(SOT_WRITES[0]["files"] if SOT_WRITES else ""))
ev = [e for e in accounts.STATE["events"] if e.get("type") == "switch"]
check("Event type=switch", len(ev) == 1 and ev[0]["target"] == "default2" and ev[0]["by"] == "manual")

print("== 4) Cooldown greift ==")
r2 = accounts.switch_account("default1")
check("switched=False", r2.get("switched") is False)
check("error 'Cooldown aktiv'", r2.get("error") == "Cooldown aktiv", str(r2.get("error")))
check("cooldownRemaining gesetzt", 0 < int(r2.get("cooldownRemaining") or 0) <= 1800, str(r2.get("cooldownRemaining")))
check("kein 2. Dispatch", len(CALLS) == 1)

print("== 5) force umgeht den Cooldown ==")
r3 = accounts.switch_account("default1", force=True, by="panel")
check("switched=True", r3.get("switched") is True, str(r3.get("error")))
check("2. Dispatch erfolgt", len(CALLS) == 2)

print("== 6) leeres Ziel ==")
check("kein Zielkonto", accounts.switch_account("").get("error") == "kein Zielkonto")

print("== 7) Dispatch-Fehler -> switched=False, lastSwitch unverändert ==")
reset()
saved = accounts.STATE["lastSwitch"]


def boom(*a, **kw):
    raise github_api.ConfigError("GitHub 401")


accounts.github_api.dispatch_workflow = boom
r4 = accounts.switch_account("default2")
check("switched=False", r4.get("switched") is False)
check("Fehler durchgereicht", "401" in (r4.get("error") or ""), str(r4.get("error")))
check("lastSwitch unveraendert", accounts.STATE["lastSwitch"] == saved)
check("switching zurückgesetzt", accounts.STATE["switching"] is False)
accounts.github_api.dispatch_workflow = fake_dispatch

print("== 8) _apply_policy: auto / notify / off ==")
# auto → wechselt
reset()
accounts.STATE.update({"mode": "auto", "recommendation": {"switchRecommended": True, "target": "default2", "reason": "aktiv erschöpft"}})
accounts._apply_policy()
check("auto dispatcht", len(CALLS) == 1 and CALLS[0]["inputs"]["account"] == "default2")
check("by=auto", accounts.STATE["events"][-1].get("by") == "auto")

# notify → nur Event, kein Dispatch, dedupliziert
reset()
accounts.STATE.update({"mode": "notify", "recommendation": {"switchRecommended": True, "target": "default2", "reason": "Gleichgewicht"}})
accounts._apply_policy()
accounts._apply_policy()
recs = [e for e in accounts.STATE["events"] if e.get("type") == "recommendation"]
check("kein Dispatch", CALLS == [])
check("genau 1 recommendation-Event", len(recs) == 1, str(len(recs)))
check("target im Event", recs and recs[0]["target"] == "default2")

# off → nichts
reset()
accounts.STATE.update({"mode": "off", "recommendation": {"switchRecommended": True, "target": "default2", "reason": "x"}})
accounts._apply_policy()
check("off: kein Dispatch", CALLS == [])
check("off: kein Event", accounts.STATE["events"] == [])

print("== 9) dispatch_workflow: inputs im Body ==")
bodies = []
github_api._req = lambda method, url, body=None: (bodies.append((method, url, body)), (204, {}))[1]
res = real_dispatch_workflow("switch-account.yml", inputs={"account": "default2", "reason": "r"})
check("dispatched", res.get("dispatched") is True)
check("POST", bodies[0][0] == "POST")
check("Body mit ref+inputs", bodies[0][2] == {"ref": "master", "inputs": {"account": "default2", "reason": "r"}}, str(bodies[0][2]))
bodies.clear()
real_dispatch_workflow("rollout.yml")
check("ohne inputs kein inputs-Block", bodies[0][2] == {"ref": "master"}, str(bodies[0][2]))

print("== 10) expiring-Hysterese (Flip-Flop-Schutz) ==")
# Hintergrund (2026-10-18): Die expiring-Regel sah nur die NICHT-aktiven Konten
# an. Laufen zwei Abos gleichzeitig bald ab, zeigt jede Prüfung nach Ablauf des
# Cooldowns auf das jeweils andere — der aktive Account wechselte im
# 30-Minuten-Takt endlos hin und her (default2 <-> default3). Erwartet wird
# jetzt: solange das AKTIVE Konto selbst bald abläuft, wird es abgearbeitet.


def acc(aid, weekly_free, weekly_reset_h, weekly_status="ok",
        monthly_free=50.0, monthly_reset_h=500.0, monthly_status="ok"):
    from datetime import datetime, timedelta, timezone

    def iso(h):
        return (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat()

    return {
        "id": aid, "ok": True,
        "windows": {
            "weekly": {"free": weekly_free, "used": 100.0 - weekly_free,
                       "status": weekly_status, "resetsAt": iso(weekly_reset_h)},
            "monthly": {"free": monthly_free, "used": 100.0 - monthly_free,
                        "status": monthly_status, "resetsAt": iso(monthly_reset_h)},
        },
    }


EXP_POLICY = {
    "mode": "auto",
    "expiring": {"enabled": True, "withinHours": 24, "minRemainingPercent": 5,
                 "windows": ["weekly", "monthly"]},
    "switchWhenExhausted": True,
    "balance": {"enabled": True, "windows": ["weekly", "monthly"],
                "maxSpreadPercentPoints": 25, "minHeadroomPercent": 30},
}


def eval_cfg(active_id, accs):
    cfg = {"active": active_id, "policy": EXP_POLICY, "accounts": [], "providers": {}}
    return accounts.evaluate(cfg, accs)


# (a) BEIDE Abos laufen bald ab → kein Wechsel (das war die Endlos-Schleife).
two_expiring = [acc("default3", 80, 5), acc("default2", 90, 3)]
r = eval_cfg("default3", two_expiring)
check("a1 aktives Abo laeuft selbst ab -> kein Wechsel",
      r.get("switchRecommended") is False, str(r))
r = eval_cfg("default2", two_expiring)
check("a2 Richtung egal -> kein Wechsel",
      r.get("switchRecommended") is False, str(r))

# (b) Nur ein anderes Abo laeuft bald ab → weiterhin wechseln ("use it or lose it").
r = eval_cfg("default1", [acc("default1", 90, 300), acc("default2", 90, 3)])
check("b1 anderes Abo laeuft ab -> Wechsel", r.get("switchRecommended") is True, str(r))
check("b2 Ziel = default2", r.get("target") == "default2", str(r.get("target")))
check("b3 Grund 'bald ablaufend'", (r.get("reason") or "").startswith("bald ablaufend"), str(r.get("reason")))

# (c) Erschoepft-Regel greift weiter, auch wenn das aktive Konto 'bald ablaufend' ist.
#     default3 hat im Woche-Fenster 0 % frei (rate-limited), default2 laeuft nicht ab.
r = eval_cfg("default3", [acc("default3", 0.0, 5, weekly_status="rate-limited"),
                          acc("default2", 60, 200)])
check("c1 erschoepft -> Wechsel trotz Hysterese", r.get("switchRecommended") is True, str(r))
check("c2 Ziel = default2", r.get("target") == "default2", str(r.get("target")))
check("c3 Grund 'aktiv erschoepft'", (r.get("reason") or "").startswith("aktiv erschöpft"), str(r.get("reason")))

print("== 11) gesperrtes Abo wird nicht leergefahren ==")
# Hintergrund (2026-10-18): Das Panel zeigte "⚡ OpenCode Go #3 wird jetzt
# bevorzugt leergefahren — Reset in 10 h (Woche), noch 100 % frei", obwohl das
# Konto im Monatsfenster gesperrt war (0 % frei). Leerfahren setzt aber voraus,
# dass sich das Konto überhaupt noch nutzen lässt; ein gesperrtes Abo kann
# nichts mehr "retten".
# Aufbau 1:1 aus dem Screenshot — alle drei Konten im Monat gesperrt, die
# Wochenfenster laufen in 10 h bei 29/77/100 % frei zurueck.
shot = [
    acc("default1", 0.0, 10, weekly_status="rate-limited", monthly_free=29.0),
    acc("default2", 77.0, 10, monthly_free=0.0, monthly_status="rate-limited"),
    acc("default3", 100.0, 10, monthly_free=0.0, monthly_status="rate-limited"),
]
check("d1 gesperrtes Wochen-Fenster zaehlt nicht",
      accounts._expiring(shot[0], ["weekly", "monthly"], 24, 5) is None)
check("d2 Konto mit gesperrtem Monat zaehlt nicht",
      accounts._expiring(shot[2], ["weekly", "monthly"], 24, 5) is None)
check("d3 gesundes Konto zaehlt weiter",
      accounts._expiring(acc("default9", 100.0, 10), ["weekly", "monthly"], 24, 5) is not None)
r = eval_cfg("default3", shot)
check("d4 kein Leerfahr-Wechsel", r.get("switchRecommended") is False, str(r))
check("d5 kein Grund 'bald ablaufend'",
      not (r.get("reason") or "").startswith("bald ablaufend"), str(r.get("reason")))
# Gegenprobe: dasselbe Wochenfenster ohne Monats-Sperre wird weiter bevorzugt.
r = eval_cfg("default1", [acc("default1", 90, 300), acc("default2", 90, 3)])
check("d6 gesundes Abo wird weiter bevorzugt",
      r.get("switchRecommended") is True and r.get("target") == "default2", str(r))
# Erschoepfungsregel (Regel 1) waehlt weiterhin nur freie Ziele: das gesperrte
# default2 darf nicht als Ziel durchkommen.
r = eval_cfg("default1", [acc("default1", 0.0, 1, weekly_status="rate-limited"),
                          acc("default2", 90, 3, monthly_free=0.0, monthly_status="rate-limited"),
                          acc("default3", 60, 200)])
check("d7 erschoepft: gesperrtes Ziel uebersprungen",
      r.get("target") == "default3", str(r))

print()
print(f"FEHLGESCHLAGEN: {len(fails)}" + (f" -> {fails}" if fails else ""))
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if fails else 0)