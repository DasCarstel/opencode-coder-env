#!/usr/bin/env python3
"""Regressionstests für hestia/backend/failover.py — komplett ohne Netz.

Hintergrund (18.10.2026): Der Ausfallschutz startete die Workspaces stündlich
neu, ohne dass sich etwas änderte. Ursache war eine Kette aus drei Fehlern:
`fast` und `medium` standen bereits auf ihren Ausweich-Modellen, der Swap
schrieb denselben Inhalt, `put_files` erzeugte trotzdem einen Commit — und
`_swap_tier` dispatchte `rollout.yml` zusätzlich (je Tier einmal, also zwei
parallele Rollouts). Diese Tests halten die fünf Schutzregeln fest:

  1. Kein Commit/Rollout, wenn das Tier schon auf dem Ausweich-Modell steht.
  2. Erschöpftes Kontingent (429) ist kein Modellausfall → kein Wechsel.
  3. Das Ausweich-Modell wird vorher geprobt.
  4. Mehrere Wechsel in einem Lauf ergeben genau EINEN Rollout.
  5. Modelle in Quarantäne werden nicht als Ziel gewählt.
  6. `github_api.put_files` schreibt nicht, wenn alle Blobs identisch sind.

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_failover.py
    python3 hestia/ci/test_failover.py
"""
import importlib
import json
import os
import sys
import time
import types

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

OVERRIDES = ".opencode/opencode-model-router.overrides.jsonc"
HEALTH_PATH = ".opencode/hestia-model-health.json"
FALLBACKS_PATH = ".opencode/hestia-fallbacks.json"
AGENTS_PATH = ".opencode/hestia-primary-agents.json"

fails = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# ── Fakes (kein Netz, kein GitHub, kein OpenCode-Server) ─────────────────────
FILES = {}
CALLS = {"put_files": [], "dispatch": []}
BEHAVIOUR = {}


def _fake_github():
    gh = types.ModuleType("github_api")
    gh.OVERRIDES_PATH = OVERRIDES

    def get_file(path):
        return FILES.get(path)

    def get_overrides():
        return {"config": json.loads(json.dumps(BEHAVIOUR["preset"]))}

    def put_files(files, message):
        CALLS["put_files"].append({"paths": sorted(files), "message": message})
        if BEHAVIOUR.get("unchanged"):
            return {"unchanged": True, "sha": "unchanged"}
        FILES.update(files)
        return {"sha": "deadbeefcafe", "html_url": ""}

    def dispatch_workflow(wf, ref=None, inputs=None):
        CALLS["dispatch"].append(wf)
        return {"dispatched": True, "workflow": wf}

    gh.get_file = get_file
    gh.get_overrides = get_overrides
    gh.put_files = put_files
    gh.dispatch_workflow = dispatch_workflow
    return gh


def _fake_catalog():
    c = types.ModuleType("catalog")
    c.usable = lambda ref: (True, "")
    c.find_model = lambda ref: {"variants": ["low", "medium", "high"]}
    return c


def _fake_suggest():
    s = types.ModuleType("suggest")
    s.OC_SERVER_URL = "http://127.0.0.1:9"
    s.OC_SUGGEST_DIR = "/tmp"
    s._req = lambda *a, **k: (200, {"id": "ses_test"})
    s._delete_session = lambda sid: True
    s._event_watch = lambda sid, deadline, stall_timeout, on_progress=None: (
        BEHAVIOUR["probe_results"].pop(0) if BEHAVIOUR["probe_results"] else "succeeded")
    return s


def _fake_accounts():
    a = types.ModuleType("accounts")
    a.state = lambda: {"active": "default3", "accounts": [{"id": "default3"}]}
    a._exhausted = lambda acc: bool(BEHAVIOUR.get("quota_blocked"))
    return a


def _fake_hjsonc():
    h = types.ModuleType("hjsonc")
    h.parse = lambda s: json.loads(s) if s else {}
    h.serialize = lambda cfg: json.dumps(cfg, indent=2)
    return h


for _name, _mod in (("github_api", _fake_github()), ("catalog", _fake_catalog()),
                    ("suggest", _fake_suggest()), ("accounts", _fake_accounts()),
                    ("hjsonc", _fake_hjsonc())):
    sys.modules[_name] = _mod

