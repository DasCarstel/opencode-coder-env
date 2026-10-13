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

STATIC_DIR = os.environ.get(
    "HESTIA_STATIC", os.path.join(os.path.dirname(BASE_DIR), "frontend", "dist")
)
HESTIA_USER = os.environ.get("HESTIA_USER", "hestia")
HESTIA_PASSWORD = os.environ.get("HESTIA_PASSWORD") or secrets.token_urlsafe(24)
PRESET_PROVIDER = os.environ.get("HESTIA_PRESET_PROVIDER", "opencode-go")
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
        if not model_ref.startswith(f"{PRESET_PROVIDER}/"):
            raise ApplyError(
                f"Tier '{tier}': Modell muss aus Provider '{PRESET_PROVIDER}' kommen"
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
                provider_filter = PRESET_PROVIDER
                return self._json({"models": models, "presetProvider": provider_filter})
            except Exception as e:
                return self._json({"error": str(e)}, 502)
        if path == "/api/config":
            try:
                r = github_api.get_overrides()
                cfg = r["config"]
                preset = cfg.get("presets", {}).get(PRESET_PROVIDER, {})
                return self._json({
                    "activePreset": cfg.get("activePreset"),
                    "presetProvider": PRESET_PROVIDER,
                    "tiers": {t: preset.get(t, {}) for t in TIER_DEFS},
                    "modes": list(cfg.get("modes") or {}),
                    "sha": r["sha"],
                    "html_url": r["html_url"],
                    "updated": bool(r["content"]),
                })
            except ConfigError as e:
                return self._json({"error": str(e)}, 404)
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        if path == "/api/suggest/status":
            with SUGGEST_LOCK:
                state = dict(SUGGEST_STATE)
            if state.get("running") and state.get("startedAt"):
                state["elapsed"] = round(time.time() - state["startedAt"], 1)
            return self._json(state)
        if path in ("/api/apply", "/api/suggest", "/api/rollout"):
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
                msg = (
                    "hestia: Tier-Update\n\n"
                    f"fast={models_desc['fast']}\nmedium={models_desc['medium']}\nheavy={models_desc['heavy']}"
                )
                res = github_api.put_overrides(new_jsonc, msg)
                _log(f"apply ok: {models_desc}")
                return self._json({
                    "applied": True,
                    "tiers": models_desc,
                    "commit": res,
                    "rollout": "CI/CD läuft (validate + Coder-Restart der Workspaces)",
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
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    _log(f"Hestia backend listening on :{port} (user={HESTIA_USER}; static={STATIC_DIR})")
    if not os.environ.get("HESTIA_PASSWORD"):
        _log(f"WARN: HESTIA_PASSWORD env unset — **generiertes Passwort**: {HESTIA_PASSWORD}")
    server.serve_forever()


if __name__ == "__main__":
    main()
