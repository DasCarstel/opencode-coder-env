#!/usr/bin/env python3
"""Regressionstests für hestia/backend/model_watch.py — ohne Netz, ohne
/app/data (tempfile + SQLite-Tempdatei).

Getestet werden die drei realen Hermes-Zeilenformate (API-Call, Stream-Stall,
Turn-Ende) plus ERROR- und Provider-Auth-Zeilen, die ms-Umrechnung,
p50/p95 (lineare Interpolation), die Degraded-Regeln (p50-Guard, Stalls,
Error-Rate), die Auswahl-Logik (Blockierung entfernen; Voice über Preferred-
Kandidaten; kein Kandidat → keine Aktion), Cooldown, das Dry-Run-Fenster
(kein Apply im Dry-Run — die Apply-Funktion ist gemockt) und die
Sperrlisten-Ablauflogik.

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_model_watch.py
    python3 hestia/ci/test_model_watch.py    (CI-Environment ohne pytest)
"""
import json
import os
import sys
import tempfile
import time

# Temp-Pfade VOR dem Modul-Import setzen — model_watch liest Env beim Import.
_TMP = tempfile.mkdtemp(prefix="hestia-model-watch-test-")
os.environ["HESTIA_MODEL_WATCH_DB"] = os.path.join(_TMP, "model-watch.db")
os.environ["HESTIA_MODEL_WATCH_PATH"] = os.path.join(_TMP, "model-watch.json")
os.environ["HESTIA_MODEL_WATCH_POLICY_PATH"] = os.path.join(_TMP, "policy.json")
os.environ["HESTIA_MODEL_WATCH_STATE_PATH"] = os.path.join(_TMP, "state.json")
os.environ["HESTIA_MODEL_WATCH_LOG_DIR"] = os.path.join(_TMP, "logs")

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

import model_watch as mw  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}"
                                                       if detail else ""))
    if not cond:
        fails.append(name)


RESET_FILES = ("model-watch.json", "state.json", "policy.json")


def reset_dir():
    for name in RESET_FILES:
        p = os.path.join(_TMP, name)
        if os.path.exists(p):
            os.remove(p)


POL = mw.load_policy()

# ── Reale Hermes-Zeilen (Europe/Berlin-Zeitstempel) ─────────────────────────
LINE_CALL = ("2026-10-21 16:29:49,631 INFO [api-9f2c] agent.conversation_loop: "
             "API call #2: model=glm-5.3-flash provider=opencode-go in=6963 "
             "out=34 total=6997 latency=1.0s cache=6848/6963 (98%) id=x")
LINE_STALL = ("2026-10-21 16:40:12,042 WARNING [sess-1] "
              "agent.chat_completion_helpers: Stream stale for 8s "
              "(threshold 8s) — no chunks received. model=glm-5.3-flash "
              "context=~16,393 tokens. Killing connection.")
LINE_TURN = ("2026-10-21 16:40:24,466 INFO [api-9f2c] agent.conversation_loop: "
             "Turn ended: reason=text_response(final) model=glm-5.3-flash "
             "api_calls=8/9223372036854775807 budget=8/9223372036854775807 "
             "tool_turns=7 last_msg_role=assistant response_len=135 "
             "session=api-9f2c")
LINE_ERROR = ("2026-10-21 16:41:00,120 ERROR [api-9f2c] agent.helpers: "
              "boom: upstream 500 model=glm-5.3-flash provider=opencode-go")
LINE_AUTH = ("2026-10-21 16:41:01,130 ERROR [api-9f2c] agent.helpers: "
             "Auth failed: 401 provider=opencode-zen model=qwen-3-max")

print("\n[1] Parser: reale Zeilenformate + ms-Umrechnung")
rows = mw.parse_lines([LINE_CALL, LINE_STALL, LINE_TURN])
kinds = [r["kind"] for r in rows]
check("drei Events erkannt (call/stall/turn)",
      kinds == ["call", "stall", "turn"], str(kinds))
call = rows[kinds.index("call")]
check("Call-Modell als provider/model",
      call["model"] == "opencode-go/glm-5.3-flash", str(call["model"]))
check("latency 1.0s → 1000 ms", call["latency_ms"] == 1000,
      str(call.get("latency_ms")))
check("in/out Tokens", (call["in_tokens"], call["out_tokens"]) == (6963, 34),
      f"{call.get('in_tokens')}/{call.get('out_tokens')}")