PRESET = {"presets": {"hestia": {
    "fast": {"model": "opencode-go/deepseek-v4.1-flash", "variant": "low"},
    "medium": {"model": "opencode-go/glm-5.3-flash"},
    "heavy": {"model": "opencode-go/glm-5.3", "variant": "max"},
}}}


def reset(probe_results, preset=None, fallbacks=None, health=None, quota_blocked=False):
    FILES.clear()
    CALLS["put_files"].clear()
    CALLS["dispatch"].clear()
    BEHAVIOUR.clear()
    BEHAVIOUR["probe_results"] = list(probe_results)
    BEHAVIOUR["quota_blocked"] = quota_blocked
    BEHAVIOUR["preset"] = preset or PRESET
    FILES[FALLBACKS_PATH] = json.dumps(fallbacks if fallbacks is not None else {
        "fast": {"model": "opencode-go/deepseek-v4.1-flash", "variant": "low"},
        "medium": {"model": "opencode-go/glm-5.3-flash"},
    })
    FILES[OVERRIDES] = json.dumps(BEHAVIOUR["preset"])
    if health:
        FILES[HEALTH_PATH] = json.dumps(health)
    sys.modules.pop("failover", None)
    mod = importlib.import_module("failover")
    # Im Test läuft genau EIN Prüflauf (real sammelt sich die Fehlschlagsserie
    # über die stündlichen Läufe) — deshalb die Schwelle auf 1 setzen. Seit dem
    # Umbauf liegt die Schwelle als Runtime-Wert an (SOT/STATE), nicht mehr als
    # Modulkonstante → vollständig setzen.
    mod.THRESHOLD = 1
    mod.STATE["threshold"] = 1
    return mod


print("\n[1] Ist-Zustand: Tier steht schon auf dem Ausweich-Modell")
fo = reset(probe_results=["failed:failed", "failed:failed", "succeeded"])
res = fo.run_probes(apply_backup=True)
ovr = [c for c in CALLS["put_files"] if OVERRIDES in c["paths"]]
check("kein Overrides-Commit", not ovr, str(res["swaps"]))
check("kein Rollout", not CALLS["dispatch"], str(CALLS["dispatch"]))
check("Grund wird protokolliert", all("läuft bereits" in v for v in res["swaps"].values()),
      str(res["swaps"]))

print("\n[2] Kontingent erschöpft (429) ist kein Modellausfall")
fo = reset(probe_results=["failed:failed", "failed:failed", "succeeded"], quota_blocked=True,
           fallbacks={"fast": {"model": "openai/gpt-5.1"}, "medium": {"model": "openai/gpt-5.1"}})
res = fo.run_probes(apply_backup=True)
check("kein Wechsel", all("Kontingent" in v for v in res["swaps"].values()), str(res["swaps"]))
check("kein Rollout", not CALLS["dispatch"])
check("Quota-Status im Zustand", res["quota"].get("blocked") is True, str(res["quota"]))

print("\n[3] Ausweich-Modell wird vorher geprobt")
fo = reset(probe_results=["failed:failed", "failed:failed", "succeeded", "timeout", "timeout"],
           fallbacks={"fast": {"model": "openai/gpt-5.1"}, "medium": {"model": "openai/gpt-5.1"}})
res = fo.run_probes(apply_backup=True)
check("kein blinder Wechsel",
      any("antwortet ebenfalls nicht" in v for v in res["swaps"].values()), str(res["swaps"]))
check("kein Rollout", not CALLS["dispatch"])

print("\n[4] Echter Ausfall, gesundes Ausweich-Modell")
fo = reset(probe_results=["failed:failed", "failed:failed", "succeeded", "succeeded"],
           fallbacks={"fast": {"model": "opencode-go/deepseek-v4.1-flash", "variant": "low"},
                      "medium": {"model": "openai/gpt-5.1-pro"}})
res = fo.run_probes(apply_backup=True)
ovr = [c for c in CALLS["put_files"] if OVERRIDES in c["paths"]]
check("genau ein Commit", len(ovr) == 1, f"{len(ovr)} Commits")
# Tier-Wechsel laufen über sync.yml (nur Overrides-Datei in die Container, kein
# Neustart); rollout.yml bleibt für echte Deployments (mode="restart").
check("genau EIN Rollout (sync)", CALLS["dispatch"] == ["sync.yml"], str(CALLS["dispatch"]))
check("Primary-Agents mitgezogen", AGENTS_PATH in ovr[0]["paths"], str(ovr[0]["paths"]))
check("Modell-Gedächtnis geschrieben",
      any(HEALTH_PATH in c["paths"] for c in CALLS["put_files"]),
      str([c["paths"] for c in CALLS["put_files"]]))
