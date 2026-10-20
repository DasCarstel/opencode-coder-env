#!/usr/bin/env python3
"""Regressionstests für hestia/backend/hermes_speed.py — komplett ohne Netz.

Hintergrund (20.10.2026, Hermes Voice Phase 2): Der Speed-Scan misst neue
Modelle über echte Probe-Sessions (failover.probe-Muster) und liefert eine
Empfehlung für den Hermes-Primary — angewendet wird aber nur per Klick über
hermes.apply (die SOT bleibt Quelle der Wahrheit, Fallback bleibt erhalten).
Daneben liest hermes_speed.telemetry() den O19-Snapshot der Hermes-state.db
(read-only). Diese Tests halten fest:

  1. Discovery: free-only-Filter (Ausnahme: aktueller Primary/Fallback),
     Ausschlüsse (-contributor, deprecated, disabled, fremder Provider),
     Katalog-Hash-Abgleich gegen known, Tageslimit, Archiv bis recheckAfter.
  2. Probe: Warm-up + N Samples, Sessions werden gelöscht, toolOk bleibt None,
     Fehler zählen (Session-Anlegen fehlgeschlagen, Watch-Fehlschlag).
  3. Läufe: run_probe_refs/run_discovery/run_monitoring aktualisieren State +
     known erst NACH der Probe; Fehler/p95 ≥ 4000 ms archivieren
     (archivedAt + recheckAfter); Status „monitoring“ sonst.
  4. recommend() wählt den besten freien Monitoring-Kandidaten;
     apply_recommendation() geht ausschließlich über hermes.apply und erhält
     den Fallback; ein katalogungültiger Ref wird abgelehnt.
  5. telemetry(): Aggregation aus einer minimalen state.db (Anfragen/Tokens/
     Kosten, Fensterfilter, Antwortzeit-Näherung aus Zeitstempeln — ohne
     Inhalte); fehlender Snapshot → available=False, nie eine Exception.
  6. Automatik-Loop: kein Sofort-Probe nach Neustart, wenn checkedAt jünger
     als das Intervall ist (Restzeit wird gewartet); fehlender/ungültiger
     checkedAt (erste Installation) läuft sofort, danach volles Intervall.
  7. Statische Checks: suggest._event_watch nimmt on_first_delta an,
     telemetry_snapshot.py kompiliert ohne Netz-Imports, Compose/Workflows
     verdrahten den Seitenwagen (nur der Snapshot geht ans Panel).

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_hermes_speed.py
    python3 hestia/ci/test_hermes_speed.py
"""
import importlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import types

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TMP = tempfile.mkdtemp(prefix="hestia-speed-test-")

# Alle Toggles deterministisch halten (hermes_speed liest sie beim Import).
ENV_DEFAULTS = {
    "HESTIA_SPEED_AUTO": "0",
    "HESTIA_SPEED_PATH": os.path.join(TMP, "speed.json"),
    "HESTIA_SPEED_KNOWN_PATH": os.path.join(TMP, "speed-known.json"),
    "HERMES_STATE_DB": os.path.join(TMP, "state.db"),
    "HESTIA_SPEED_INTERVAL_HOURS": "24",
    "HESTIA_SPEED_SAMPLES": "3",
    "HESTIA_SPEED_PROBE_TIMEOUT": "35",
    "HESTIA_SPEED_MAX_NEW_PER_DAY": "5",
    "HESTIA_SPEED_ARCHIVE_DAYS": "90",
    "HESTIA_SPEED_FREE_ONLY": "1",
    "HESTIA_PROVIDERS": "opencode-go,opencode",
}

fails = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# ── Fakes (kein Netz, kein GitHub, kein OpenCode-Server) ─────────────────────

def _model(model_id, provider="opencode-go", released=1, status="active", enabled=True,
           cost=(0, 0)):
    inp, outp = cost
    return {
        "modelID": model_id, "providerID": provider, "ref": f"{provider}/{model_id}",
        "name": model_id, "released": released, "status": status, "enabled": enabled,
        "variants": [], "cost": {"input": inp, "output": outp, "cacheRead": 0,
                                 "cacheWrite": 0},
        "costTiers": [], "costEffective": {"input": inp, "output": outp},
        "limit": {"context": 400000},
    }


