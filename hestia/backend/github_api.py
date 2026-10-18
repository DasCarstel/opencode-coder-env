#!/usr/bin/env python3
"""github_api.py — Overrides-File lesen/schreiben über die GitHub Contents API.

Hestia läuft als eigener Container auf dem Coder-Host (ohne Repo-Checkout) und
pflegt die Single Source of Truth direkt über GitHub:
  .opencode/opencode-model-router.overrides.jsonc
Der Push feuert automatisch die CI/CD-Workflows (Validate → Coder-Restart).
"""
import base64
import hashlib
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


def dispatch_workflow(workflow: str, ref: str | None = None, inputs: dict | None = None) -> dict:
    """GitHub Actions Workflow dispatchen (manueller Force-Rollout).

    inputs: Optionale workflow_dispatch-Inputs (nur senden, wenn gesetzt —
    GitHub lehnt einen leeren/leer-typisierten inputs-Block nicht ab, aber
    üblich ist es, ihn dann komplett weizulassen).
    """
    ref = ref or GITHUB_REF
    body: dict = {"ref": ref}
    if inputs:
        body["inputs"] = inputs
    status, d = _req(
        "POST",
        f"{GITHUB_API}/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches",
        body,
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


def get_file(path: str) -> str | None:
    """Inhalt einer (optionalen) Datei von GitHub holen; None wenn nicht vorhanden."""
    status, d = _req("GET", f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}?ref={GITHUB_REF}")
    if status != 200:
        return None
    return base64.b64decode(d.get("content", "")).decode()


def _blob_sha(content: str) -> str:
    """Git-Blob-Hash eines Textinhalts (identisch zu dem, was GitHub vergibt)."""
    data = content.encode("utf-8")
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def put_files(files: dict, message: str) -> dict:
    """Mehrere Dateien in EINEM Commit schreiben (Git-Data-API) — ein Push = ein Rollout.

    Enthält der Commit inhaltlich nichts Neues (alle Blobs identisch zum
    aktuellen Tree), wird KEIN Commit erzeugt: Rückgabe {"unchanged": True}.
    Ohne diese Prüfung erzeugte jeder Aufruf einen Push — und damit einen
    Rollout mit Container-Neustarts, auch wenn sich nichts geändert hat
    (genau das war der Dauer-Restart des Ausfallschutzes).
    """
    st, refd = _req("GET", f"{GITHUB_API}/repos/{GITHUB_REPO}/git/ref/heads/{GITHUB_REF}")
    if st != 200:
        raise ConfigError(f"Ref nicht lesbar ({st}): {refd.get('message','')}")
    base_commit = refd["object"]["sha"]
    st, cm = _req("GET", f"{GITHUB_API}/repos/{GITHUB_REPO}/git/commits/{base_commit}")
    if st != 200:
        raise ConfigError(f"Commit nicht lesbar ({st})")
    base_tree = cm["tree"]["sha"]

    st, tb = _req("GET", f"{GITHUB_API}/repos/{GITHUB_REPO}/git/trees/{base_tree}?recursive=1")
    existing = {e.get("path"): e.get("sha") for e in (tb.get("tree") or [])} if st == 200 else {}
    if existing and all(existing.get(p) == _blob_sha(c) for p, c in files.items()):
        return {"unchanged": True, "sha": base_commit, "html_url": "", "branch": GITHUB_REF}

    entries = []
    for path, content in files.items():
        st, b = _req("POST", f"{GITHUB_API}/repos/{GITHUB_REPO}/git/blobs",
                     {"content": content, "encoding": "utf-8"})
        if st not in (200, 201):
            raise ConfigError(f"Blob {path} fehlgeschlagen ({st})")
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": b["sha"]})

    st, tr = _req("POST", f"{GITHUB_API}/repos/{GITHUB_REPO}/git/trees",
                  {"base_tree": base_tree, "tree": entries})
    if st not in (200, 201):
        raise ConfigError(f"Tree fehlgeschlagen ({st})")
    st, nc = _req("POST", f"{GITHUB_API}/repos/{GITHUB_REPO}/git/commits",
                  {"message": message, "tree": tr["sha"], "parents": [base_commit]})
    if st not in (200, 201):
        raise ConfigError(f"Commit fehlgeschlagen ({st})")
    st, up = _req("PATCH", f"{GITHUB_API}/repos/{GITHUB_REPO}/git/refs/heads/{GITHUB_REF}",
                  {"sha": nc["sha"]})
    if st not in (200, 201):
        raise ConfigError(f"Ref-Update fehlgeschlagen ({st})")
    return {"sha": nc["sha"], "html_url": nc.get("html_url", ""), "branch": GITHUB_REF}
