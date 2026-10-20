#!/usr/bin/env python3
"""server.py — Hestia Backend (Python stdlib, kein npm-Build).

REST-API des Model-Panels. Läuft als eigener Container auf dem Coder-Host:
  - Katalog + Preise:  OpenCode-Server des Infrastructure-Containers (/api/model)
  - Overrides (SOT):   GitHub Contents API  (.opencode/opencode-model-router.overrides.jsonc)
  - Vorschlag:         OpenCode-Session-Dispatch (gleiche REST-Muster wie Hermes)
  - Rollout:           passiert via CI/CD (Workflow-Trigger bei Push; /api/rollout = Force)

Endpoints (alle unter Basic Auth):
  GET  /api/health            Status + Konnektivität
  GET  /api/catalog           Live-Katalog (inkl. neuer OpenCode-Go-Modelle)
  GET  /api/config            aktives Tier-Setup (+SHA, Commit-URL)
  POST /api/apply             Tier-Wechsel validieren → Override committen → CI/CD
  POST /api/suggest           KI-Analyse → 3-Tier-Vorschlag (JSON)
  POST /api/rollout           Force-Rollout (Workflow-Dispatch)
  GET  /api/accounts          Budget-Status aller Provider-Konten (inkl. Empfehlung)
  POST /api/accounts/check    Konten sofort neu abfragen (kein Wechsel)
  POST /api/accounts/switch   Konto-Wechsel anstoßen (id optional = Empfehlung)
  GET  /api/hermes            Hermes-Zustand (SOT + Health + Workflow)
  POST /api/hermes/apply      Hermes Primary/Fallback/Pool setzen (SOT-Commit + CI)
  GET  /api/hermes/speed      Hermes-Speed: Scan-Zustand + O19-Telemetrie + Empfehlung
  POST /api/hermes/speed/run  Speed-Lauf starten (discovery|monitoring|probe, im Hintergrund)
  POST /api/hermes/speed/apply Speed-Empfehlung als Hermes-Primary anwenden (SOT via hermes.apply)
  GET  /api/failover/status   Ausfallschutz-Zustand (autoFailover/interval/threshold/timeout/…)
  POST /api/failover/settings Ausfallschutz-Einstellungen (SOT-Commit + STATE-Update)
  GET  /api/suggest/status    Live-Zustand + auto (Einstellungen/Fingerprint/Zeiten) + proposal
  POST /api/suggest/settings  Automatik-Einstellungen (SOT-Commit + STATE-Update)
  GET  /api/benchmarks/status Benchmark-Zustand (Quelle/Alter/Zähler/fehlende Refs)
  POST /api/benchmarks/refresh Benchmarks sofort neu holen (OpenRouter, read-only)
  GET  /*                     statisches Frontend (dist/)
"""
import base64
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import catalog  # noqa: E402
import github_api  # noqa: E402
from github_api import ConfigError  # noqa: E402
import hjsonc  # noqa: E402
import suggest as suggest_mod  # noqa: E402
import failover as failover_mod  # noqa: E402
import accounts as accounts_mod  # noqa: E402
import hermes as hermes_mod  # noqa: E402
import hermes_speed as hermes_speed_mod  # noqa: E402
import benchmarks as benchmarks_mod  # noqa: E402
import dcp as dcp_mod  # noqa: E402

STATIC_DIR = os.environ.get(
    "HESTIA_STATIC", os.path.join(os.path.dirname(BASE_DIR), "frontend", "dist")
)
HESTIA_USER = os.environ.get("HESTIA_USER", "hestia")
HESTIA_PASSWORD = os.environ.get("HESTIA_PASSWORD") or secrets.token_urlsafe(24)
PRESET_PROVIDER = os.environ.get("HESTIA_PRESET_PROVIDER", "hestia")
# Erlaubte Provider für Tier-/Agent-Auswahl (Komma-getrennt).
# "opencode" = OpenCode Zen (enthält die kostenlosen Modelle, z. B. cat-jam-free).
ALLOWED_PROVIDERS = [p.strip() for p in
                     os.environ.get("HESTIA_PROVIDERS", PRESET_PROVIDER).split(",") if p.strip()]
