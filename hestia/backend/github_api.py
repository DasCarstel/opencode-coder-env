#!/usr/bin/env python3
"""github_api.py — Overrides-File lesen/schreiben über die GitHub Contents API.

Hestia läuft als eigener Container auf dem Coder-Host (ohne Repo-Checkout) und
pflegt die Single Source of Truth direkt über GitHub:
  .opencode/opencode-model-router.overrides.jsonc
Der Push feuert automatisch die CI/CD-Workflows (Validate → Coder-Restart).
"""
import base64
import json
import os
import urllib.request
import urllib.error

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "DasCarstel/opencode-coder-env")
OVERRIDES_PATH = os.environ.get("OVERRIDES_PATH", ".opencode/opencode-model-router.overrides.jsonc")
GITHUB_REF = os.environ.get("GITHUB_REF", "master")


def _headers():
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN nicht gesetzt (OpenBao secret/data/mcp/github)")
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "hestia-panel",
    }


def _req(method: str, url: str, body: dict | None = None) -> tuple[int, dict | list]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


class ConfigError(Exception):
    pass


def get_overrides() -> dict:
    """Aktuelles Overrides-File von GitHub holen: {content(str, JSONC), parsed, sha, commitUrl}"""
    status, d = _req(
        "GET", f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{OVERRIDES_PATH}?ref={GITHUB_REF}"
    )
    if status == 404:
        raise ConfigError(
            f"{OVERRIDES_PATH} existiert nicht (Branch {GITHUB_REF}) — Overrides-File im Repo anlegen"
        )
    if status != 200:
        raise ConfigError(f"GitHub GET fehlgeschlagen ({status}): {d.get('message', '')}")
    content = base64.b64decode(d.get("content", "")).decode()
    from hjsonc import parse
    parsed = parse(content)
    return {
        "content": content,
        "config": parsed,
        "sha": d.get("sha"),
        "html_url": d.get("html_url"),
        "branch": GITHUB_REF,
    }


def put_overrides(new_jsonc: str, message: str) -> dict:
    """Overrides-File committen (Commit auf master). Liefert {sha, html_url, commit}."""
    status, current = _req(
        "GET", f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{OVERRIDES_PATH}?ref={GITHUB_REF}"
    )
    sha = current.get("sha") if status == 200 else None
    status, d = _req(
        "PUT",
        f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{OVERRIDES_PATH}",
        {
            "message": message,
            "content": base64.b64encode(new_jsonc.encode()).decode(),
            "branch": GITHUB_REF,
            "sha": sha,
        },
    )
    if status not in (200, 201):
        raise ConfigError(f"GitHub PUT fehlgeschlagen ({status}): {d.get('message', '')}")
    commit = (d.get("commit") or {})
    return {
        "sha": commit.get("sha"),
        "html_url": (commit.get("html_url") or ""),
        "branch": GITHUB_REF,
    }


def dispatch_workflow(workflow: str, ref: str | None = None) -> dict:
    """GitHub Actions Workflow dispatchen (manueller Force-Rollout)."""
    ref = ref or GITHUB_REF
    status, d = _req(
        "POST",
        f"{GITHUB_API}/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches",
        {"ref": ref},
    )
    if status == 204:
        return {"dispatched": True, "workflow": workflow, "ref": ref}
    raise ConfigError(
        f"Workflow-Dispatch fehlgeschlagen ({status}): {d.get('message', '')} — "
        "Hinweis: PAT braucht 'workflow'-Scope; Workflow-Datei muss auf dem Default-Branch liegen."
    )


def latest_run(workflow: str) -> dict:
    """Letzten Lauf eines Workflows auslesen (Status-Anzeige im Panel)."""
    status, d = _req(
        "GET",
        f"{GITHUB_API}/repos/{GITHUB_REPO}/actions/workflows/{workflow}/runs?per_page=1",
    )
    if status != 200:
        raise ConfigError(f"Workflow-Läufe nicht lesbar ({status}): {d.get('message', '')}")
    runs = d.get("workflow_runs") or []
    if not runs:
        return {"none": True, "workflow": workflow}
    r = runs[0]
    return {
        "workflow": workflow,
        "run_number": r.get("run_number"),
        "status": r.get("status"),
        "conclusion": r.get("conclusion"),
        "event": r.get("event"),
        "html_url": r.get("html_url"),
        "created_at": r.get("created_at"),
    }
