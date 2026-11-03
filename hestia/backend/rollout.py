#!/usr/bin/env python3
"""rollout.py — Live-Fortschritt für Modell-/Tier-Rollouts (Force + Apply).

Force-Rollout (/api/rollout) dispatch rollout.yml manuell; Apply (/api/apply)
erzeugt bei Änderung einen Push, der rollout.yml automatisch triggert. Beide
Wege laufen hier in denselben pending-Zustand (Muster wie accounts.py
switchPending): ein Daemon-Thread sucht den Run, pollt seinen Status und
meldet done/failed — das Panel pollt /api/rollout/status und zeigt Phasen,
Run-Link und Elapsed live, ohne dass es blockiert.
"""
import os
import threading
import time
from datetime import datetime

import github_api

# Workflow, der den Rollout (Container-Restart) ausführt.
WORKFLOW = os.environ.get("ROLLOUT_WORKFLOW", "rollout.yml")
VERIFY_FIND_TIMEOUT = int(os.environ.get("ROLLOUT_VERIFY_FIND_TIMEOUT") or 180)    # s bis der Run auftaucht
VERIFY_POLL_INTERVAL = int(os.environ.get("ROLLOUT_VERIFY_POLL_INTERVAL") or 15)   # s zwischen Status-Polls
VERIFY_MAX_RUNTIME = int(os.environ.get("ROLLOUT_VERIFY_MAX_RUNTIME") or 1800)      # s Obergrenze für den Run
PENDING_RETENTION = int(os.environ.get("ROLLOUT_PENDING_RETENTION") or 180)         # s: fertiges pending lesbar halten

LOCK = threading.Lock()

STATE = {
    "pending": None,   # Live-Status eines laufenden/abgeschlossenen Rollouts
    "last": None,      # letzter abgeschlossener Run (Run-Info für das Panel)
    "events": [],      # Ringpuffer der Rollout-Ereignisse (max 200)
}