MODELS = [
    _model("dash-free", released=30),
    _model("longcat-free", released=20),
    _model("beam-free", released=10),
    _model("glm-5.3-flash", released=9, cost=(0.2, 0.8)),
    _model("x-contributor", released=8),
    _model("old-model", released=7, status="deprecated"),
    _model("off-model", released=6, enabled=False),
    _model("thing-free", provider="fremd", released=5),
]

CALLS = {"req": [], "watch": [], "deleted": []}
SUGGEST_BEHAVIOUR = {"watch": "succeeded", "fail_create": False}
HERMES_STATE = {"sot": {}, "applied": []}


def _fake_catalog():
    c = types.ModuleType("catalog")

    def find_model(ref):
        for m in MODELS:
            if m["ref"].lower() == ref.lower():
                return m
        return None

    def usable(ref):
        m = find_model(ref)
        if not m:
            return False, f"Modell {ref} nicht im Katalog"
        if m["status"] != "active":
            return False, f"Modell {ref} hat Status '{m['status']}'"
        if not m["enabled"]:
            return False, f"Modell {ref} ist disabled"
        if m["modelID"].endswith("-contributor"):
            return False, f"Modell {ref} traegt die Trainings-Klausel"
        return True, m

    def is_free(ref):
        m = find_model(ref)
        if not m:
            return False
        return not m["cost"]["input"] and not m["cost"]["output"]

    c.fetch_models = lambda force=False: json.loads(json.dumps(MODELS))
    c.find_model = find_model
    c.usable = usable
    c.is_free = is_free
    return c


def _fake_suggest():
    s = types.ModuleType("suggest")
    s.OC_SERVER_URL = "http://opencode-fake"
    s.OC_SUGGEST_DIR = "/tmp/fake-workspace"

    def _req(method, url, body=None, timeout=30):
        CALLS["req"].append({"method": method, "url": url, "body": body})
        if method == "POST" and url.endswith("/api/session"):
            if SUGGEST_BEHAVIOUR["fail_create"]:
                return 500, {}
            return 201, {"data": {"id": f"sid-{len(CALLS['req'])}"}}
        if method == "DELETE":
            return 200, {}
        return 200, {}

    # Gleiche Signatur wie suggest._event_watch (inkl. on_first_delta).
    def _event_watch(sid, deadline, stall_timeout, on_progress=None, on_first_delta=None):
        CALLS["watch"].append({"sid": sid, "on_first_delta": callable(on_first_delta)})
        if on_first_delta:
            on_first_delta()
        return SUGGEST_BEHAVIOUR["watch"]

    def _delete_session(sid):
        CALLS["deleted"].append(sid)
        return True

    s._req = _req
    s._event_watch = _event_watch
    s._delete_session = _delete_session
    return s


def _fake_hermes():
    h = types.ModuleType("hermes")

    class ApplyError(Exception):
        pass

    h.ApplyError = ApplyError

    def config(force=False):
        return json.loads(json.dumps(HERMES_STATE["sot"]))

    def _public(cfg):
        return json.loads(json.dumps({k: v for k, v in cfg.items() if k != "_error"}))

    def apply(payload):
        HERMES_STATE["applied"].append(json.loads(json.dumps(payload)))
        HERMES_STATE["sot"] = json.loads(json.dumps(payload))
        return {"applied": True}

    h.config = config
    h._public = _public
    h.apply = apply
    return h


sys.modules["catalog"] = _fake_catalog()
sys.modules["suggest"] = _fake_suggest()
sys.modules["hermes"] = _fake_hermes()

HERMES_STATE["sot"] = {
    "enabled": True,
    "primary": {"provider": "", "model": ""},
    "fallback": [],
    "pool": {"followActive": True, "onlyActiveWhenExpiring": False},
}

DEFAULT_SOT = {
    "enabled": True,
    "primary": {"provider": "", "model": ""},
    "fallback": [],
    "pool": {"followActive": True, "onlyActiveWhenExpiring": False},
}

DEFAULT_SPEED_STATE = {
    "checkedAt": None,
    "candidates": [],
    "archive": [],
    "newToday": {"date": "2000-01-01", "count": 0},
    "lastApplied": None,
}