check("Gedächtnis nennt Swap-Grund", res["health"]["tiers"].get("medium", {}).get("model")
      == "openai/gpt-5.1-pro", str(res["health"]["tiers"].get("medium")))

print("\n[5] Quarantäne verhindert den Flip-Flop")
fo = reset(probe_results=["failed:failed", "failed:failed", "succeeded"],
           fallbacks={"fast": {"model": "openai/gpt-5.1"}, "medium": {"model": "openai/gpt-5.1"}},
           health={"tiers": {}, "models": {"openai/gpt-5.1": {
               "failStreak": 3, "quarantineUntil": "2099-01-01T00:00:00Z"}}})
res = fo.run_probes(apply_backup=True)
check("Wechsel unterbleibt", all("Quarantäne" in v for v in res["swaps"].values()), str(res["swaps"]))
check("kein Rollout", not CALLS["dispatch"])

print("\n[6] Alles gesund → kein Overrides-Commit, kein Rollout")
fo = reset(probe_results=["succeeded", "succeeded", "succeeded"])
res = fo.run_probes()
ovr = [c for c in CALLS["put_files"] if OVERRIDES in c["paths"]]
check("kein Overrides-Commit", not ovr, str([c["paths"] for c in CALLS["put_files"]]))
check("kein Rollout", not CALLS["dispatch"])
check("Fehlerserie zurückgesetzt", all(v == 0 for v in res["swaps"].values()) if res["swaps"] else True)

# ── Ereignisgesteuerter Ausfallschutz (Plan S-1, kein Netz) ─────────────────
def _failed_event(err_type, retry=None, status=None, ses="ses_a"):
    """SSE-Event nach Schema: {type, data: {sessionID, error: {...}}}."""
    err = {"type": err_type, "message": "Free usage limit reached." if retry else "boom"}
    if status is not None:
        err["status"] = status
    if retry is not None:
        err["retryAfterMs"] = retry
    return {"type": "session.execution.failed", "data": {"sessionID": ses, "error": err}}

FB_ALL = {"fast": {"model": "openai/gpt-5.1"},
          "medium": {"model": "openai/gpt-5.1-pro"},
          "heavy": {"model": "openai/gpt-5.1-max"}}

print("\n[8] Event free-limit → sofortiger Swap (Schwelle 1) + Rückweg-Timer")
fo = reset(probe_results=[], fallbacks=dict(FB_ALL))
fo.STATE["autoFailover"] = True
res = fo.handle_execution_failed(_failed_event("provider.free-limit", retry=28744000, status=429))
check("Klassifikation free_limit", res["kind"] == "free_limit", str(res))
check("Swaps sofort (alle Tiers mit Ausfall-Modell)",
      res["swapped"] == ["fast", "medium", "heavy"], str(res["swaps"]))
check("keine Proben im Event-Pfad (request sparsam)",
      len(BEHAVIOUR["probe_results"]) == 0, str(BEHAVIOUR["probe_results"]))
check("genau EIN sync-Rollout", CALLS["dispatch"] == ["sync.yml"], str(CALLS["dispatch"]))
check("Rückweg-Timer im STATE (freeLimitUntil)",
      (fo.STATE.get("freeLimitUntil") or 0) > fo._now(), str(fo.STATE.get("freeLimitUntil")))
check("paidUntil + primaryModel im Gedächtnis",
      all(res["health"]["tiers"][t].get("paidUntil") and
          res["health"]["tiers"][t].get("primaryModel") for t in fo.TIERS),
      str({t: {"paidUntil": res["health"]["tiers"][t].get("paidUntil"),
               "primaryModel": res["health"]["tiers"][t].get("primaryModel")} for t in fo.TIERS}))
check("Event im Ringpuffer", len(fo.recent_events()) == 1, str(fo.recent_events()))
dedup = fo.handle_execution_failed(_failed_event("provider.free-limit", retry=28744000, ses="ses_a"))
check("identisches Event dedupliziert", dedup.get("deduped") is True, str(dedup))
check("kein zweiter Rollout", CALLS["dispatch"] == ["sync.yml"], str(CALLS["dispatch"]))
check("status_extras liefert die Panel-Felder",
      set(fo.status_extras()) >= {"eventStream", "freeLimitUntil", "lastEvent"},
      str(fo.status_extras()))
