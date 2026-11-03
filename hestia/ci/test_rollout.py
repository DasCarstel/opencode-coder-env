#!/usr/bin/env python3
"""Live-Rollout-Test: rollout.dispatch() / _verify() mit GESTUBBTEM github_api.

Dispatch, Run-Suche und Run-Status werden komplett abgefangen — es wird nichts
wirklich ausgelöst und es gibt kein Netz. Läuft ohne Netz/Container:
  python3 hestia/ci/test_rollout.py
Stil wie test_switch_account.py (print-Checks, FEHLGESCHLAGEN: 0, Exit 0).
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import github_api  # noqa: E402
import rollout  # noqa: E402

# GitHub-Umgebungsvariablen explizit setzen (Muster wie test_switch_account.py):
# Auf CI-Runnern sind GITHUB_REF/GITHUB_TOKEN gesetzt — der Prozess darf sie
# hier nie wirklich benutzen: alle Calls sind gestubbt.
_temp_env = tempfile.mkdtemp(prefix="hestia-rollout-tests-env-")
os.environ["GITHUB_REF"] = "master"
os.environ["GITHUB_REPO"] = "DasCarstel/opencode-coder-env"
os.environ.pop("GITHUB_REPOSITORY", None)
os.environ.pop("GITHUB_TOKEN", None)
github_api.GITHUB_REF = "master"
github_api.GITHUB_REPO = "DasCarstel/opencode-coder-env"

rollout.WORKFLOW = "rollout.yml"   # falls der Test-Runner ROLLOUT_WORKFLOW setzt

# Sicherheitsnetz: Der Produktionszustand des Moduls darf nie vorausgesetzt
# werden — je Test komplett resettet der STATE via reset().
_CALL_DIR = tempfile.mkdtemp(prefix="hestia-rollout-tests-")   # (nur als Marker, keine Writes)

DISPATCH_CALLS = []       # dispatch_workflow-Aufrufe: {workflow, ref}
FIND_CALLS = []           # find_run-Aufrufe: {workflow, head_sha, event}
FAKE_RUN_URL = "https://github.com/DasCarstel/opencode-coder-env/actions/runs/777"
FAKE_COMMIT = "cafef00d1234567890cafef00d1234567890cafe"
RUN_RESULT = "success"    # conclusion der gestubbten run_status() — global steuerbar
FIND_MODE = "found"       # "found" = Run liefern, "stall" = kein Run (Sperren-Test)


def fake_dispatch(workflow, ref=None, inputs=None):
    DISPATCH_CALLS.append({"workflow": workflow, "ref": ref, "inputs": inputs})
    return {"dispatched": True, "workflow": workflow, "ref": ref or "master"}


def fake_find_run(workflow, since_ts, within_seconds=180, head_sha=None, event=None):
    time.sleep(0.05)   # kurzer Halt: dispatch()/state()-Checks müssen dem Thread zuvorkommen
    FIND_CALLS.append({"workflow": workflow, "since_ts": since_ts,
                       "within_seconds": within_seconds, "head_sha": head_sha,
                       "event": event})
    if FIND_MODE == "stall":
        return None
    return {"id": 777, "run_number": 12, "html_url": FAKE_RUN_URL,
            "status": "queued", "conclusion": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "head_sha": head_sha or "0000000000000000000000000000000000000000"}


def fake_run_status(run_id, *a, **kw):
    """Run-Status stubben: sofort 'completed' mit conclusion=RUN_RESULT."""
    return {"id": run_id, "status": "completed", "conclusion": RUN_RESULT,
            "html_url": FAKE_RUN_URL,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


rollout.github_api.dispatch_workflow = fake_dispatch
rollout.github_api.find_run = fake_find_run
rollout.github_api.run_status = fake_run_status
# Verifikations-Thread stark beschleunigen (Sekundenwerte -> Millisekunden):
rollout.VERIFY_POLL_INTERVAL = 0.01
rollout.VERIFY_FIND_TIMEOUT = 1
rollout.VERIFY_MAX_RUNTIME = 5
rollout.PENDING_RETENTION = 0.05


def reset():
    """Modul-Zustand UND die Aufzeichnungen der Stubs zurücksetzen."""
    DISPATCH_CALLS.clear()
    FIND_CALLS.clear()
    with rollout.LOCK:
        rollout.STATE["pending"] = None
        rollout.STATE["last"] = None
        rollout.STATE["events"] = []


fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  [{extra}]" if extra else ""))
    if not cond:
        fails.append(label)


def wait_pending(timeout=5.0):
    """Warten bis der Verifikations-Thread fertig ist (pending abgeräumt).

    Liefert den letzten beobachteten pending-Zustand (done/failed), oder None,
    wenn nie eins gesetzt war.
    """
    snap = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        with rollout.LOCK:
            p = rollout.STATE.get("pending")
            if p is not None:
                snap = dict(p)
        if snap is not None and snap.get("phase") in ("done", "failed"):
            time.sleep(0.15)   # PENDING_RETENTION (0.05 s) + Abräumen abwarten
        with rollout.LOCK:
            p = rollout.STATE.get("pending")
        if p is None:
            return snap
        time.sleep(0.01)
    with rollout.LOCK:
        p = rollout.STATE.get("pending")
    return dict(p) if p else snap


print("== (a) dispatch akzeptiert: started=True, phase=wait-run, kein last ==")
reset()
r = rollout.dispatch(by="test", reason="Test-A")
check("started=True", r.get("started") is True, str(r.get("error")))
check("pending.phase == wait-run", r.get("pending", {}).get("phase") == "wait-run",
      str(r.get("pending")))
check("pending.mode == dispatch", r.get("pending", {}).get("mode") == "dispatch")
check("pending.workflow == rollout.yml", r.get("pending", {}).get("workflow") == "rollout.yml")
check("kein runId/runNumber/runUrl im Stapel",
      r.get("pending", {}).get("runId") is None and r.get("pending", {}).get("runNumber") is None
      and r.get("pending", {}).get("runUrl") is None)
check("last bleibt None", rollout.STATE["last"] is None, str(rollout.STATE["last"]))
check("genau 1 Dispatch-Call", len(DISPATCH_CALLS) == 1, str(DISPATCH_CALLS))
check("Workflow beim Dispatch = rollout.yml",
      (DISPATCH_CALLS[0] or {}).get("workflow") == "rollout.yml", str(DISPATCH_CALLS))
ev = [e for e in rollout.STATE["events"] if e.get("type") == "rollout"]
check("Event type=rollout beim Anstoßen", len(ev) == 1 and ev[0]["by"] == "test", str(ev))
wait_pending()
check("Rollout (a) läuft durch: phase done", rollout.STATE["last"] is not None,
      str(rollout.STATE["last"]))

print("== (b) Erfolg: phase done, last gesetzt, Event rollout-ok ==")
reset()
RUN_RESULT = "success"
r = rollout.dispatch(by="test", reason="Test-B")
snap = wait_pending()
check("phase done erreicht", snap is not None and snap.get("phase") == "done", str(snap))
check("runNumber im lauf", snap is not None and snap.get("runNumber") == 12, str(snap))
check("runUrl im lauf", snap is not None and FAKE_RUN_URL in str(snap.get("runUrl")), str(snap))
last = rollout.STATE.get("last") or {}
check("STATE.last gesetzt", bool(last), str(last))
check("last.conclusion == success", last.get("conclusion") == "success", str(last))
check("last.runNumber == 12", last.get("runNumber") == 12, str(last))
check("last.workflow == rollout.yml", last.get("workflow") == "rollout.yml", str(last))
check("last.at gesetzt", isinstance(last.get("at"), float), str(last))
check("last.duration gesetzt", isinstance(last.get("duration"), (int, float)), str(last))
evok = [e for e in rollout.STATE["events"] if e.get("type") == "rollout-ok"]
check("Event rollout-ok vorhanden",
      len(evok) == 1 and evok[0].get("runNumber") == 12 and FAKE_RUN_URL in str(evok[0].get("runUrl")),
      str(evok))

print("== (c) Failure: phase failed, error mit Run-URL, Event rollout-error ==")
reset()
RUN_RESULT = "failure"
r = rollout.dispatch(by="test", reason="Test-C")
snap = wait_pending()
check("phase failed erreicht", snap is not None and snap.get("phase") == "failed", str(snap))
check("error enthält Run-URL",
      snap is not None and FAKE_RUN_URL in str(snap.get("error")), str(snap))
check("error enthält conclusion",
      snap is not None and "conclusion=failure" in str(snap.get("error")), str(snap))
ev_err = [e for e in rollout.STATE["events"] if e.get("type") == "rollout-error"]
check("Event rollout-error mit Run-URL",
      len(ev_err) == 1 and FAKE_RUN_URL in str(ev_err[0].get("runUrl"))
      and FAKE_RUN_URL in str(ev_err[0].get("reason")), str(ev_err))
RUN_RESULT = "success"

print("== (d) pending-Sperre blockt zweiten dispatch ==")
reset()
FIND_MODE = "stall"     # Run bleibt unauffindbar → pending bleibt wait-run
r1 = rollout.dispatch(by="test", reason="Test-D1")
check("erster Start ok", r1.get("started") is True, str(r1.get("error")))
r2 = rollout.dispatch(by="test", reason="Test-D2")
check("zweiter Versuch gestoppt", r2.get("started") is False, str(r2))
check("Fehler 'Rollout läuft bereits'", r2.get("error") == "Rollout läuft bereits", str(r2))
check("zweiter Versuch liefert das aktive pending",
      r2.get("pending", {}).get("phase") == "wait-run"
      and r2.get("pending", {}).get("reason") == "Test-D1", str(r2.get("pending")))
check("kein zweiter Dispatch", len(DISPATCH_CALLS) == 1, str(len(DISPATCH_CALLS)))
FIND_MODE = "found"
wait_pending()   # ersten Thread (jetzt erfolgreich) auslaufen lassen

print("== (e) mode=push ohne commit -> Fehler, kein pending, kein Dispatch ==")
reset()
r = rollout.dispatch(mode="push", by="test")
check("started=False", r.get("started") is False, str(r))
check("Fehler nennt commit", "commit" in str(r.get("error")), str(r.get("error")))
check("kein pending gesetzt", rollout.STATE["pending"] is None, str(rollout.STATE["pending"]))
check("kein Dispatch", DISPATCH_CALLS == [], str(DISPATCH_CALLS))

print("== (f) mode=push MIT commit: kein Dispatch, Suche über head_sha ==")
reset()
r = rollout.dispatch(mode="push", commit=FAKE_COMMIT, by="test", reason="apply")
check("started=True", r.get("started") is True, str(r.get("error")))
check("kein Dispatch (Run kommt vom Push)", DISPATCH_CALLS == [], str(DISPATCH_CALLS))
check("pending.headSha == commit", r.get("pending", {}).get("headSha") == FAKE_COMMIT, str(r.get("pending")))
snap = wait_pending()
check("push-Rollout läuft durch", snap is not None and snap.get("phase") == "done", str(snap))
FC = FIND_CALLS[FIND_CALLS.__len__() - 1] if FIND_CALLS else {}
check("find_run ohne event-Filter (push)",
      FC.get("event") is None, str(FC))
check("find_run mit head_sha=commit", FC.get("head_sha") == FAKE_COMMIT, str(FC))

print("== (g) state(): live elapsed, last, events, workflow ==")
reset()
r = rollout.dispatch(by="test", reason="Test-G")
first = rollout.state()
check("state.workflow == rollout.yml", first.get("workflow") == "rollout.yml", first.get("workflow"))
check("state.pending.phase == wait-run", first.get("pending", {}).get("phase") == "wait-run",
      str(first.get("pending")))
check("live elapsed gesetzt",
      isinstance(first.get("pending", {}).get("elapsed"), (int, float)),
      str(first.get("pending", {}).get("elapsed")))
time.sleep(0.1)
second = rollout.state()
check("elapsed steigt wärend wait-run",
      (second.get("pending") or {}).get("elapsed", 0) >= (first.get("pending") or {}).get("elapsed", 0),
      f"{first.get('pending', {}).get('elapsed')} -> {second.get('pending', {}).get('elapsed')}")
check("events im state (max 20)",
      isinstance(first.get("events"), list) and len(first.get("events")) <= 20,
      str(len(first.get("events") or [])))
wait_pending()
st = rollout.state()
check("pending nach Abräumung None", st.get("pending") is None, str(st.get("pending")))
check("last überlebt die Abräumung", (st.get("last") or {}).get("conclusion") == "success",
      str(st.get("last")))

print()
print(f"FEHLGESCHLAGEN: {len(fails)}" + (f" -> {fails}" if fails else ""))
shutil.rmtree(_CALL_DIR, ignore_errors=True)
shutil.rmtree(_temp_env, ignore_errors=True)
sys.exit(1 if fails else 0)