check("Call-Provider", call["provider"] == "opencode-go",
      str(call["provider"]))
stall = rows[kinds.index("stall")]
check("Stall-Event mit Detail", stall["kind"] == "stall"
      and "8" in (stall.get("detail") or ""), str(stall))
turn = rows[kinds.index("turn")]
check("api_calls=8", turn["api_calls"] == 8, str(turn["api_calls"]))
check("tool_turns=7", turn["tool_turns"] == 7, str(turn["tool_turns"]))
check("session=api-9f2c", turn["session"] == "api-9f2c", str(turn["session"]))
err = mw.parse_line(LINE_ERROR)
check("ERROR → Modellfehler-Event (provider/model)",
      bool(err) and err[0]["kind"] == "error"
      and err[0]["model"] == "opencode-go/glm-5.3-flash", str(err))
auth = mw.parse_line(LINE_AUTH)
check("Provider-Auth-Fehler (opencode-zen) NICHT Modellfehler", auth == [],
      str(auth))
check("Garbage-Zeile → kein Event", mw.parse_line("kein log)") == [])

print("\n[2] Statistik: p50/p95 linear interpoliert")
check("p50 [1000..4000]", mw._pctl([1000, 2000, 3000, 4000], 50) == 2500,
      str(mw._pctl([1000, 2000, 3000, 4000], 50)))
check("p95 [1000..4000]", mw._pctl([1000, 2000, 3000, 4000], 95) == 3850,
      str(mw._pctl([1000, 2000, 3000, 4000], 95)))
check("p50 leere Liste → None", mw._pctl([], 50) is None)

print("\n[3] Degraded-Regeln (Aggregation über bekannte Zahlenreihen)")
TS = "2026-10-20T12:00:00"

# (a) 8 Calls, einer davon 17,5-s-Ausreißer → NICHT degraded
vals_a = [1000] * 7 + [17500]
rows_a = [(TS, "opencode-go/m", v, 1) for v in vals_a]
stats_a = mw._aggregate(rows_a, [])
na = stats_a["opencode-go/m"]
check("(a) p20-8-calls p50 bleibt klein (1000 ms)", na["p50Ms"] == 1000,
      str(na["p50Ms"]))
check("(a) 8 Calls mit Ausreißer NICHT degraded",
      not mw._degraded(na, POL), str(mw._degraded_reasons(na, POL)))

# (b) 20 Calls, p50 5 s / p95 9 s → degraded
vals_b = [4000] * 10 + [6000] * 8 + [9000] * 2
rows_b = [(TS, "opencode-go/m", v, 1) for v in vals_b]
nb = mw._aggregate(rows_b, [])["opencode-go/m"]
check("(b) p50 5000", nb["p50Ms"] == 5000, str(nb["p50Ms"]))
check("(b) p95 9000", nb["p95Ms"] == 9000, str(nb["p95Ms"]))
check("(b) 20 Calls p50 5s/p95 9s → degraded", mw._degraded(nb, POL))

# (c) 5 Calls + 2 Stalls → degraded (Stall-Events mit nacktem Modellnamen)
rows_c = [(TS, "opencode-go/m", 1000, 1)] * 5
events_c = [(TS, "m", "stall"), (TS, "m", "stall")]
nc = mw._aggregate(rows_c, events_c)["opencode-go/m"]
check("(c) Stalls suffix-gematcht", nc["stalls"] == 2, str(nc))
check("(c) 5 Calls/2 Stalls → degraded", mw._degraded(nc, POL))

# (d) 10 Calls, 2 Fehler → degraded (Error-Rate 0.2 > 0.10)
rows_d = [(TS, "opencode-go/m", 1000, 1)] * 10
events_d = [(TS, "opencode-go/m", "error"), (TS, "opencode-go/m", "error")]
nd = mw._aggregate(rows_d, events_d)["opencode-go/m"]
check("(d) errorRate 0.2", nd["errorRate"] == 0.2, str(nd["errorRate"]))
check("(d) 10 Calls/2 Fehler → degraded", mw._degraded(nd, POL))

