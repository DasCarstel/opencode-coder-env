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
    # über die stündlichen Läufe) — deshalb die Schwelle auf 1 setzen.
    mod.THRESHOLD = 1
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
check("genau EIN Rollout", CALLS["dispatch"] == ["rollout.yml"], str(CALLS["dispatch"]))
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