def _log(msg):
    print(f"[hestia-rollout] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _event(**fields) -> dict:
    """Ereignis an die STATE anhängen (Ringpuffer, älteste zuerst raus)."""
    ev = {"at": time.time(), **fields}
    with LOCK:
        events = STATE.setdefault("events", [])
        events.append(ev)
        del events[:-200]
    return ev


def dispatch(workflow=None, by="manual", reason="", commit=None, mode="dispatch") -> dict:
    """Rollout anstoßen: dispatch oder Push-begleitung, dann Verifikations-Thread.

    mode="dispatch": rollout.yml wird explizit dispatcht (Force-Rollout).
    mode="push": KEIN Dispatch — der Run entsteht durch den Commit, den
    /api/apply vorher gemacht hat; hier genügt es, den Run mit head_sha=commit
    zu suchen und seinen Fortschritt zu melden (ein zusätzlicher Dispatch
    würde den Rollout doppelt ausführen).

    Rückgabe ist immer ein Dict:
      {"started": True,  "pending": {...}}        — angenommen, Verifikation läuft
      {"started": False, "error": ...}            — nichts angestoßen
      {"started": False, "error": ..., "pending": {...}} — sperre aktiv
    """
    wf = workflow or WORKFLOW

    # Max. 1 gleichzeitiger Rollout: ein aktives pending (wait-run/running)
    # blockt. Abgeschlossenes pending (done/failed) blockt nicht — auch wenn
    # es noch für PENDING_RETENTION s lesbar bleibt.
    with LOCK:
        pend = STATE.get("pending")
        if pend and pend.get("phase") not in ("done", "failed"):
            return {"started": False, "error": "Rollout läuft bereits",
                    "pending": dict(pend)}

    if mode == "push":
        if not commit:
            return {"started": False,
                    "error": "mode='push' braucht commit (SHA) — ohne SHA ist der Run nicht auffindbar"}
        # Bewusst KEIN Dispatch: der Push von /api/apply feuert den Workflow
        # selbst. Der Verifikations-Thread sucht den Run über die Commit-SHA.
    else:
        try:
            github_api.dispatch_workflow(wf)
        except Exception as e:  # noqa: BLE001
            err = f"Dispatch fehlgeschlagen: {e}"
            _event(type="rollout-error", workflow=wf, reason=err, by=by)
            _log(f"Rollout nicht angestoßen ({wf}) — {err}")
            return {"started": False, "error": str(e)}

    pending = {
        "workflow": wf,
        "mode": mode,
        "phase": "wait-run",
        "startedAt": time.time(),
        "runId": None,
        "runNumber": None,
        "runUrl": None,
        "headSha": commit,
        "error": None,
        "elapsed": None,
        "by": by,
        "reason": reason or "",
    }
    with LOCK:
        STATE["pending"] = pending
    _log(f"Rollout angestoßen: {wf} (mode={mode}, via {by}, Grund: {pending['reason'] or '—'})"
         + (f", commit={commit[:8]}" if commit else ""))
    _event(type="rollout", workflow=wf, mode=mode, reason=pending["reason"], by=by)
    # Verifikation im Hintergrund (Daemon: stirbt mit dem Panel-Prozess).
    threading.Thread(target=_verify, args=(pending,), daemon=True).start()
    return {"started": True, "pending": dict(pending), "workflow": wf}


def _finish_failed(pending: dict, err: str) -> None:
    """Rollout als fehlgeschlagen markieren (elapsed einfrieren, Event, Log)."""
    with LOCK:
        pending["phase"] = "failed"
        pending["error"] = err
        pending["elapsed"] = round(time.time() - float(pending.get("startedAt") or time.time()), 1)
    _event(type="rollout-error", workflow=pending.get("workflow") or WORKFLOW, reason=err,
           runNumber=pending.get("runNumber"), runUrl=pending.get("runUrl"))
    _log(f"Rollout NICHT bestätigt: {pending.get('workflow')} — {err}")


def _verify(pending: dict) -> None:
    """Verifikations-Thread: Run finden, Status pollen, done/failed melden.

    phase-Verlauf im pending (Panel liest STATE["pending"] live):
      wait-run → running → done    (Erfolgsfall)
      wait-run → running → failed  (Fehlerfall; auch wait-run → failed, wenn
                                    der Run nie auftaucht)

    Der Thread darf NIEMALS crashen: jede GitHub-Abfrage ist einzeln in
    try/except und ein Catch-All fängt unbehandelte Fehler.
    """
    wf = str(pending.get("workflow") or WORKFLOW)
    mode = str(pending.get("mode") or "dispatch")
    try:
        # --- 1) Run finden (bis VERIFY_FIND_TIMEOUT warten). ---
        # Dispatch-Run: event-Filter workflow_dispatch; Push-Run: über die
        # Commit-SHA (sonst fängt die Suche den VORHERIGEN Run ab).
        run = None
        deadline = time.time() + VERIFY_FIND_TIMEOUT
        while run is None and time.time() < deadline:
            try:
                run = github_api.find_run(
                    wf, float(pending.get("startedAt") or 0.0),
                    within_seconds=VERIFY_FIND_TIMEOUT,
                    head_sha=pending.get("headSha"),
                    event="workflow_dispatch" if mode == "dispatch" else None,
                )
            except Exception as e:  # noqa: BLE001
                _log(f"[verify] Run-Suche fehlgeschlagen (weiter warten): {e}")
            if run is None:
                time.sleep(5)
        if run is None:
            _finish_failed(pending, f"kein {wf}-Run gefunden ({VERIFY_FIND_TIMEOUT}s) — "
                                    f"Workflow-Trigger fehlgeschlagen oder verzögert")
            return
        with LOCK:
            pending["runId"] = run.get("id")
            pending["runNumber"] = run.get("run_number")
            pending["runUrl"] = run.get("html_url")
            pending["phase"] = "running"
        _log(f"[verify] Run erkannt: #{run.get('run_number')} id={run.get('id')} "
             f"({run.get('html_url')})")

        # --- 2) Status pollen (VERIFY_POLL_INTERVAL-Takt, max. VERIFY_MAX_RUNTIME). ---
        status, conclusion = None, None
        hard = time.time() + VERIFY_MAX_RUNTIME
        while time.time() < hard:
            try:
                d = github_api.run_status(pending["runId"])
                status, conclusion = d.get("status"), d.get("conclusion")
            except Exception as e:  # noqa: BLE001
                # GitHub-Hackeleien → weiterpollen statt sofort failed; NUR der
                # Timeout entscheidet über Abbruch.
                _log(f"[verify] run_status fehlgeschlagen (Poll wiederholt): {e}")
                status = status or "in_progress"
            with LOCK:
                pending["phase"] = "running"
            if status == "completed":
                break
            time.sleep(VERIFY_POLL_INTERVAL)

        # --- 3) Auswertung. ---
        if status != "completed":
            _finish_failed(pending, f"Timeout beim Warten auf den Run "
                                    f"(>{VERIFY_MAX_RUNTIME // 60} min, Run {pending.get('runUrl')})")
            return
        if conclusion != "success":
            _finish_failed(pending, f"Rollout fehlgeschlagen: conclusion={conclusion} "
                                    f"(Run {pending.get('runUrl')})")
            return

        with LOCK:
            pending["phase"] = "done"
            pending["elapsed"] = round(time.time() - float(pending.get("startedAt") or time.time()), 1)
            STATE["last"] = {
                "runId": pending.get("runId"),
                "runNumber": pending.get("runNumber"),
                "runUrl": pending.get("runUrl"),
                "conclusion": conclusion,
                "workflow": wf,
                "at": time.time(),
                "duration": pending.get("elapsed"),
            }
        _event(type="rollout-ok", workflow=wf, runNumber=pending.get("runNumber"),
               runUrl=pending.get("runUrl"), duration=pending.get("elapsed"),
               reason=pending.get("reason") or "")
        _log(f"Rollout bestätigt: {wf} (Run #{pending.get('runNumber')}, "
             f"{pending.get('runUrl')}, {pending.get('elapsed')}s)")
    except Exception as e:  # noqa: BLE001
        # Letzte Verteidigungslinie: der Thread darf nie unbeobachtet sterben.
        _log(f"[verify] unerwarteter Fehler: {e}")
        try:
            _finish_failed(pending, f"Verifikation fehlgeschlagen: {e}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        # Abgeschlossenes pending noch ~PENDING_RETENTION im STATE lassen
        # (Panel kann done/failed + error abrufen), dann abräumen — aber nur,
        # wenn nicht längst ein neuer Rollout das Feld übernommen hat.
        try:
            time.sleep(PENDING_RETENTION)
        except Exception:  # noqa: BLE001
            pass
        with LOCK:
            if STATE.get("pending") is pending:
                STATE["pending"] = None


def state() -> dict:
    """Panel-Snapshot: pending (+ live elapsed), last, Events, Workflow."""
    with LOCK:
        pend = STATE.get("pending")
        pending = dict(pend) if pend else None
        out = {
            "pending": pending,
            "last": dict(STATE["last"]) if STATE.get("last") else None,
            "events": list(STATE.get("events") or [])[-20:],
            "workflow": WORKFLOW,
        }
    # Elapsed für laufende Phasen live berechnen (auch während running) —
    # done/failed haben ihren eingefrorenen Endwert.
    p = out.get("pending")
    if p and p.get("startedAt") and p.get("phase") in ("wait-run", "running"):
        try:
            p["elapsed"] = round(time.time() - float(p["startedAt"]), 1)
        except (TypeError, ValueError):
            pass
    return out