# Traefik-SSO: forwardAuth (authentik@file) setzt X-authentik-*; zusätzlich malgré
# ein Proxy-Secret-Header (customRequestHeaders-Middleware), damit direkte
# Zugriffe auf 10.0.10.17:49400 die Header nicht fälschen können.
HESTIA_PROXY_SECRET = os.environ.get("HESTIA_PROXY_SECRET", "")
PROXY_SECRET_HEADER = "X-Hestia-Proxy-Secret"

# Live-Zustand des (einzigen) laufenden KI-Vorschlags — für /api/suggest/status
SUGGEST_STATE = {
    "running": False,
    "startedAt": None,
    "model": None,
    "source": None,
    "attempt": None,
    "total": None,
    "phase": None,
    "note": None,
    "finishedAt": None,
}
SUGGEST_LOCK = __import__("threading").Lock()
TIER_DEFS = ("fast", "medium", "heavy")
# Primary-Agents: abgeleitet aus @medium, im Panel manuell überschreibbar
PRIMARY_AGENTS = ("build", "plan")
AGENTS_PATH = os.environ.get("HESTIA_AGENTS_PATH", ".opencode/hestia-primary-agents.json")
FALLBACKS_PATH = os.environ.get("HESTIA_FALLBACKS_PATH", ".opencode/hestia-fallbacks.json")
FAILOVER_SETTINGS_PATH = os.environ.get(
    "HESTIA_FAILOVER_SETTINGS_PATH", ".opencode/hestia-failover.jsonc"
)
VARIANT_RANK = {"minimal": 1, "none": 2, "low": 3, "medium": 4, "thinking": 4,
                "high": 5, "xhigh": 6, "max": 7}