# ── Public-Fixture für Auswahl/Durchsetzung ─────────────────────────────────
PUBLIC = {
    "enabled": True,
    "primary": {"provider": "opencode-go", "model": "glm-5.3-flash"},
    "fallback": [{"provider": "opencode-go", "model": "glm-5.3"}],
    "pool": {"followActive": False, "onlyActiveWhenExpiring": False},
    "api_server": {"enabled": True, "port": 8642, "host": "0.0.0.0"},
    "restart": True,
    "profiles": {"kleve": {
        "enabled": True,
        "primary": {"provider": "opencode-go", "model": "glm-5.3"},
        "fallback": [{"provider": "opencode-go", "model": "bad-model"}],
        "pool": {"followActive": False, "onlyActiveWhenExpiring": False}}},
    "agent": {"reasoning_effort": "high"},
    "providers": {},
    "platform_toolsets": {"api_server": ["web", "skills"]},
    "skills": {"disabled": []},
    "platforms": {"api_server": {"extra": {"model_routes": {
        "hermes-voice": {"provider": "opencode-go", "model": "bad-model"},
    }}}},
}
REF_BAD = "opencode-go/bad-model"

apply_calls = []


def fake_apply(payload):
    apply_calls.append(json.loads(json.dumps(payload)))
    return {"applied": True, "unchanged": False}


def prep_run():
    mw._current_public = lambda: json.loads(json.dumps(PUBLIC))
    mw._apply = fake_apply
    apply_calls.clear()


def write_block(refs, ttl=3600):
    doc = {"blocked": []}
    for ref in refs:
        provider, model = mw._split_ref(ref)
        doc["blocked"].append({
            "model": model, "provider": provider, "reason": "test",
            "blocked_at": "2020-01-01T00:00:00",
            "until": time.strftime(mw.FMT, time.gmtime(time.time() + ttl)),
            "snapshot": {}})
    mw._save_blocks(doc)


def write_state(**fields):
    mw._save_state(dict(fields))


def voice_route_of(p):
    return ((((p.get("platforms") or {}).get("api_server") or {})
             .get("extra") or {}).get("model_routes") or {}).get("hermes-voice")


print("\n[4] Auswahl: blockierte Voice-Route → Preferred-Kandidat")
reset_dir()
write_block([REF_BAD])
PRE_ENFORCE_FROM = time.strftime(mw.FMT, time.gmtime(time.time() * 0 - 24 * 3600))
write_state(enforceFrom=PRE_ENFORCE_FROM)     # Dry-Run vorbei
prep_run()
res = mw.run_once()
check("Apply gelaufen", res.get("applied") is True, str(res))
check("Apply-Funktion genau einmal", len(apply_calls) == 1,
      str(len(apply_calls)))
if apply_calls:
    p = apply_calls[0]
    check("Voice-Route auf Preferred-Kandidat",
          voice_route_of(p) == {"provider": "opencode-go",
                                "model": "beam-free"},
          str(voice_route_of(p)))
    check("Primary unverändert (nicht blockiert)",
          p["primary"] == {"provider": "opencode-go",
                           "model": "glm-5.3-flash"}, str(p["primary"]))
    check("blockiertes Modell nirgends mehr",
          "bad-model" not in json.dumps(p), str(
              [p["profiles"]["kleve"]["fallback"]]))
    check("keine Änderung an platform_toolsets/skills/agent/providers",
          p["platform_toolsets"] == PUBLIC["platform_toolsets"]
          and p["skills"] == PUBLIC["skills"]
          and p["agent"] == PUBLIC["agent"]
          and p["providers"] == PUBLIC["providers"])
    check("Fallback-Filter je Profil (blockierte entfernt, Preferred als Ersatz)",
          p["profiles"]["kleve"]["fallback"] == [
              {"provider": "opencode-go", "model": "beam-free"}],
          str(p["profiles"]["kleve"]["fallback"]))
check("lastApplyAt im State",
      bool(mw._load_state().get("lastApplyAt")),
      str(mw._load_state().get("lastApplyAt")))

print("\n[5] Auswahl: kein Kandidat → keine Aktion + Alarm")
pol5 = dict(POL, preferred=["opencode-go/x", "opencode-go/y"])
blocked5 = {REF_BAD, "opencode-go/x", "opencode-go/y"}
pending, alarms, plan5 = mw.plan_changes(PUBLIC, blocked5, pol5)
check("keine Voice-Änderung im Plan", plan5.get("voice") is None,
      str(plan5.get("voice")))
check("kein pending für die Voice-Route",
      not [p for p in pending if p["kind"] == "voice"], str(pending))
check("Alarm gemeldet", bool(alarms) and "Voice" in alarms[0], str(alarms))

