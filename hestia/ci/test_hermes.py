#!/usr/bin/env python3
"""Regressionstests für hestia/backend/hermes.py — komplett ohne Netz.

Hintergrund (20.10.2026, Hermes Voice Phase 1): Die SOT kennt fünf erweiterte
Sektionen (`agent`, `providers`, `platform_toolsets`, `platforms`, `skills`),
die das Panel nicht schickt. Ein apply() hat sie stillschweigend gelöscht, weil
`_default_config()`, `_public()` und der new_cfg-Literal nur die sieben
klassischen Keys kannten. Diese Tests halten fest:

  1. Ein Panel-artiger Apply OHNE die erweiterten Sektionen erhält sie in der SOT
     (inkl. profiles, platforms.ntfy und unbekannten agent-Keys).
  2. Ein zweiter identischer Apply erzeugt keinen Commit (keine Diff-Schleife).
  3. Ungültige agent-Werte (api_max_retries, auto_recovery_cycles, reasoning_effort)
     werden abgelehnt.
  4. Ungültige Provider-Timeouts werden abgelehnt.
  5. Unbekannte Toolset-Namen und leere api_server-Listen werden abgelehnt.
  6. model_routes: String-Form → Dict-Form (Round-Trip), Unsinn wird abgelehnt.
  7. `_public()` enthält alle fünf Sektionen (Panel + Änderungserkennung).
  8. Ein teilweiser agent-Payload ergänzt statt zu ersetzen (Deep-Merge).
  9. `skills.disabled` überlebt Apply und Serialisierung; ungültige
     skills-Sektionen werden abgelehnt; Listen werden im Merge ersetzt statt
     konkateniert (hermes.py UND der eingebettete Merge aus apply-hermes.sh).

Läuft mit pytest UND direkt:
    pytest -q hestia/ci/test_hermes.py
    python3 hestia/ci/test_hermes.py
"""
import ast
import importlib
import json
import os
import re
import sys
import types

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

HERMES_PATH = ".opencode/hestia-hermes.jsonc"

# Provider-Allowlist deterministisch halten (hermes.py liest sie beim Import).
os.environ.pop("HESTIA_PROVIDERS", None)

fails = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# ── Fakes (kein Netz, kein GitHub, kein Katalog) ─────────────────────────────
FILES = {}
CALLS = {"put_files": [], "dispatch": []}


def _fake_github():
    gh = types.ModuleType("github_api")

    def get_file(path):
        return FILES.get(path)

    def put_files(files, message):
        CALLS["put_files"].append({"paths": sorted(files), "message": message})
        FILES.update(files)
        return {"sha": "deadbeefcafe", "html_url": ""}

    def dispatch_workflow(wf, ref=None, inputs=None):
        CALLS["dispatch"].append(wf)
        return {"dispatched": True, "workflow": wf}

    gh.get_file = get_file
    gh.put_files = put_files
    gh.dispatch_workflow = dispatch_workflow
    return gh


def _fake_catalog():
    c = types.ModuleType("catalog")
    c.usable = lambda ref: (True, "")
    return c


def _fake_hjsonc():
    h = types.ModuleType("hjsonc")
    h.parse = lambda s: json.loads(s) if s else {}
    h.serialize = lambda cfg: json.dumps(cfg, indent=2)
    return h


for _name, _mod in (("github_api", _fake_github()), ("catalog", _fake_catalog()),
                    ("hjsonc", _fake_hjsonc())):
    sys.modules[_name] = _mod