def _log(msg: str):
    print(f"[hestia] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


# ── Apply: Validierung + neues Overrides-JSONC ─────────────────────────────
class ApplyError(Exception):
    pass


def validate_payload(payload: dict) -> dict:
    tiers = payload.get("tiers") or {}
    normalized = {}
    for tier in TIER_DEFS:
        t = tiers.get(tier) or {}
        model_ref = (t.get("model") or "").strip()
        variant = (t.get("variant") or "").strip()
        if not model_ref:
            raise ApplyError(f"Tier '{tier}': model fehlt")
        ok, res = catalog.usable(model_ref)
        if not ok:
            raise ApplyError(res)
        m = res
        if model_ref.split("/", 1)[0] not in ALLOWED_PROVIDERS:
            raise ApplyError(
                f"Tier '{tier}': Provider '{model_ref.split('/', 1)[0]}' nicht erlaubt "
                f"(erlaubt: {', '.join(ALLOWED_PROVIDERS)})"
            )
        if variant and variant not in (m["variants"] or []):
            raise ApplyError(
                f"Tier '{tier}': Variante '{variant}' nicht verfügbar für {model_ref} — "
                f"erlaubt: {', '.join(m['variants'] or ['—'])}"
            )
        normalized[tier] = {
            "model": model_ref,
            "variant": variant or None,
            "costRatio": tier_cost_ratio(tier, m),
        }
    return normalized


def tier_cost_ratio(tier: str, m: dict) -> int:
    """Preset-Kosten-Ratio relativ zum fast-Tier (1/5/12-Ladder)."""
    mapping = {"fast": 1, "medium": 5, "heavy": 12}
    return mapping.get(tier, 1)


def build_config(current: dict, tiers: dict) -> dict:
    preset = PRESET_PROVIDER
    cfg = dict(current or {})
    cfg["activePreset"] = preset
    presets = dict(cfg.get("presets") or {})
    prev = presets.get(preset) or {}
    new_preset = {}
    for tier in TIER_DEFS:
        old = prev.get(tier) or {}
        new_preset[tier] = {
            "model": tiers[tier]["model"],
            "costRatio": tiers[tier]["costRatio"],
            "description": old.get("description", ""),
            "steps": old.get("steps", {"fast": 30, "medium": 50, "heavy": 120}[tier]),
            "whenToUse": old.get("whenToUse", []),
        }
        if tiers[tier]["variant"]:
            new_preset[tier]["variant"] = tiers[tier]["variant"]
    presets[preset] = new_preset
    cfg["presets"] = presets
    return cfg



# ── Primary-Agents (build/plan): Ableitung aus @medium + Overrides ──────────
def highest_variant(model_ref: str) -> str:
    """Höchste Effort-Variante eines Modells laut Live-Katalog ("" = keine)."""
    m = catalog.find_model(model_ref)
    if not m:
        return ""
    ranked = sorted([v for v in (m.get("variants") or []) if v in VARIANT_RANK],
                    key=lambda v: VARIANT_RANK[v])
    return ranked[-1] if ranked else ""


def agents_from_overrides() -> dict:
    """Manuelle Build/Plan-Overrides aus dem Repo lesen ({build:{model,variant},...})."""
    try:
        raw = github_api.get_file(AGENTS_PATH)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def saved_fallbacks() -> dict:
    """Persistierte Ausfall-Modelle aus dem Repo lesen.

    Liefert immer {"fast": {...}|None, "medium": {...}|None}; leere oder
    defekte Dateien (und GitHub-Fehler) werden stillschweigend als {} gelesen,
    damit /api/config nicht an einem Fallback-Problem scheitert.
    """
    data = {}
    try:
        raw = github_api.get_file(FALLBACKS_PATH)
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {t: (data.get(t) or None) for t in ("fast", "medium")}


def effective_agents(preset: dict, overrides: dict) -> dict:
    """Effektive Primär-Agenten: manuell > hergeleitet aus @medium.

    build = @medium (Standardvariante) · plan = @medium (höchste Variante)
    """
    medium = preset.get("medium") or {}
    med_ref = (medium.get("model") or "").strip()
    med_var = (medium.get("variant") or "").strip()
    out = {}
    for agent in PRIMARY_AGENTS:
        ov = overrides.get(agent) or {}
        ref = (ov.get("model") or "").strip()
        var = (ov.get("variant") or "").strip()
        # "source": "manual" => im Panel gewählt; "derived" => aus @medium abgeleitet
        manual = (ov.get("source") == "manual") or (bool(ref) and not ov.get("source"))
        if not ref:                                  # hergeleitet
            ref = med_ref
            var = med_var if agent == "build" else highest_variant(med_ref)
        out[agent] = {
            "model": ref,
            "variant": var or None,
            "manual": manual,
            "source": "manuell" if manual else "hergeleitet (Medium)",
        }
    return out


def agents_to_display(eff: dict) -> dict:
    """Speicherbare Felder inkl. Herkunft (manual|derived) für den Editor."""
    return {a: {"model": eff[a]["model"], "variant": eff[a]["variant"] or "",
                "source": "manual" if eff[a]["manual"] else "derived"}
            for a in PRIMARY_AGENTS}


# ── HTTP-Handler ───────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "Hestia/1.0"

    def log_message(self, fmt, *args):  # weniger Lograuschen
        pass

    def _authorized(self) -> bool:
        # SSO-Pfad: Traefik markiert authentifizierte Requests mit dem Proxy-Secret
        if HESTIA_PROXY_SECRET:
            if self.headers.get(PROXY_SECRET_HEADER, "") == HESTIA_PROXY_SECRET \
                    and self.headers.get("X-authentik-username"):
                return True
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
        except Exception:
            return False
        ok = secrets.compare_digest(user, HESTIA_USER) and secrets.compare_digest(pw, HESTIA_PASSWORD)
        return ok

    def _denied(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="hestia"')
        self.send_header("Content-Type", "application/json")
        body = b'{"error": "auth required"}'
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode())

    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        fs_path = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not fs_path.startswith(os.path.abspath(STATIC_DIR)):
            self._json({"error": "forbidden"}, 403)
            return
        if not os.path.isfile(fs_path):
            fs_path = os.path.join(STATIC_DIR, "index.html")  # SPA fallback
        if not os.path.isfile(fs_path):
            self._json(
                {"error": "frontend dist fehlt — erst Svelte-Frontend bauen (frontend/README zhält Schritte)"},
                404,
            )
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript",
            ".css": "text/css",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
        }.get(os.path.splitext(fs_path)[1], "application/octet-stream")
        with open(fs_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # /api/health ist bewusst unauthentifiziert (Monitoring-freundlich, keine
        # sensiblen Inhalte) — alles andere verlangt Basic/SSO.
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._health()
        if not self._authorized():
            return self._denied()
        if path == "/api/catalog":
            try:
                models = catalog.fetch_models()
                # Budget (Go-Abo) + gültiger Preis inkl. Kontextstufe und Zeitfaktor
                # je Modell — Quelle: .opencode/hestia-benchmarks.jsonc (budgets).
                # Fehler dürfen den Katalog nie reißen (Panel zeigt dann "—").
                try:
                    import gobudget
                    for m in models:
                        if isinstance(m, dict):
                            m["budget"] = gobudget.budget_for(m.get("ref") or "")
                            m["price"] = gobudget.price_for(m)
                except Exception as e:  # noqa: BLE001
                    _log(f"catalog budget error: {e}")
                # Verbrauchsampel je Modell (aus den Sitzungen des OpenCode-Servers).
                try:
                    import usage
                    for m in models:
                        if isinstance(m, dict):
                            m["usage"] = usage.ampel(m.get("ref") or "")
                except Exception as e:  # noqa: BLE001
                    _log(f"catalog usage error: {e}")
                payload = {"models": models, "providers": ALLOWED_PROVIDERS,
                           "presetProvider": PRESET_PROVIDER}
                # Benchmarks dürfen den Katalog-Aufruf nie reißen (Quelle OpenRouter).
                try:
                    refs = [m.get("ref") for m in models if isinstance(m, dict)]
                    # Alle vorhandenen Felder — die Detailzeile der neuen
                    # Tabellen-UI zeigt jeden Benchmark, nicht nur die SOT-Auswahl.
                    payload["benchmarks"] = benchmarks_mod.for_refs(refs, all_fields=True)
                    payload["benchmarksStatus"] = benchmarks_mod.state()
                except Exception as e:  # noqa: BLE001
                    _log(f"catalog benchmarks error: {e}")
                    payload["benchmarks"] = {}
                    payload["benchmarksStatus"] = {"error": str(e)}
                return self._json(payload)
            except Exception as e:
                return self._json({"error": str(e)}, 502)
        if path == "/api/config":
            try:
                r = github_api.get_overrides()
                cfg = r["config"]
                preset = cfg.get("presets", {}).get(PRESET_PROVIDER, {})
                overrides = agents_from_overrides()
                eff = effective_agents(preset, overrides)
                return self._json({
                    "activePreset": cfg.get("activePreset"),
                    "presetProvider": PRESET_PROVIDER,
                    "tiers": {t: preset.get(t, {}) for t in TIER_DEFS},
                    "primaryAgents": eff,
                    "fallbacks": saved_fallbacks(),
                    "modes": list(cfg.get("modes") or {}),
                    "sha": r["sha"],
                    "html_url": r["html_url"],
                    "updated": bool(r["content"]),
                })
            except ConfigError as e:
                return self._json({"error": str(e)}, 404)
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        if path == "/api/rollout/status":
            try:
                wf = os.environ.get("ROLLOUT_WORKFLOW", "rollout.yml")
                return self._json(github_api.latest_run(wf))
            except ConfigError as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        if path == "/api/failover/status":
            with failover_mod.LOCK:
                st = dict(failover_mod.STATE)
            return self._json(st)
        if path == "/api/suggest/status":
            with SUGGEST_LOCK:
                state = dict(SUGGEST_STATE)
            if state.get("running") and state.get("startedAt"):
                state["elapsed"] = round(time.time() - state["startedAt"], 1)
            try:
                auto, proposal = suggest_mod.auto_status()
            except Exception as e:  # noqa: BLE001 — Status darf nie ausfallen
                _log(f"suggest auto status error: {e}")
                auto, proposal = {}, None
            state["auto"] = auto
            state["proposal"] = proposal
            return self._json(state)
        if path in ("/api/accounts", "/api/accounts/status"):
            return self._json(accounts_mod.state())
        if path in ("/api/benchmarks/status", "/api/benchmarks"):
            try:
                return self._json(benchmarks_mod.state())
            except Exception as e:  # noqa: BLE001 — Status darf nie ausfallen
                _log(f"benchmarks status error: {e}")
                return self._json({"error": str(e)}, 500)
        if path in ("/api/hermes", "/api/hermes/status"):
            # Der Hermes-Status darf nie ausfallen: eine Ausnahme hier beendet die
            # Verbindung ohne Antwort, und das Panel zeigt dann nur noch "nicht
            # erreichbar" — unabhaengig von der echten Ursache. Gleiche Absicherung
            # wie bei /api/catalog.
            try:
                return self._json(hermes_mod.state())
            except Exception as e:  # noqa: BLE001
                _log(f"hermes status error: {e}")
                return self._json({"error": str(e)}, 502)
        if path in ("/api/hermes/speed", "/api/hermes/speed/status"):
            # Hermes-Speed + O19-Telemetrie: gleiche Nie-ausfallen-Absicherung wie
            # /api/hermes (Telemetrie liest den state.db-Snapshot read-only).
            try:
                return self._json(hermes_speed_mod.state())
            except Exception as e:  # noqa: BLE001
                _log(f"hermes speed status error: {e}")
                return self._json({"error": str(e)}, 502)
        if path == "/api/dcp":
            try:
                return self._json(dcp_mod.state())
            except Exception as e:  # noqa: BLE001
                _log(f"dcp status error: {e}")
                return self._json({"error": str(e)}, 500)
        if path in ("/api/apply", "/api/suggest", "/api/rollout",
                    "/api/accounts/check", "/api/accounts/switch",
                    "/api/hermes/apply", "/api/hermes/speed/run", "/api/hermes/speed/apply",
                    "/api/failover/settings",
                    "/api/failover/check", "/api/suggest/settings",
                    "/api/benchmarks/refresh", "/api/dcp/settings"):
            return self._json(
                {"error": f"{path} ist POST-only (der Aufruf kam als GET an)"}, 405
            )
        self._static(path)

    def _health(self):
        ok_oc = False
        ok_gh = False
        try:
            catalog.fetch_models()
            ok_oc = True
        except Exception as e:
            _log(f"health: oc={e}")
        try:
            github_api.get_overrides()
            ok_gh = True
        except Exception as e:
            _log(f"health: gh={e}")
        return self._json({
            "status": "ok" if (ok_oc and ok_gh) else "degraded",
            "serverTime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "openCodeReachable": ok_oc,
            "githubReachable": ok_gh,
            "repo": github_api.GITHUB_REPO,
            "overridesPath": github_api.OVERRIDES_PATH,
        })

    def do_POST(self):
        if not self._authorized():
            return self._denied()
        path = urlparse(self.path).path

        if path == "/api/apply":
            try:
                payload = self._body()
                tiers = validate_payload(payload)
                r = github_api.get_overrides()
                cfg = build_config(r["config"], tiers)
                new_jsonc = hjsonc.serialize(cfg)
                models_desc = {t: (tiers[t]["model"] + (f"#{tiers[t]['variant']}" if tiers[t]["variant"] else "")) for t in TIER_DEFS}

                # Primary-Agents: manuelle Auswahl validieren, sonst aus @medium ableiten
                want = payload.get("primaryAgents") or {}
                overrides = {}
                for agent in PRIMARY_AGENTS:
                    ov = want.get(agent) or {}
                    ref = (ov.get("model") or "").strip()
                    if not ref:
                        continue                      # leer = hergeleitet
                    ok, res = catalog.usable(ref)
                    if not ok:
                        raise ApplyError(f"{agent}: {res}")
                    m = res
                    var = (ov.get("variant") or "").strip()
                    if var and var not in (m["variants"] or []):
                        raise ApplyError(f"{agent}: Variante '{var}' nicht verfügbar für {ref}")
                    overrides[agent] = {"model": ref, "variant": var}
                preset = cfg.get("presets", {}).get(PRESET_PROVIDER, {})
                eff = effective_agents(preset, overrides)
                agents_json = json.dumps(agents_to_display(eff), indent=2, ensure_ascii=False) + "\n"

                # Ausfall-Modelle (bezahlte Alternative je Tier, optional)
                fb_in = payload.get("fallbacks") or {}
                fb_out = {}
                for tier in ("fast", "medium"):
                    fb = fb_in.get(tier) or {}
                    if not isinstance(fb, dict):
                        fb = {}
                    if fb.get("disabled") is True:
                        # Bewusst ohne Ausfall-Modell: kein Eintrag schreiben und
                        # auch nicht automatisch ergänzen (Gratis-Tier bleibt ohne Backup).
                        _log(f"Ausfall-Modell {tier}: bewusst deaktiviert")
                        continue
                    ref = (fb.get("model") or "").strip()
                    if not ref and catalog.is_free(tiers[tier]["model"]):
                        # Gratis-Tier ohne gesendetes Ausfall-Modell: das bezahlte
                        # Gegenstück automatisch als Ausfall-Modell hinterlegen.
                        paid = catalog.free_paid_counterpart(tiers[tier]["model"])
                        if paid:
                            ref = paid["ref"]
                            _log(f"Ausfall-Modell {tier}: automatisch {ref} (Gratis-Tier)")
                    if not ref:
                        continue
                    ok, res2 = catalog.usable(ref)
                    if not ok:
                        raise ApplyError(f"Ausfall-Modell {tier}: {res2}")
                    var = (fb.get("variant") or "").strip()
                    if var and var not in (res2["variants"] or []):
                        raise ApplyError(f"Ausfall-Modell {tier}: Variante '{var}' nicht verfügbar")
                    fb_out[tier] = {"model": ref, "variant": var}
                fb_json = json.dumps(fb_out, indent=2, ensure_ascii=False) + "\n"
                fb_current = github_api.get_file(FALLBACKS_PATH) or ""
                fb_desc = {t: (fb_out[t]["model"] + (f"#{fb_out[t]['variant']}" if fb_out[t]["variant"] else "")) for t in fb_out}

                agents_current = github_api.get_file(AGENTS_PATH) or ""
                agents_desc = {a: (eff[a]["model"] + (f"#{eff[a]['variant']}" if eff[a]["variant"] else "")) for a in PRIMARY_AGENTS}

                if (new_jsonc.strip() == (r["content"] or "").strip()
                        and agents_json.strip() == agents_current.strip()
                        and fb_json.strip() == fb_current.strip()):
                    wf = os.environ.get("ROLLOUT_WORKFLOW", "rollout.yml")
                    try:
                        github_api.dispatch_workflow(wf)
                        note = f"unverändert — Rollout ({wf}) direkt gestartet"
                    except ConfigError as e:
                        note = f"unverändert — KEIN Rollout gestartet: {e}"
                    _log(f"apply unchanged: {models_desc} | agents {agents_desc}")
                    return self._json({"applied": True, "unchanged": True, "tiers": models_desc,
                                       "primaryAgents": agents_desc, "rollout": note})

                msg = (
                    "hestia: Tier-Update\n\n"
                    f"fast={models_desc['fast']}\nmedium={models_desc['medium']}\nheavy={models_desc['heavy']}\n"
                    f"build={agents_desc['build']}\nplan={agents_desc['plan']}"
                )
                res = github_api.put_files({
                    github_api.OVERRIDES_PATH: new_jsonc,
                    AGENTS_PATH: agents_json,
                    FALLBACKS_PATH: fb_json,
                }, msg)
                _log(f"apply ok: {models_desc} | agents {agents_desc}")
                return self._json({
                    "applied": True,
                    "unchanged": False,
                    "tiers": models_desc,
                    "primaryAgents": agents_desc,
                    "fallbacks": fb_desc,
                    "commit": res,
                    "rollout": "CI/CD läuft (validate + Container-Restart)",
                })
            except ApplyError as e:
                return self._json({"error": str(e)}, 400)
            except ConfigError as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                _log(f"apply error: {e}")
                return self._json({"error": str(e)}, 500)

        if path == "/api/suggest":
            # synchron: ThreadingHTTPServer bedient jeden Request in eigenem
            # Thread — der Client wartet bis zur fertigen Analyse (1–5 min).
            try:
                payload = self._body()
                override = (payload.get("model") or "").strip()
                variant = (payload.get("variant") or "").strip()
                if override:
                    ok, res = catalog.usable(override)
                    if not ok:
                        return self._json({"error": res}, 400)

                def progress(state: dict):
                    with SUGGEST_LOCK:
                        SUGGEST_STATE.update(state)

                with SUGGEST_LOCK:
                    SUGGEST_STATE.update({
                        "running": True, "startedAt": time.time(), "finishedAt": None,
                        "model": None, "source": None, "attempt": None,
                        "total": None, "phase": "starting", "note": None,
                    })
                try:
                    res = suggest_mod.suggest(override_ref=override, override_variant=variant,
                                              progress=progress)
                finally:
                    with SUGGEST_LOCK:
                        SUGGEST_STATE.update({"running": False, "finishedAt": time.time(),
                                              "phase": SUGGEST_STATE.get("phase") or "done"})
                _log(f"suggest ok (session {res['session']})")
                return self._json(res)
            except RuntimeError as e:
                _log(f"suggest error: {e}")
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                _log(f"suggest error: {e}")
                return self._json({"error": str(e)}, 500)

        if path == "/api/suggest/settings":
            # SOT der Automatik committen + Laufzeit-Zustand aktualisieren
            # (der Prüf-Thread liest Intervall/Cooldown je Iteration neu).
            try:
                return self._json(suggest_mod.update_settings(self._body()))
            except (suggest_mod.SettingsError, ApplyError) as e:
                return self._json({"error": str(e)}, 400)
            except ConfigError as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                _log(f"suggest settings error: {e}")
                return self._json({"error": str(e)}, 500)

        if path == "/api/failover/check":
            try:
                payload = self._body()
                res = failover_mod.run_probes(apply_backup=bool(payload.get("apply")))
                return self._json(res)
            except Exception as e:
                _log(f"failover check error: {e}")
                return self._json({"error": str(e)}, 500)

        if path == "/api/failover/settings":
            # Nur Hestia-intern: SOT committen + STATE zur Laufzeit aktualisieren
            # (der Probe-Thread liest Intervall/Schwelle je Iteration neu).
            try:
                res = failover_mod.update_settings(self._body())
                return self._json(res)
            except (failover_mod.SettingsError, ApplyError) as e:
                return self._json({"error": str(e)}, 400)
            except ConfigError as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                _log(f"failover settings error: {e}")
                return self._json({"error": str(e)}, 500)
        if path == "/api/accounts/check":
            try:
                self._body()
                return self._json(accounts_mod.run_check())
            except Exception as e:
                _log(f"accounts check error: {e}")
                return self._json({"error": str(e)}, 500)
        if path == "/api/accounts/switch":
            try:
                payload = self._body()
                # Ohne explizite id wird die aktuelle Empfehlung übernommen.
                target = str(payload.get("id") or "").strip()
                if not target:
                    target = str((accounts_mod.STATE.get("recommendation") or {}).get("target") or "")
                if not target:
                    return self._json({"error": "kein Zielkonto"}, 400)
                res = accounts_mod.switch_account(
                    target,
                    str(payload.get("reason") or ""),
                    bool(payload.get("force")),
                    by="manual",
                )
                return self._json(res, 200 if res.get("switched") else 409)
            except Exception as e:
                _log(f"accounts switch error: {e}")
                return self._json({"error": str(e)}, 500)
        if path == "/api/hermes/apply":
            try:
                payload = self._body()
                res = hermes_mod.apply(payload)
                # lastApplied vermerken (best-effort) — auch der direkte Apply
                # soll im Speed-State nachvollziehbar bleiben (Muster
                # apply_recommendation). Ein fehlgeschlagener Vermerk darf den
                # bereits committeten Apply nie zum Fehler machen.
                try:
                    prim = payload.get("primary") or {}
                    provider = str(prim.get("provider") or "").strip()
                    model = str(prim.get("model") or "").strip()
                    if provider and model:
                        hermes_speed_mod._mark_applied(f"{provider}/{model}")
                except Exception as e:
                    _log(f"hermes apply lastApplied error: {e}")
                return self._json(res)
            except hermes_mod.ApplyError as e:
                return self._json({"error": str(e)}, 400)
            except ConfigError as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                _log(f"hermes apply error: {e}")
                return self._json({"error": str(e)}, 500)

        if path == "/api/hermes/speed/run":
            # Speed-Lauf im Hintergrund starten (kehrt sofort zurück); der Status
            # kommt aus GET /api/hermes/speed. Kein Auto-Apply — Anwenden nur über
            # /api/hermes/speed/apply.
            try:
                payload = self._body()
                mode = str(payload.get("mode") or "monitoring").strip()
                if mode not in ("discovery", "monitoring", "probe"):
                    return self._json(
                        {"error": "mode muss 'discovery', 'monitoring' oder 'probe' sein"}, 400)
                refs = [str(r).strip() for r in (payload.get("refs") or []) if str(r).strip()]
                if mode == "probe" and not refs:
                    return self._json({"error": "mode=probe braucht refs (Liste von provider/model)"}, 400)
                return self._json(hermes_speed_mod.run_async(mode, refs, payload.get("samples")))
            except Exception as e:
                _log(f"hermes speed run error: {e}")
                return self._json({"error": str(e)}, 500)

        if path == "/api/hermes/speed/apply":
            # Speed-Empfehlung als Hermes-Primary anwenden — ausschließlich über
            # hermes.apply (SOT-Commit + Workflow), nie direkte Config-Schreiberei.
            try:
                payload = self._body()
                ref = str(payload.get("ref") or "").strip()
                res = hermes_speed_mod.apply_recommendation(ref or None)
                return self._json(res)
            except hermes_mod.ApplyError as e:
                return self._json({"error": str(e)}, 400)
            except ConfigError as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                _log(f"hermes speed apply error: {e}")
                return self._json({"error": str(e)}, 500)

        if path == "/api/benchmarks/refresh":
            # Benchmarks sofort neu holen (OpenRouter ist read-only und gecacht).
            try:
                self._body()
                return self._json(benchmarks_mod.refresh(force=True))
            except Exception as e:
                _log(f"benchmarks refresh error: {e}")
                return self._json({"error": str(e)}, 500)

        if path == "/api/dcp/settings":
            try:
                res = dcp_mod.update_settings(self._body())
                return self._json(res)
            except (dcp_mod.SettingsError, ApplyError) as e:
                return self._json({"error": str(e)}, 400)
            except ConfigError as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                _log(f"dcp settings error: {e}")
                return self._json({"error": str(e)}, 500)

        if path == "/api/rollout":
            try:
                payload = self._body()
                wf = payload.get("workflow") or os.environ.get("ROLLOUT_WORKFLOW", "rollout.yml")
                return self._json(github_api.dispatch_workflow(wf))
            except ConfigError as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:
                return self._json({"error": str(e)}, 500)

        return self._json({"error": "unknown endpoint"}, 404)


def main():
    port = int(os.environ.get("HESTIA_PORT", "8000"))
    try:
        failover_mod.start()
    except Exception as e:  # noqa: BLE001
        _log(f"Failover-Start fehlgeschlagen: {e}")
    try:
        accounts_mod.start()
    except Exception as e:  # noqa: BLE001
        _log(f"Accounts-Start fehlgeschlagen: {e}")
    try:
        suggest_mod.start()
    except Exception as e:  # noqa: BLE001
        _log(f"Suggest-Automatik-Start fehlgeschlagen: {e}")
    try:
        hermes_speed_mod.start()
    except Exception as e:  # noqa: BLE001
        _log(f"Hermes-Speed-Start fehlgeschlagen: {e}")
    try:
        benchmarks_mod.start()
    except Exception as e:  # noqa: BLE001
        _log(f"Benchmarks-Start fehlgeschlagen: {e}")
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    _log(f"Hestia backend listening on :{port} (user={HESTIA_USER}; static={STATIC_DIR})")
    if not os.environ.get("HESTIA_PASSWORD"):
        _log(f"WARN: HESTIA_PASSWORD env unset — **generiertes Passwort**: {HESTIA_PASSWORD}")
    server.serve_forever()


if __name__ == "__main__":
    main()