check("eventStream zählt als getrennt", fo.status_extras()["eventStream"]["connected"] is False)

print("\n[9] Event quota → Swap ohne Rückweg-Timer")
fo = reset(probe_results=[], fallbacks=dict(FB_ALL))
fo.STATE["autoFailover"] = True
res = fo.handle_execution_failed(_failed_event("provider.quota", status=402))
check("Swaps sofort", res["swapped"] == ["fast", "medium", "heavy"], str(res["swaps"]))
check("kein freeLimitUntil im STATE", fo.STATE.get("freeLimitUntil") is None,
      str(fo.STATE.get("freeLimitUntil")))
check("kein paidUntil im Gedächtnis",
      all(not res["health"]["tiers"][t].get("paidUntil") for t in fo.TIERS))

print("\n[10] Event other → bestehende Schwelle (STATE['threshold'] wirkt)")
fo = reset(probe_results=[], fallbacks=dict(FB_ALL))
fo.STATE["autoFailover"] = True
fo.STATE["threshold"] = 42                      # bewusst absurd hoch
r1 = fo.handle_execution_failed(_failed_event("provider.internal", status=500, ses="s1"))
check("Schwelle nicht erreicht → kein Swap", r1["swapped"] == [], str(r1["swaps"]))
fo.STATE["threshold"] = 2
r2 = fo.handle_execution_failed(_failed_event("provider.internal", status=500, ses="s2"))
check("Serie aus Datei + Schwelle 2 → Swap",
      r2["swapped"] == ["fast", "medium", "heavy"], str(r2["swaps"]))

print("\n[11] Rückweg nach retryAfterMs (Fake-Clock, kein sleep)")
fo = reset(probe_results=[], fallbacks=dict(FB_ALL))
fo.STATE["autoFailover"] = True
real = time.time()
fo._now = lambda: real                          # eingefrorene Uhr vor dem Event
ev = fo.handle_execution_failed(_failed_event("provider.free-limit", retry=2000, ses="ses_r"))
until = fo.STATE.get("freeLimitUntil")
check("paidUntil = now + retryAfterMs", abs(until - (real + 2.0)) < 0.5, str(until))
before_ovr = CALLS["put_files"][:]
fo._now = lambda: real + 1.5
r_early = fo.check_returns()
after_early = CALLS["put_files"][:]
check("vor Ablauf kein Rückweg (kein Commit)", r_early["returned"] == {} and len(after_early) == len(before_ovr),
      str(r_early))
fo._now = lambda: real + 3.0
r_back = fo.check_returns()
ovr = [c for c in CALLS["put_files"] if OVERRIDES in c["paths"]]
check("nach Ablauf Rückweg auf Primärmodelle",
      r_back["returned"] == {"fast": "opencode-go/deepseek-v4.1-flash#low",
                             "medium": "opencode-go/glm-5.3-flash",
                             "heavy": "opencode-go/glm-5.3#max"}, str(r_back))
check("Rückweg committet Overrides", len(ovr) >= 1, str([c["paths"] for c in CALLS["put_files"]]))
check("Rückweg OHNE Probe (keine Proben verbraucht)",
      len(BEHAVIOUR["probe_results"]) == 0, str(BEHAVIOUR["probe_results"]))
check("freeLimitUntil nach Rückweg leer", fo.STATE.get("freeLimitUntil") is None,
      str(fo.STATE.get("freeLimitUntil")))
check("Gedächtnis nach Rückweg geräumt",
      all(not fo.STATE["health"]["tiers"][t].get("paidUntil") for t in fo.TIERS))

print("\n[12] Persistierter failStreak überlebt einen Neustart (Modul-Reload)")
fo = reset(probe_results=[], fallbacks=dict(FB_ALL))
fo.STATE["autoFailover"] = True
fo.STATE["threshold"] = 2                       # sonst fiele der erste Fehler schon unter Schwelle 1
r1 = fo.handle_execution_failed(_failed_event("provider.internal", status=500, ses="s1"))
check("erster Fehler zählt, aber kein Swap", r1["swapped"] == [] and
      all(v == 1 for v in r1["streaks"].values()), str(r1["streaks"]))