def reset(env=None):
    for k, v in ENV_DEFAULTS.items():
        os.environ[k] = v
    for k, v in (env or {}).items():
        os.environ[k] = str(v)
    for key in ("HESTIA_SPEED_PATH", "HESTIA_SPEED_KNOWN_PATH"):
        if os.path.exists(os.environ[key]):
            os.remove(os.environ[key])
    if os.path.exists(os.environ["HERMES_STATE_DB"]):
        os.remove(os.environ["HERMES_STATE_DB"])
    CALLS["req"].clear()
    CALLS["watch"].clear()
    CALLS["deleted"].clear()
    SUGGEST_BEHAVIOUR.update({"watch": "succeeded", "fail_create": False})
    HERMES_STATE["applied"].clear()
    HERMES_STATE["sot"] = json.loads(json.dumps(DEFAULT_SOT))
    sys.modules.pop("hermes_speed", None)
    return importlib.import_module("hermes_speed")


def _write_state(mod, st):
    with open(mod.SPEED_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f)


def _read_state(mod):
    with open(mod.SPEED_PATH, encoding="utf-8") as f:
        return json.load(f)


def _seed_monitoring(mod, refs_with_scores):
    st = json.loads(json.dumps(DEFAULT_SPEED_STATE))
    for ref, score in refs_with_scores.items():
        st["candidates"].append({
            "ref": ref, "free": True, "provider": ref.split("/")[0],
            "model": ref.split("/", 1)[1], "runs": 3,
            "ttfbMs": {"p50": 10.0, "p95": 20.0}, "totalMs": {"p50": 30.0, "p95": 40.0},
            "toolOk": None, "errors": 0, "score": score, "status": "monitoring",
            "lastProbe": "2026-10-20T00:00:00Z",
        })
    _write_state(mod, st)


