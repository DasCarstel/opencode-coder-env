#!/usr/bin/env python3
"""P2-Test: switch_account() / _apply_policy() mit GESTUBBTEM github_api.

Dispatch wird komplett abgefangen — es wird nichts wirklich ausgelöst.
Läuft ohne Netz/Container: python3 hestia/ci/test_switch_account.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import accounts  # noqa: E402
import github_api  # noqa: E402

CFG = {
    "active": "default1",
    "policy": {"mode": "auto", "cooldownSeconds": 1800},
    "accounts": [{"id": "default1"}, {"id": "default2"}, {"id": "default3"}],
    "providers": {},
}
CALLS = []


def fake_get_file(path):
    import hjsonc
    return hjsonc.dumps(CFG) if hasattr(hjsonc, "dumps") else None


def fake_dispatch(workflow, ref=None, inputs=None):
    CALLS.append({"workflow": workflow, "ref": ref, "inputs": inputs})
    return {"dispatched": True, "workflow": workflow, "ref": ref or "master"}


# Original-Funktion VOR dem Patch festhalten (accounts.github_api ist dasselbe
# Modulobjekt wie github_api — der Patch wirkt sonst global).
real_dispatch_workflow = github_api.dispatch_workflow
accounts._config = lambda force=False: CFG
accounts.github_api.dispatch_workflow = fake_dispatch


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
check("Workflow = switch-account.yml", CALLS[0]["workflow"] == "switch-account.yml", CALLS[0]["workflow"])
check("inputs korrekt", CALLS[0]["inputs"] == {"account": "default2", "reason": "Test"}, str(CALLS[0]["inputs"]))
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

print()
print(f"FEHLGESCHLAGEN: {len(fails)}" + (f" -> {fails}" if fails else ""))
sys.exit(1 if fails else 0)