# SOT-Fixture — entspricht .opencode/hestia-hermes.jsonc plus Erhaltungsbeweisen:
# platforms.ntfy, den (in der echten SOT entfernten) Model-Routen und einem
# unbekannten agent-Key.
SOT = {
    "enabled": True,
    "primary": {"provider": "opencode-go", "model": "beam-free"},
    "fallback": [
        {"provider": "opencode-go", "model": "glm-5.3-flash"},
        {"provider": "opencode-go", "model": "longcat-2.5-preview-free"},
    ],
    "pool": {"followActive": True, "onlyActiveWhenExpiring": False},
    "api_server": {"enabled": True, "port": 8642, "host": "0.0.0.0"},
    "restart": True,
    "profiles": {"kleve": {"enabled": True}},
    "agent": {"reasoning_effort": "low", "api_max_retries": 1,
              "auto_recovery_cycles": 0, "extra_key": "bleibt"},
    "providers": {"opencode-go": {"request_timeout_seconds": 25,
                                  "stale_timeout_seconds": 20}},
    "platform_toolsets": {"api_server": ["web", "todo", "memory", "clarify",
                                         "skills", "terminal"]},
    "skills": {"disabled": ["pdf", "github", "notion", "obsidian", "xlsx"]},
    "platforms": {
        "api_server": {"extra": {"model_routes": {
            "hermes-free": {"model": "opencode-go/beam-free", "reason": "Free voice model"},
            "hermes-paid": {"model": "opencode-go/glm-5.3-flash", "reason": "Paid fallback model"},
        }}},
        "ntfy": {"enabled": True},
    },
}

# Was das Panel heute schickt (ohne die erweiterten Sektionen).
PANEL_PAYLOAD = {
    "enabled": True,
    "primary": {"provider": "opencode-go", "model": "beam-free"},
    "fallback": [
        {"provider": "opencode-go", "model": "glm-5.3-flash"},
        {"provider": "opencode-go", "model": "longcat-2.5-preview-free"},
    ],
    "pool": {"followActive": True, "onlyActiveWhenExpiring": False},
    "api_server": {"enabled": True, "port": 8642, "host": "0.0.0.0"},
    "restart": True,
}


def reset():
    FILES.clear()
    CALLS["put_files"].clear()
    CALLS["dispatch"].clear()
    FILES[HERMES_PATH] = json.dumps(SOT)
    sys.modules.pop("hermes", None)
    mod = importlib.import_module("hermes")
    mod._CACHE.update(at=0.0, cfg=None, error=None)
    return mod


def expect_apply_error(h, payload, name):
    try:
        h.apply(payload)
        check(name, False, "kein ApplyError")
    except h.ApplyError as e:
        check(name, True, str(e))