def main():
    print("\n[1] Discovery: free-only + Ausschlüsse")
    mod = reset()
    queued = {c["ref"]: c for c in mod.discover()}
    check("nur freie opencode-go-Modelle in der Queue",
          set(queued) == {"opencode-go/dash-free", "opencode-go/longcat-free",
                          "opencode-go/beam-free"}, str(sorted(queued)))
    check("bezahltes Modell ausgeschlossen (free-only)", "opencode-go/glm-5.3-flash" not in queued)
    check("-contributor ausgeschlossen", "opencode-go/x-contributor" not in queued)
    check("deprecated ausgeschlossen", "opencode-go/old-model" not in queued)
    check("disabled ausgeschlossen", "opencode-go/off-model" not in queued)
    check("fremder Provider ausgeschlossen", "fremd/thing-free" not in queued)
    check("Kandidaten-Form (Report §2.1)",
          all(set(("ref", "free", "provider", "model", "runs", "ttfbMs", "totalMs",
                   "toolOk", "errors", "score", "status", "lastProbe")) <= set(c)
              for c in queued.values()), str(next(iter(queued.values()), {})))
    check("status=queued", all(c["status"] == "queued" for c in queued.values()))
    check("toolOk bleibt None", all(c["toolOk"] is None for c in queued.values()))
    check("known bleibt bis zur Probe leer",
          os.path.exists(mod.KNOWN_PATH) is False, str(mod.KNOWN_PATH))

    print("\n[2] Free-only-Ausnahme: bezahlter Fallback/Primary darf geprobt werden")
    mod = reset()
    HERMES_STATE["sot"]["fallback"] = [{"provider": "opencode-go", "model": "glm-5.3-flash"}]
    queued = {c["ref"] for c in mod.discover()}
    check("aktueller Fallback trotz free-only in der Queue",
          "opencode-go/glm-5.3-flash" in queued, str(sorted(queued)))

    print("\n[3] Katalog-Hash: bekannt & unverändert bleibt draußen")
    mod = reset()
    known_all = {ref: {"hash": mod._metadata_hash(mod.catalog.find_model(ref)),
                       "firstSeen": "2026-10-01T00:00:00Z",
                       "lastSeen": "2026-10-01T00:00:00Z"}
                 for ref in ("opencode-go/dash-free", "opencode-go/longcat-free",
                             "opencode-go/beam-free")}
    mod._save_known(known_all)
    check("alles bekannt & unverändert → leere Queue", mod.discover() == [])
    # Hash-Änderung am Katalog (released ändert sich, Modell bleibt frei) →
    # Modell wird neu eingereiht
    MODELS[0]["released"] = 99
    queued = {c["ref"] for c in mod.discover()}
    MODELS[0]["released"] = 30
    check("Hash-Änderung reiht Modell neu ein", queued == {"opencode-go/dash-free"},
          str(sorted(queued)))

    print("\n[4] Tageslimit (MAX_NEW_PER_DAY) und force")
    mod = reset({"HESTIA_SPEED_MAX_NEW_PER_DAY": "2"})
    queued = {c["ref"] for c in mod.discover(force=False)}
    check("Limit 2 → nur 2 neue Kandidaten", len(queued) == 2, str(sorted(queued)))
    st = _read_state(mod)
    check("Zähler newToday geführt", st["newToday"]["count"] == 2, str(st["newToday"]))
    mod = reset({"HESTIA_SPEED_MAX_NEW_PER_DAY": "2"})
    queued = {c["ref"] for c in mod.discover(force=True)}
    check("force=True umgeht das Limit", len(queued) == 3, str(sorted(queued)))

    print("\n[5] Archiv: skip bis recheckAfter, danach Wiederaufnahme")
    mod = reset()
    st = json.loads(json.dumps(DEFAULT_SPEED_STATE))
    st["archive"] = [{
        "ref": "opencode-go/longcat-free", "free": True, "provider": "opencode-go",
        "model": "longcat-free", "runs": 3, "ttfbMs": {"p50": 1.0, "p95": 2.0},
        "totalMs": {"p50": 3.0, "p95": 4.0}, "toolOk": None, "errors": 1, "score": 1.0,
        "status": "archived", "lastProbe": "2026-10-01T00:00:00Z",
        "archivedAt": "2026-10-01T00:00:00Z", "recheckAfter": "2999-01-01T00:00:00Z",
    }]
    _write_state(mod, st)
    queued = {c["ref"] for c in mod.discover()}
    check("archiviert (recheckAfter in Zukunft) bleibt draußen",
          "opencode-go/longcat-free" not in queued, str(sorted(queued)))
    st["archive"][0]["recheckAfter"] = "2000-01-01T00:00:00Z"
    _write_state(mod, st)
    queued = {c["ref"] for c in mod.discover()}
    check("abgelaufenes recheckAfter → Wiederaufnahme",
          "opencode-go/longcat-free" in queued, str(sorted(queued)))
    st = _read_state(mod)
    check("Wiederaufnahme löst den Archiveintrag auf", st["archive"] == [], str(st["archive"]))

    print("\n[6] probe(): Warm-up + Samples, Sessions weggeräumt, toolOk=None")
    mod = reset()
    res = mod.probe("opencode-go/beam-free", samples=3)
    check("Probe ok", res["ok"] is True, str(res.get("detail")))
    check("runs = Samples (Warm-up nicht gezählt)", res["runs"] == 3, str(res.get("runs")))
    check("keine Fehler", res["errors"] == 0)
    check("Percentiles vorhanden", res["ttfbMs"]["p50"] is not None
          and res["totalMs"]["p95"] is not None, str(res))
    check("toolOk None (keine Tool-Probe)", res["toolOk"] is None)
    check("4 Sessions angelegt (1 Warm-up + 3 Samples)",
          len([r for r in CALLS["req"] if r["url"].endswith("/api/session")]) == 4)
    check("alle Sessions gelöscht", len(CALLS["deleted"]) == 4, str(CALLS["deleted"]))
    check("Prompt-Text wie failover.probe",
          any(r["body"] == {"text": "Antworte nur mit OK"} for r in CALLS["req"]
              if r["url"].endswith("/prompt")))
    check("on_first_delta an _event_watch durchgereicht",
          len(CALLS["watch"]) == 4 and all(w["on_first_delta"] for w in CALLS["watch"]))

    print("\n[7] probe(): Fehlerfälle zählen")
    mod = reset()
    SUGGEST_BEHAVIOUR["fail_create"] = True
    res = mod.probe("opencode-go/beam-free", samples=2)
    SUGGEST_BEHAVIOUR["fail_create"] = False
    check("Session-Anlegen fehlgeschlagen → ok False, errors gezählt",
          res["ok"] is False and res["errors"] == 2, str(res))
    mod = reset()
    SUGGEST_BEHAVIOUR["watch"] = "failed:upstream"
    res = mod.probe("opencode-go/beam-free", samples=2)
    SUGGEST_BEHAVIOUR["watch"] = "succeeded"
    check("Watch-Fehlschlag → ok False, errors gezählt",
          res["ok"] is False and res["errors"] == 2, str(res))
    mod = reset()
    res = mod.probe("opencode-go/gibts-nicht", samples=2)
    check("Katalog-Guard: unusable → ok False ohne Sessions",
          res["ok"] is False and res["runs"] == 0 and not CALLS["req"], str(res))

    print("\n[8] run_probe_refs: Status monitoring, known erst nach der Probe")
    mod = reset()
    res = mod.run_probe_refs(["opencode-go/beam-free"], samples=2)
    st = _read_state(mod)
    known = mod._load_known()
    cand = next(c for c in st["candidates"] if c["ref"] == "opencode-go/beam-free")
    check("Kandidat status=monitoring", cand["status"] == "monitoring", str(res))
    check("runs/lastProbe aktualisiert",
          cand["runs"] == 2 and cand["lastProbe"] is not None, str(cand))
    check("Score berechnet", cand["score"] is not None, str(cand["score"]))
    check("known mit Hash + firstSeen/lastSeen geschrieben",
          known["opencode-go/beam-free"]["hash"]
          == mod._metadata_hash(mod.catalog.find_model("opencode-go/beam-free"))
          and known["opencode-go/beam-free"]["firstSeen"] is not None, str(known))
    check("checkedAt gesetzt", st["checkedAt"] is not None)
    res = mod.run_probe_refs(["opencode-go/gibts-nicht"], samples=1)
    check("unbekannter Ref → results meldet 'unbekannt'",
          res["results"]["opencode-go/gibts-nicht"]["status"] == "unbekannt", str(res))

    print("\n[9] Archivierung: Fehler und p95 >= 4000 ms")
    mod = reset()
    mod._single_run = lambda ref, variant="", timeout=mod.PROBE_TIMEOUT: {
        "ok": False, "detail": "stalled", "ttfbMs": None, "totalMs": None}
    res = mod.run_probe_refs(["opencode-go/beam-free"], samples=2)
    st = _read_state(mod)
    check("Fehler → archiviert und ins Archiv verschoben",
          not st["candidates"] and st["archive"]
          and st["archive"][0]["ref"] == "opencode-go/beam-free"
          and st["archive"][0]["status"] == "archived", str(st))
    check("Archiv-Eintrag mit archivedAt + recheckAfter (90 Tage)",
          st["archive"][0]["archivedAt"] is not None
          and st["archive"][0]["recheckAfter"] > st["archive"][0]["archivedAt"],
          str(st["archive"][0]))

    mod = reset()
    _seed_monitoring(mod, {"opencode-go/beam-free": 123.0})
    mod._single_run = lambda ref, variant="", timeout=mod.PROBE_TIMEOUT: {
        "ok": True, "detail": "succeeded", "ttfbMs": 100.0, "totalMs": 4500.0}
    res = mod.run_monitoring(samples=2)
    st = _read_state(mod)
    check("p95 4500 ms >= 4000 → archiviert",
          not st["candidates"] and st["archive"][0]["ref"] == "opencode-go/beam-free",
          str(st["archive"]))
    check("kein Auto-Apply durch Läufe", HERMES_STATE["applied"] == [])

    print("\n[10] recommend() + apply_recommendation(): Fallback bleibt erhalten")
    mod = reset()
    _seed_monitoring(mod, {"opencode-go/beam-free": 500.0,
                           "opencode-go/longcat-free": 120.0,
                           "opencode-go/dash-free": 900.0})
    HERMES_STATE["sot"]["fallback"] = [{"provider": "opencode-go", "model": "glm-5.3-flash"}]
    rec = mod.recommend()
    check("bester Score gewinnt", rec and rec["ref"] == "opencode-go/longcat-free", str(rec))
    res = mod.apply_recommendation()
    check("apply über hermes.apply", HERMES_STATE["applied"] != [] and res.get("applied") is True,
          str(res))
    payload = HERMES_STATE["applied"][0]
    check("primary gesetzt", payload["primary"] == {"provider": "opencode-go",
                                                    "model": "longcat-free"}, str(payload["primary"]))
    check("Fallback unverändert erhalten",
          payload["fallback"] == [{"provider": "opencode-go", "model": "glm-5.3-flash"}],
          str(payload.get("fallback")))
    st = _read_state(mod)
    check("lastApplied vermerkt", st["lastApplied"]["ref"] == "opencode-go/longcat-free",
          str(st["lastApplied"]))
    mod2 = reset()
    try:
        mod2.apply_recommendation("opencode-go/off-model")
        check("katalogungültiger Ref abgelehnt", False, "kein ApplyError")
    except mod2.hermes.ApplyError:
        check("katalogungültiger Ref abgelehnt", True)
    mod3 = reset()
    mod3._save_state(json.loads(json.dumps(DEFAULT_SPEED_STATE)))
    check("ohne Kandidaten: kein Apply",
          mod3.apply_recommendation() == {"applied": False, "detail": "keine Kandidaten"})

    print("\n[11] telemetry(): Aggregation aus dem O19-Snapshot")
    mod = reset()
    now = time.time()
    con = sqlite3.connect(mod.STATE_DB)
    con.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, started_at REAL,
            last_activity_at REAL, input_tokens INTEGER, output_tokens INTEGER,
            api_call_count INTEGER);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
            role TEXT, content TEXT, timestamp REAL, token_count INTEGER, tool_name TEXT);
        CREATE TABLE session_model_usage (session_id TEXT, model TEXT,
            billing_provider TEXT, api_call_count INTEGER, input_tokens INTEGER,
            output_tokens INTEGER, estimated_cost_usd REAL, first_seen REAL, last_seen REAL);
    """)
    con.execute("INSERT INTO sessions VALUES ('s1', 'opencode-go/beam-free', ?, ?, 0, 0, 0)",
                (now - 7200, now - 60))
    con.execute("INSERT INTO session_model_usage VALUES "
                "('s1', 'opencode-go/beam-free', 'opencode-go', 10, 1000, 2000, 0.0, ?, ?)",
                (now - 7200, now - 60))
    con.execute("INSERT INTO session_model_usage VALUES "
                "('s2', 'opencode-go/glm-5.3-flash', 'opencode-go', 2, 500, 700, 0.05, ?, ?)",
                (now - 100000, now - 100000))   # ~27,8 h → außerhalb des 24-h-Fensters
    # Antwortzeit-Näherung: user→assistant 2.0 s und 0.5 s → avg 1.25 s
    con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES "
                "('s1', 'user', 'geheim?', ?)", (now - 3000,))
    con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES "
                "('s1', 'assistant', 'heimliche Antwort', ?)", (now - 2998,))
    con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES "
                "('s1', 'user', 'nochmal?', ?)", (now - 2000,))
    con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES "
                "('s1', 'assistant', 'geheime Antwort', ?)", (now - 1999.5,))
    con.commit()
    con.close()

    tel = mod.telemetry()
    check("available True", tel["available"] is True, str(tel.get("error")))
    check("nur Modell im 24-h-Fenster", [m["model"] for m in tel["models"]]
          == ["opencode-go/beam-free"], str(tel["models"]))
    m0 = tel["models"][0]
    check("Aggregation korrekt", m0["requests"] == 10 and m0["inputTokens"] == 1000
          and m0["outputTokens"] == 2000 and m0["costUsd"] == 0.0
          and m0["provider"] == "opencode-go", str(m0))
    check("Totals summiert nur Fenster", tel["totals"] == {
        "requests": 10, "inputTokens": 1000, "outputTokens": 2000, "costUsd": 0.0},
        str(tel["totals"]))
    resp = (tel.get("responseMs") or {}).get("opencode-go/beam-free") or {}
    check("Antwortzeit-Näherung ≈ 1250 ms (nur Zeitstempel)",
          abs(resp.get("avgMs", 0) - 1250.0) < 50.0 and resp.get("pairs") == 2, str(resp))
    long_tel = mod.telemetry(window_hours=24 * 30)
    check("großes Fenster schließt altes Modell ein",
          {m["model"] for m in long_tel["models"]} == {
              "opencode-go/beam-free", "opencode-go/glm-5.3-flash"},
          str([m["model"] for m in long_tel["models"]]))

    mod = reset({"HERMES_STATE_DB": os.path.join(TMP, "fehlt.db")})
    tel = mod.telemetry()
    check("fehlender Snapshot → available False, kein Crash",
          tel["available"] is False and "models" in tel, str(tel))

    print("\n[12] state(): Form und Inaktive Automatik")
    mod = reset()
    st = mod.state()
    check("state-Keys vorhanden",
          set(("auto", "running", "lastRun", "config", "state", "recommendation",
               "telemetry")) <= set(st), str(sorted(st)))
    check("AUTO=0 → keine Automatik, kein Thread", st["auto"] is False
          and st["running"]["running"] is False)
    check("Telemetrie eingebettet", st["telemetry"]["available"] is False,
          str(st["telemetry"].get("reason", ""))[:80])

    print("\n[13] Automatik: kein Sofort-Probe nach Neustart (Intervall-Schutz)")

    class _StopAfterWait:
        """Fake-Event (thread-frei): eine Loop-Iteration, Ende im ersten wait()."""

        def __init__(self):
            self.calls = []
            self.count = 0

        def is_set(self):
            return self.count > 0

        def wait(self, timeout=None):
            self.calls.append(timeout)
            self.count += 1
            return True

    mod = reset()
    ev = _StopAfterWait()
    mod.loop(ev)                       # kein State → erste Installation: sofort
    st = _read_state(mod)
    check("erste Installation: Loop läuft sofort und setzt checkedAt",
          st["checkedAt"] is not None and len(CALLS["req"]) > 0, str(st.get("checkedAt")))
    check("nach dem Lauf wartet der Loop das volle Intervall",
          ev.calls == [max(60, mod.INTERVAL_HOURS * 3600)], str(ev.calls))

    mod = reset()
    check("fehlender checkedAt → sofort fällig",
          mod._seconds_until_auto_run(now=time.time()) == 0.0)
    st = json.loads(json.dumps(DEFAULT_SPEED_STATE))
    st["checkedAt"] = "kein-zeitstempel"
    _write_state(mod, st)
    check("ungültiger checkedAt → sofort fällig",
          mod._seconds_until_auto_run(now=time.time()) == 0.0, str(st["checkedAt"]))
    last = time.time() - 3600          # 1 h alt, Intervall 24 h → ~23 h Rest
    st["checkedAt"] = mod._now_iso(last)
    _write_state(mod, st)
    remaining = mod._seconds_until_auto_run(now=last + 120)   # deterministisch
    check("Restzeit = Intervall - verstrichene Zeit (deterministisch)",
          abs(remaining - (24 * 3600 - 120)) < 1.0, f"{remaining:.1f}s")
    remaining_live = mod._seconds_until_auto_run()
    check("checkedAt 1 h alt (Live-Zeit) → ~23 h Restzeit",
          abs(remaining_live - 23 * 3600) < 60, f"{remaining_live:.0f}s")
    check("checkedAt exakt am Intervall → sofort fällig",
          mod._seconds_until_auto_run(now=last + 24 * 3600) == 0.0)
    st["checkedAt"] = mod._now_iso(time.time() - 25 * 3600)
    _write_state(mod, st)
    check("checkedAt älter als Intervall → sofort fällig",
          mod._seconds_until_auto_run() == 0.0, str(st["checkedAt"]))
    calls_before = len(CALLS["req"])
    st_before = _read_state(mod)
    mod._seconds_until_auto_run()
    check("Helper startet keine Proben (reine Lese-Logik)",
          len(CALLS["req"]) == calls_before and _read_state(mod) == st_before)

    mod = reset()
    st = json.loads(json.dumps(DEFAULT_SPEED_STATE))
    st["checkedAt"] = mod._now_iso(time.time() - 3600)   # 1 h alt, Intervall 24 h
    _write_state(mod, st)
    ev = _StopAfterWait()
    calls_before = len(CALLS["req"])
    mod.loop(ev)
    check("Neustart mit jungem checkedAt: keine Sofort-Probe",
          len(CALLS["req"]) == calls_before,
          f"{len(CALLS['req']) - calls_before} neue Probe-Calls")
    check("Neustart: Loop wartet die Restzeit (~23 h)",
          len(ev.calls) == 1 and abs(ev.calls[0] - 23 * 3600) < 60, str(ev.calls))

    print("\n[14] Statische Checks: Verdrahtung + Grenzen")
    import ast
    suggest_src = open(os.path.join(BACKEND, "suggest.py"), encoding="utf-8").read()
    tree = ast.parse(suggest_src)
    watch_args = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_event_watch":
            watch_args = [a.arg for a in node.args.args]
    check("suggest._event_watch nimmt on_first_delta an",
          watch_args is not None and "on_first_delta" in watch_args, str(watch_args))

    speed_src = open(os.path.join(BACKEND, "hermes_speed.py"), encoding="utf-8").read()
    check("Speed-Probe-Titel trägt den ntfy-Stumm-Marker",
          "Hestia Speed-Probe (Ausfall-Probe)" in speed_src)

    snap_path = os.path.join(ROOT, "hestia", "telemetry_snapshot.py")
    snap_src = open(snap_path, encoding="utf-8").read()
    ok, detail = True, ""
    try:
        compile(snap_src, snap_path, "exec")
        ast.parse(snap_src)
    except SyntaxError as e:
        ok, detail = False, str(e)
    check("telemetry_snapshot.py kompiliert", ok, detail)
    net_imports = [m for m in ("socket", "urllib", "requests", "http.client", "subprocess")
                   if re.search(rf"^\s*(import|from)\s+{re.escape(m)}\b", snap_src, re.M)]
    check("Seitenwagen ohne Netz-/Subprocess-Imports", not net_imports, str(net_imports))
    check("Backup-API genutzt", ".backup(" in snap_src)
    check("Snapshot atomar (os.replace) + 0644",
          "os.replace" in snap_src and "0o644" in snap_src)

    compose_src = open(os.path.join(ROOT, "hestia", "docker-compose.yml"), encoding="utf-8").read()
    check("Compose: telemetry-snapshot Service",
          "telemetry-snapshot:" in compose_src and "python:3.12-slim" in compose_src)
    check("Compose: harte Isolation des Seitenwagens",
          "network_mode: none" in compose_src and "read_only: true" in compose_src
          and "no-new-privileges:true" in compose_src)
    check("Compose: Quelle ro + Snapshot-Ordner",
          "/etc/docker/hermes/data:/source:ro" in compose_src
          and "/etc/hestia/hermes-telemetry:/snapshot" in compose_src)
    check("Compose: Panel bekommt nur den Snapshot (ro)",
          "/etc/hestia/hermes-telemetry:/hermes-data:ro" in compose_src
          and "HERMES_STATE_DB" in compose_src)
    check("Compose: Panel sieht die Hermes-Live-DB nie",
          [ln.strip() for ln in compose_src.splitlines()
           if "/etc/docker/hermes/data" in ln and ln.strip().startswith("-")]
          == ["- /etc/docker/hermes/data:/source:ro"])

    deploy_src = open(os.path.join(ROOT, ".github/workflows/deploy-hestia.yml"),
                      encoding="utf-8").read()
    check("Deploy: legt /etc/hestia/hermes-telemetry an", "hermes-telemetry" in deploy_src)
    validate_src = open(os.path.join(ROOT, ".github/workflows/validate-hestia-config.yml"),
                        encoding="utf-8").read()
    check("Validate-Workflow läuft die Speed-Tests",
          "test_hermes_speed.py" in validate_src)

    speed_src = open(os.path.join(BACKEND, "hermes_speed.py"), encoding="utf-8").read()
    check("hermes_speed schreibt nie direkt die Hermes-SOT (nur via hermes.apply)",
          "github_api" not in speed_src and "put_files" not in speed_src)

    return 1 if fails else 0


def test_hermes_speed():
    """pytest-Einstieg: führt die gesamte Suite aus."""
    fails.clear()
    assert main() == 0, f"Regressionen: {fails}"


if __name__ == "__main__":
    sys.exit(main())