print("\n[6] Auswahl: Primary blockiert → Fallback-Nachrücken")
reset_dir()
pol6 = dict(POL)
pub6 = json.loads(json.dumps(PUBLIC))
pub6["primary"] = {"provider": "opencode-go", "model": "bad-model"}
pending, alarms, plan6 = mw.plan_changes(pub6, {REF_BAD}, pol6)
check("Primary-Nachrücken: erster sauberer Fallback",
      plan6["primary"] == {"provider": "opencode-go", "model": "glm-5.3"},
      str(plan6["primary"]))
check("pending enthält primary-Wechsel",
      any(p["kind"] == "primary" for p in pending), str(pending))

print("\n[7] Sperrliste: Ablauf entfernt Eintrag")
expired = {"model": "old-model", "provider": "opencode-go", "reason": "test",
           "blocked_at": "2020-01-01T00:00:00", "until": "2020-01-02T00:00:00",
           "snapshot": {}}
mw._save_blocks({"blocked": [expired]})
doc = mw._prune_blocks()
check("abgelaufener Eintrag entfernt", doc["blocked"] == [],
      str(doc["blocked"]))
check("Sperrdatei nachgeschrieben", mw._load_blocks()["blocked"] == [],
      str(mw._load_blocks()))

print("\n[8] Dry-Run: kein Apply, enforceFrom = Start + Fenster")
reset_dir()
prep_run()
res = mw.run_once()
st = mw._load_state()
check("Dry-Run: kein Apply", res.get("applied") is False, str(res))
check("Dry-Run: Apply-Funktion nie gerufen", apply_calls == [],
      str(apply_calls))
ef = mw.ts_epoch(st.get("enforceFrom"))
check("enforceFrom ~ Start + 48 h",
      ef is not None and 47 * 3600 < ef - time.time() <= 48.2 * 3600,
      f"delta={(ef or 0) - time.time():.0f}s")
check("mode dry-run im Ergebnis", res.get("mode") == "dry-run",
      str(res.get("mode")))

print("\n[9] Cooldown: pending ja, aber Apply ausgesetzt")
reset_dir()
last = time.strftime(mw.FMT, time.gmtime(time.time() - 60))
write_state(enforceFrom=PRE_ENFORCE_FROM, lastApplyAt=last)
prep_run()
res = mw.run_once()
st = mw._load_state()
check("Coolddown: kein Apply", res.get("applied") is False, str(res))
check("Cooldown: Apply-Funktion nie gerufen",
      bool(apply_calls) is False, str(apply_calls))

print("\n[10] state() wirft nie + Sperrliste/Modelle sichtbar")
reset_dir()
write_block([REF_BAD])
s = mw.state()
check("Grundstruktur",
      bool(s) and s.get("enabled") is not None
      and isinstance(s.get("models"), list)
      and isinstance(s.get("blocked"), list)
      and isinstance(s.get("pending"), list)
      and isinstance(s.get("actions"), list)
      and "thresholds" in s and "enforceFrom" in s, str(sorted(s.keys())))
check("blockiert im State",
      any(b.get("model") == "bad-model" for b in s.get("blocked", [])),
      str(s.get("blocked")))

print("\n[11] Log-Lesen inkrementell + SQLite-Statistik")
reset_dir()
logdir = os.path.dirname(mw.LOG_FILE)
os.makedirs(logdir, exist_ok=True)
st0 = mw._load_state()
with open(mw.LOG_FILE, "w", encoding="utf-8") as f:
    f.write(LINE_CALL + "\n" + LINE_STALL + "\n" + LINE_TURN + "\n"
            + LINE_ERROR + "\n")
ingested = mw._ingest_logs(st0)
mw._save_state(st0)
check("4 Events im ersten Lauf", ingested == 4, str(ingested))
stats = mw.model_stats(24)
got = stats.get("opencode-go/glm-5.3-flash", {})
check("calls/prov/model in DB", got.get("calls") == 1, str(got))
check("Stall im Modell aggregiert", got.get("stalls") == 1, str(got))
check("Errors im Modell aggregiert", got.get("errors") == 1, str(got))
check("latency ms aus Log", got.get("p50Ms") == 1000, str(got.get("p50Ms")))
# zweiter Lauf ohne Ergebnis: offset angehalten
st1 = mw._load_state()
check("kein erneutes Ingest (Offset-Advanced)",
      mw._ingest_logs(st1) == 0, str(mw._ingest_logs(st1)))

print()
if not fails:
    print("ALLE CHECKS OK")
sys.exit(1 if fails else 0)