def main():
    print("\n[1] Panel-Apply ohne erweiterte Sektionen erhält sie in der SOT")
    h = reset()
    payload = json.loads(json.dumps(PANEL_PAYLOAD))
    payload["primary"] = {"provider": "opencode-go", "model": "glm-5.3-flash"}  # erzwingt Commit
    res = h.apply(payload)
    written = json.loads(FILES[HERMES_PATH])
    check("Commit erfolgt", CALLS["put_files"] != [], str(res.get("commit")))
    check("primary übernommen", written["primary"]["model"] == "glm-5.3-flash",
          str(written["primary"]))
    check("agent überlebt", written.get("agent", {}).get("reasoning_effort") == "low",
          str(written.get("agent")))
    check("agent: unbekannte Keys bleiben", written.get("agent", {}).get("extra_key") == "bleibt",
          str(written.get("agent")))
    check("providers überleben",
          written.get("providers", {}).get("opencode-go", {}).get("request_timeout_seconds") == 25,
          str(written.get("providers")))
    check("platform_toolsets überleben",
          written.get("platform_toolsets", {}).get("api_server")
          == ["web", "todo", "memory", "clarify", "skills", "terminal"],
          str(written.get("platform_toolsets")))
    check("platforms.api_server überlebt",
          written.get("platforms", {}).get("api_server", {}).get("extra", {})
          .get("model_routes", {}).get("hermes-free", {}).get("model") == "opencode-go/beam-free",
          str(written.get("platforms")))
    check("platforms.ntfy überlebt", written.get("platforms", {}).get("ntfy") == {"enabled": True},
          str(written.get("platforms")))
    check("profiles überleben", written.get("profiles", {}).get("kleve") == {"enabled": True},
          str(written.get("profiles")))
    check("skills überleben", written.get("skills") == SOT["skills"],
          str(written.get("skills")))
    check("Dispatch erfolgt", CALLS["dispatch"] == ["apply-hermes.yml"], str(CALLS["dispatch"]))

    print("\n[2] Zweiter identischer Apply → kein Commit (keine Diff-Schleife)")
    h = reset()
    payload = json.loads(json.dumps(PANEL_PAYLOAD))
    payload["primary"] = {"provider": "opencode-go", "model": "glm-5.3-flash"}
    h.apply(payload)
    commits_after_first = len(CALLS["put_files"])
    res2 = h.apply(payload)
    check("kein zweiter Commit", len(CALLS["put_files"]) == commits_after_first,
          f"{len(CALLS['put_files'])} Commits, unchanged={res2.get('unchanged')}")

    print("\n[3] agent: unsinnige Werte werden abgelehnt")
    for bad_agent, name in (
            ({"api_max_retries": 0}, "api_max_retries=0 abgelehnt"),
            ({"api_max_retries": True}, "api_max_retries=true (bool) abgelehnt"),
            ({"api_max_retries": "1"}, "api_max_retries='1' (String) abgelehnt"),
            ({"auto_recovery_cycles": -1}, "auto_recovery_cycles=-1 abgelehnt"),
            ({"auto_recovery_cycles": True}, "auto_recovery_cycles=true (bool) abgelehnt"),
            ({"reasoning_effort": "turbo"}, "reasoning_effort=turbo abgelehnt"),
    ):
        h = reset()
        payload = json.loads(json.dumps(PANEL_PAYLOAD))
        payload["agent"] = bad_agent
        expect_apply_error(h, payload, name)

    print("\n[4] providers: unplausible Timeouts werden abgelehnt")
    for bad_prov, name in (
            ({"opencode-go": {"request_timeout_seconds": 0}}, "request_timeout_seconds=0 abgelehnt"),
            ({"opencode-go": {"stale_timeout_seconds": -5}}, "stale_timeout_seconds=-5 abgelehnt"),
            ({"opencode-go": {"request_timeout_seconds": "25"}}, "Timeout als String abgelehnt"),
            ({"opencode-go": {"request_timeout_seconds": True}}, "Timeout als bool abgelehnt"),
    ):
        h = reset()
        payload = json.loads(json.dumps(PANEL_PAYLOAD))
        payload["providers"] = bad_prov
        expect_apply_error(h, payload, name)

    print("\n[5] platform_toolsets: unbekannte Namen / leere api_server-Liste")
    h = reset()
    payload = json.loads(json.dumps(PANEL_PAYLOAD))
    payload["platform_toolsets"] = {"api_server": ["web", "mcp-homeassistant"]}
    h.apply(payload)
    written = json.loads(FILES[HERMES_PATH])
    check("mcp-homeassistant als API-Toolset erlaubt",
          written.get("platform_toolsets", {}).get("api_server")
          == ["web", "mcp-homeassistant"],
          str(written.get("platform_toolsets")))
    h = reset()
    payload = json.loads(json.dumps(PANEL_PAYLOAD))
    payload["platform_toolsets"] = {"api_server": ["web", "not-a-toolset"]}
    expect_apply_error(h, payload, "unbekanntes Toolset abgelehnt")
    h = reset()
    payload = json.loads(json.dumps(PANEL_PAYLOAD))
    payload["platform_toolsets"] = {"api_server": []}
    expect_apply_error(h, payload, "leere api_server-Liste abgelehnt")

    print("\n[6] model_routes: Round-Trip und Ablehnung von Unsinn")
    h = reset()
    payload = json.loads(json.dumps(PANEL_PAYLOAD))
    payload["platforms"] = {"api_server": {"extra": {"model_routes": {
        "hermes-voice": "opencode-go/glm-5.3-flash",   # String-Form, neuer Alias
    }}}}
    h.apply(payload)
    written = json.loads(FILES[HERMES_PATH])
    routes = written["platforms"]["api_server"]["extra"]["model_routes"]
    check("String-Route → Dict-Form",
          routes.get("hermes-voice") == {"model": "opencode-go/glm-5.3-flash"}, str(routes))
    check("bestehende Dict-Route bleibt",
          routes.get("hermes-free", {}).get("reason") == "Free voice model", str(routes))
    check("hermes-paid bleibt",
          routes.get("hermes-paid", {}).get("model") == "opencode-go/glm-5.3-flash", str(routes))
    check("ntfy bleibt bei Plattform-Payload erhalten",
          written["platforms"].get("ntfy") == {"enabled": True}, str(written["platforms"]))
    for bad_platforms, name in (
            ({"api_server": {"extra": {"model_routes": {"x": "ohne-slash"}}}},
             "Route ohne provider/model abgelehnt"),
            ({"api_server": {"extra": {"model_routes": {"-bad": "opencode-go/glm-5.3-flash"}}}},
             "ungültiger Alias abgelehnt"),
            ({"api_server": {"extra": {"model_routes": {"x": "fremd/glm"}}}},
             "fremder Provider abgelehnt"),
            ({"api_server": {"extra": {"model_routes": {
                "x": {"model": "opencode-go/glm-5.3-flash", "reason": 42}}}}},
             "reason als Zahl abgelehnt"),
    ):
        h = reset()
        payload = json.loads(json.dumps(PANEL_PAYLOAD))
        payload["platforms"] = bad_platforms
        expect_apply_error(h, payload, name)

    print("\n[7] _public() enthält alle fünf Sektionen")
    h = reset()
    pub = h._public(h.config(force=True))
    check("agent in _public", pub.get("agent", {}).get("reasoning_effort") == "low",
          str(pub.get("agent")))
    check("providers in _public",
          pub.get("providers", {}).get("opencode-go", {}).get("stale_timeout_seconds") == 20,
          str(pub.get("providers")))
    check("platform_toolsets in _public",
          pub.get("platform_toolsets", {}).get("api_server")
          == ["web", "todo", "memory", "clarify", "skills", "terminal"],
          str(pub.get("platform_toolsets")))
    check("platforms in _public",
          pub.get("platforms", {}).get("api_server", {}).get("extra", {})
          .get("model_routes", {}).get("hermes-paid", {}).get("model")
          == "opencode-go/glm-5.3-flash",
          str(pub.get("platforms")))
    check("skills in _public", pub.get("skills") == SOT["skills"],
          str(pub.get("skills")))

    print("\n[8] Teilweiser agent-Payload ergänzt statt zu ersetzen (Deep-Merge)")
    h = reset()
    payload = json.loads(json.dumps(PANEL_PAYLOAD))
    payload["agent"] = {"reasoning_effort": "high"}
    h.apply(payload)
    written = json.loads(FILES[HERMES_PATH])
    ag = written.get("agent", {})
    check("reasoning_effort aktualisiert", ag.get("reasoning_effort") == "high", str(ag))
    check("api_max_retries bleibt", ag.get("api_max_retries") == 1, str(ag))
    check("auto_recovery_cycles bleibt", ag.get("auto_recovery_cycles") == 0, str(ag))
    check("unbekannter Key bleibt", ag.get("extra_key") == "bleibt", str(ag))

    print("\n[9] skills.disabled überlebt Panel-Apply und Serialisierung")
    h = reset()
    payload = json.loads(json.dumps(PANEL_PAYLOAD))
    payload["primary"] = {"provider": "opencode-go", "model": "glm-5.3-flash"}
    h.apply(payload)
    written = json.loads(FILES[HERMES_PATH])
    check("skills.disabled überlebt",
          written.get("skills", {}).get("disabled") == SOT["skills"]["disabled"],
          str(written.get("skills")))
    pub = h._public(h.config(force=True))
    check("skills in _public", pub.get("skills") == SOT["skills"],
          str(pub.get("skills")))

    print("\n[10] skills: ungültige Sektionen werden abgelehnt")
    for bad_skills, name in (
            ("kein-objekt", "skills als String abgelehnt"),
            ({"unknown": []}, "unbekannter skills-Key abgelehnt"),
            ({"disabled": "pdf"}, "disabled als String abgelehnt"),
            ({"disabled": ["pdf", ""]}, "leerer disabled-Eintrag abgelehnt"),
            ({"disabled": ["pdf", 42]}, "disabled mit Zahl abgelehnt"),
            ({"disabled": ["pdf", "pdf"]}, "disabled-Duplikat abgelehnt"),
    ):
        h = reset()
        payload = json.loads(json.dumps(PANEL_PAYLOAD))
        payload["skills"] = bad_skills
        expect_apply_error(h, payload, name)

    print("\n[11] _deep_merge: Listen werden ersetzt, nicht konkateniert")
    h = reset()
    merged = h._deep_merge(
        {"skills": {"disabled": ["alt-1", "alt-2"]},
         "platforms": {"ntfy": {"themen": ["a", "b"]}}},
        {"skills": {"disabled": ["neu"]},
         "platforms": {"ntfy": {"themen": ["c"]}}},
    )
    check("skills.disabled ersetzt", merged["skills"]["disabled"] == ["neu"], str(merged))
    check("verschachtelte Liste ersetzt",
          merged["platforms"]["ntfy"]["themen"] == ["c"], str(merged))
    check("Dicts verschmelzen weiterhin",
          h._deep_merge({"skills": {"a": 1}}, {"skills": {"b": 2}})["skills"]
          == {"a": 1, "b": 2})

    print("\n[12] apply-hermes.sh: eingebetteter Merge ersetzt Listen ebenfalls")
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apply-hermes.sh")
    script_src = open(script_path, encoding="utf-8").read()
    merge_src = re.search(r"def merge\(target, patch\):.*?target\[key\] = value\n",
                          script_src, re.S)
    if merge_src is None:
        check("merge() aus apply-hermes.sh extrahiert", False, "nicht gefunden")
    else:
        ns = {}
        exec(merge_src.group(0), ns)  # nur die reine Merge-Funktion, kein YAML/Netz
        target = {"skills": {"disabled": ["alt-1", "alt-2"]}}
        ns["merge"](target, {"skills": {"disabled": ["neu-1", "neu-2", "neu-3"]}})
        check("skills.disabled wird ersetzt (SOT-Liste gewinnt)",
              target["skills"]["disabled"] == ["neu-1", "neu-2", "neu-3"], str(target))
        fresh = {}
        ns["merge"](fresh, {"skills": {"disabled": ["nur-sot"]}})
        check("fehlender skills-Key wird angelegt",
              fresh["skills"]["disabled"] == ["nur-sot"], str(fresh))

    print("\n[13] Fastpath hardening")
    # Statische Offline-Prüfung: apply-hermes.sh muss die Fastpath-Skripte und
    # Skills installieren, das Service-Passwort Hermes-only aus OpenBao syncen
    # und dabei niemals Passwort-Werte loggen. Die Quell-Bäume müssen existieren
    # und kompilieren.
    root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    sh_path = os.path.join(root, "hestia", "ci", "apply-hermes.sh")
    sh_src = open(sh_path, encoding="utf-8").read()

    for script in ("notion_fast.py", "obsidian_fast.py"):
        p = os.path.join(root, "hestia", "scripts", script)
        src = open(p, encoding="utf-8").read()
        ok, detail = True, ""
        try:
            compile(src, p, "exec")      # py_compile-Äquivalent, ohne .pyc-Reste
            ast.parse(src)
        except SyntaxError as e:
            ok, detail = False, str(e)
        check(f"fastpath kompiliert: {script}", ok, detail)

    check("install_fast_paths definiert", "install_fast_paths()" in sh_src)
    check("install_fast_paths wird aufgerufen",
          bool(re.search(r"^install_fast_paths\s*$", sh_src, re.M)))
    check("patch_api_tool_surface definiert", "patch_api_tool_surface()" in sh_src)
    check("patch_api_tool_surface wird aufgerufen",
          bool(re.search(r"^patch_api_tool_surface\s*$", sh_src, re.M)))
    check("API-Surface nutzt include_default_mcp_servers=False",
          'include_default_mcp_servers=False' in sh_src)
    check("API-Surface-Neustart berücksichtigt", "API_SURFACE_PATCHED" in sh_src)
    check("sync_hermes_service_password definiert",
          "sync_hermes_service_password()" in sh_src)
    check("sync_hermes_service_password wird aufgerufen",
          bool(re.search(r"^sync_hermes_service_password\s", sh_src, re.M)))
    check("OpenBao-Pfad für Hermes-Service-Passwort",
          "secret/data/mcp/hermes-opencode-service" in sh_src)
    check("AppRole-Fallback vorhanden",
          "OPENBAO_ROLE_ID" in sh_src and "OPENBAO_APPROLE_SECRET_ID" in sh_src)
    check("Fastpath via tar-over-ssh installiert", "tar -x -C /opt/data" in sh_src)
    check("Fastpath-Scripts: chmod 0755 + hermes:hermes",
          "chmod 0755 /opt/data/bin/notion_fast.py /opt/data/bin/obsidian_fast.py" in sh_src
          and re.search(r"chown hermes:hermes [^\n]*/opt/data/bin/notion_fast\.py", sh_src) is not None
          and re.search(r"chown hermes:hermes /opt/data/bin /opt/data/skills", sh_src) is not None)
    check(".env in-place mit chown/chmod 600 geschrieben",
          "cat > $env_file && chown hermes:hermes $env_file && chmod 600 $env_file" in sh_src)
    check("Skills-Installation löscht keine fremden Einträge",
          "--delete" not in sh_src and "rm -rf /opt/data/skills" not in sh_src)
    check("Service-Passwort-Sync abschaltbar",
          "HERMES_SYNC_SERVICE_PASSWORD" in sh_src)

    # Passwort-Werte dürfen nie per echo/print (oder in einer SSH-Kommandozeile)
    # landen — statisch prüfen: Variablen mit PASSWORD/PW im Namen auf solchen
    # Zeilen sind ein Leak-Indiz; nur Längen-Ausgaben sind erlaubt.
    leaks = [ln.strip() for ln in sh_src.splitlines()
             if re.search(r"\b(echo|print)\b", ln)
             and re.search(r"\$\{?\w*(PASSWORD|PW)", ln, re.I)]
    check("kein echo/print von Passwort-Werten", not leaks, " | ".join(leaks[:2]))
    ssh_leaks = [ln.strip() for ln in sh_src.splitlines()
                 if "SSH" in ln and re.search(r"\$\{?\w*(PASSWORD|PW)", ln, re.I)]
    check("Passwort nie in SSH-Kommandozeile", not ssh_leaks, " | ".join(ssh_leaks[:2]))

    # Hermes-only: startup.sh und die OpenCode-Config bleiben außen vor.
    check("startup.sh wird nicht angetastet",
          "coder-templates/shared/startup.sh" not in sh_src)

    sot_path = os.path.join(root, ".opencode", "hestia-hermes.jsonc")
    sot_src = open(sot_path, encoding="utf-8").read()
    check("API-Allowlist enthält kein oCIS-MCP", "mcp-ocis-private" not in sot_src)

    for skill, refs in (("notion-database", ("full-mcp-workflow.md", "schuldenliste-schema.md")),
                        ("obsidian-ocis", ("full-mcp-workflow.md",))):
        d = os.path.join(root, "hestia", "skills", skill)
        check(f"Skill-Verzeichnis vorhanden: {skill}", os.path.isdir(d))
        check(f"SKILL.md vorhanden: {skill}", os.path.isfile(os.path.join(d, "SKILL.md")))
        refs_dir = os.path.join(d, "references")
        has_refs = os.path.isdir(refs_dir) and any(
            f.endswith(".md") and os.path.isfile(os.path.join(refs_dir, f))
            for f in os.listdir(refs_dir)) if os.path.isdir(refs_dir) else False
        check(f"references/ nicht leer: {skill}", has_refs, refs_dir)
        for ref in refs:
            p = os.path.join(refs_dir, ref)
            check(f"Referenz vorhanden: {skill}/references/{ref}", os.path.isfile(p))

    return 1 if fails else 0


def test_hermes_voice():
    """pytest-Einstieg: führt die gesamte Suite aus."""
    fails.clear()
    assert main() == 0, f"Regressionen: {fails}"


if __name__ == "__main__":
    sys.exit(main())