sys.modules.pop("failover", None)               # „Neustart" von Hestia
fo2 = importlib.import_module("failover")
fo2.STATE["threshold"] = 1
fo2.STATE["autoFailover"] = True                # Auto-Umschaltung nach Neustart wieder an
r2 = fo2.handle_execution_failed(_failed_event("provider.internal", status=500, ses="s2"))
check("Serie aus Datei abgelesen (streak 2) → Swap",
      r2["swapped"] == ["fast", "medium", "heavy"], str(r2["swaps"]))
check("und genau EIN Rollout", CALLS["dispatch"] == ["sync.yml"], str(CALLS["dispatch"]))

print("\n[13] Fehlender Ausweichref → sichtbarer skipped-Eintrag")
fo = reset(probe_results=[], fallbacks={"fast": {"model": "openai/gpt-5.1"}})
fo.STATE["autoFailover"] = True
res = fo.handle_execution_failed(_failed_event("provider.free-limit", retry=3000, ses="ses_nf"))
check("fast wechselt", res["swapped"] == ["fast"], str(res["swaps"]))
check("medium/heavy sichtbar übersprungen",
      all("skipped: no fallback" in str(res["swaps"].get(t) or "") for t in ("medium", "heavy")),
      str(res["swaps"]))

print("\n[14] STATE['timeout'] wirkt (Regression zum Konstanten-Bug) + restart-Modus")
fo = reset(probe_results=["failed", "failed", "failed"], fallbacks=dict(FB_ALL))
seen_timeouts = []
sys.modules["suggest"]._event_watch = lambda sid, deadline=None, stall_timeout=None, \
    on_progress=None: (seen_timeouts.append(stall_timeout),
                       BEHAVIOUR["probe_results"].pop(0) if BEHAVIOUR["probe_results"]
                       else "succeeded")[1]
fo.STATE["timeout"] = 77
res = fo.run_probes()
check("alle Proben nutzten STATE['timeout']", seen_timeouts and all(t == 77 for t in seen_timeouts),
      str(seen_timeouts))
fo2 = reset(probe_results=[], fallbacks=None)   # frisches Modul ohne Vorgeschichte
CALLS["dispatch"].clear()
fo2._dispatch_rollout(["fast"], "restart")
check("mode='restart' dispatcht rollout.yml", CALLS["dispatch"] == ["rollout.yml"],
      str(CALLS["dispatch"]))

print("\n[7] put_files schreibt nicht, wenn alle Blobs identisch sind")
sys.modules.pop("github_api", None)          # echte Implementierung laden
import github_api as real_gh  # noqa: E402

CONTENT = "Dies ist ein Testinhalt\n"
CONTENT_SHA = "fa38d1e64e9f922f684596d1283d8c2a3ac5671e"   # git hash-object
check("Git-Blob-Hash wie git hash-object", real_gh._blob_sha(CONTENT) == CONTENT_SHA,
      real_gh._blob_sha(CONTENT))

req_calls = []


def fake_req(method, url, body=None):
    req_calls.append((method, url.rsplit("/git/", 1)[-1]))
    if "/git/ref/heads/" in url:
        return 200, {"object": {"sha": "basecommit"}}
    if "/git/commits/basecommit" in url:
        return 200, {"tree": {"sha": "basetree"}}
    if "/git/trees/basetree" in url:
        return 200, {"tree": [
            {"path": OVERRIDES, "sha": real_gh._blob_sha(CONTENT)},
            {"path": "README.md", "sha": "egal"},
        ]}
    return 201, {"sha": "newblob"}


real_gh._req = fake_req
res = real_gh.put_files({OVERRIDES: CONTENT}, "hestia: test")
check("unchanged gemeldet", res.get("unchanged") is True, str(res))
check("keine Schreibaufrufe", not [c for c in req_calls if c[0] in ("POST", "PATCH")],
      str([c for c in req_calls if c[0] in ("POST", "PATCH")]))

req_calls.clear()
res = real_gh.put_files({OVERRIDES: CONTENT + "// neu\n"}, "hestia: test")
check("geänderter Inhalt wird geschrieben", not res.get("unchanged"),
      str([c for c in req_calls if c[0] in ("POST", "PATCH")]))

print()
print(f"FEHLGESCHLAGEN: {len(fails)}" + (f" -> {fails}" if fails else ""))
sys.exit(1 if fails else 0)
