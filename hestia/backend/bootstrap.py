#!/usr/bin/env python3
"""bootstrap.py — Hestia-Container-Start: OpenBao-Secrets laden, dann server.py.

Wird im Dockerfile als CMD benutzt. OpenBao-AppRole (gleiche Bauweise wie bei
Hermes): OPENBAO_ADDR / OPENBAO_ROLE_ID / OPENBAO_SECRET_ID als Container-Env.

Secrets (AppRole-Policy muss covered paths freigeben):
  secret/data/mcp/github                    → GITHUB_TOKEN (api_key)
  secret/data/mcp/hermes-opencode-service   → OC_SERVER_PASSWORD (password)
  secret/data/mcp/hestia                    → HESTIA_PASSWORD (password) [optional]
  secret/data/mcp/opencode-go               → OC_GO_DEFAULT1..3 + OC_GO_ACTIVE (je ein Feld)
"""
import json
import os
import sys
import urllib.request

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "https://openbao.mueller-nas.de").rstrip("/")
OPENBAO_ROLE_ID = os.environ.get("OPENBAO_ROLE_ID", "")
OPENBAO_SECRET_ID = os.environ.get("OPENBAO_SECRET_ID", "")

SOURCES = [
    ("secret/data/mcp/github", "api_key", "GITHUB_TOKEN"),
    ("secret/data/mcp/hermes-opencode-service", "password", "OC_SERVER_PASSWORD"),
    ("secret/data/mcp/hestia", "password", "HESTIA_PASSWORD"),
]

# OpenCode-Go-Konten: alle Felder liegen in EINEM Secret → ein Request.
GO_ACCOUNTS_PATH = "secret/data/mcp/opencode-go"
GO_FIELDS = [("default1", "OC_GO_DEFAULT1"),
             ("default2", "OC_GO_DEFAULT2"),
             ("default3", "OC_GO_DEFAULT3"),
             ("active", "OC_GO_ACTIVE")]


def log(msg):
    print(f"[hestia-bootstrap] {msg}", flush=True)


def bao_request(path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{OPENBAO_ADDR}/v1/{path}", data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _load_go_accounts(token):
    """OpenCode-Go-Keys + aktives Konto in einem Request laden (Budget-System)."""
    loaded = []
    try:
        data = bao_request(GO_ACCOUNTS_PATH, token=token)["data"]["data"]
    except Exception as e:
        log(f"  ⚠ OpenCode-Go-Konten: {GO_ACCOUNTS_PATH} nicht lesbar ({e})")
        return
    for field, envname in GO_FIELDS:
        value = data.get(field, "")
        if value:
            os.environ[envname] = value
            loaded.append(envname)
            log(f"  ✓ {envname} geladen")
        else:
            log(f"  ⚠ {envname}: Key '{field}' leer in {GO_ACCOUNTS_PATH}")
    if loaded:
        log(f"  ✓ OpenCode-Go: {len(loaded)}/{len(GO_FIELDS)} Felder geladen "
            f"(aktiv: {os.environ.get('OC_GO_ACTIVE', '—')})")


def main():
    if OPENBAO_ROLE_ID and OPENBAO_SECRET_ID:
        try:
            login = bao_request("auth/approle/login",
                                {"role_id": OPENBAO_ROLE_ID, "secret_id": OPENBAO_SECRET_ID})
            token = login["auth"]["client_token"]
            log("OpenBao AppRole-Login ok")
            for path, key, envname in SOURCES:
                try:
                    value = bao_request(path, token=token)["data"]["data"].get(key, "")
                    if value:
                        os.environ[envname] = value
                        log(f"  ✓ {envname} geladen ({path})")
                    else:
                        log(f"  ⚠ {envname}: Key '{key}' leer in {path}")
                except Exception as e:
                    log(f"  ⚠ {envname}: {path} nicht lesbar ({e})")
            _load_go_accounts(token)
            os.environ["BAO_TOKEN"] = token
        except Exception as e:
            log(f"⚠ OpenBao-Login fehlgeschlagen ({e}) — fahre mit Container-Env fort")
    else:
        log("⚠ Kein OPENBAO_ROLE_ID/OPENBAO_SECRET_ID gesetzt — Container-Env wird direkt benutzt")

    # Restliche Env aus optionalen /etc/hestia.env-artigen Files: bereits im Env
    import server
    server.main()


if __name__ == "__main__":
    main()
