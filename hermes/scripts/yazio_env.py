#!/usr/bin/env python3
"""Rendert die Laufzeit-Konfiguration fuer den YAZIO-MCP-Server aus OpenBao.

Der Server (tomastaker/yazio-mcp, stdio) bekommt seine Zugangsdaten ueber
Umgebungsvariablen. Damit keine Secrets in ``config.yaml`` landen, rendert
dieses Skript zwei Dateien in das Hermes-Volume:

  /opt/data/yazio/.env     Zugangsdaten + Defaults (0600)
  /opt/data/yazio/run.sh   Wrapper: laedt .env und startet den MCP (0700)

``config.yaml`` verweist nur noch auf ``/opt/data/yazio/run.sh``. OpenBao
(``secret/data/mcp/yazio``) ist die Quelle der Wahrheit, beide Dateien sind
abgeleiteter Laufzeit-Zustand.

Wichtig: Werte werden per ``shlex.quote`` geschrieben. Das YAZIO-Passwort kann
``$`` enthalten; in ``yazio.env`` des Exporters steht dafuer ``$$`` (Compose-
Interpolation). Hier wird nichts interpoliert, deshalb bleiben die Zeichen
unveraendert.

Aufruf:
  yazio_env.py [--target-dir PFAD] [--owner UID:GID] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
import urllib.error
import urllib.request

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "https://openbao.mueller-nas.de")
SECRET_PATH = "secret/data/mcp/yazio"
DEFAULT_TARGET_DIR = "/etc/docker/hermes/data/yazio"
DEFAULT_OWNER = "10000:10000"  # User hermes im Hermes-Container

# Feste Defaults; ueberschreibbar per OpenBao-Feld gleichen Namens (klein).
DEFAULTS = {
    "YAZIO_BASE_URL": "https://yzapi.yazio.com/v19",
    "YAZIO_MCP_TIMEZONE": "Europe/Berlin",
    "YAZIO_PRODUCT_COUNTRIES": "DE",
    "YAZIO_PRODUCT_LOCALES": "de_DE",
    "YAZIO_HTTP_TIMEOUT_MS": "15000",
}

RUN_SCRIPT = """#!/bin/sh
# Von hestia/scripts/yazio_env.py erzeugt — nicht von Hand editieren.
set -eu
set -a
. /opt/data/yazio/.env
set +a
exec /opt/node/bin/node /opt/yazio-mcp/dist/index.js
"""


def vault_token() -> str:
    """Holt ein OpenBao-Token aus Env, Token-Datei oder AppRole-Login."""
    token = (os.environ.get("OPENBAO_TOKEN") or "").strip()
    if token:
        return token

    token_file = (os.environ.get("OPENBAO_TOKEN_FILE") or "").strip()
    if token_file and os.path.isfile(token_file):
        with open(token_file, encoding="utf-8") as fh:
            return fh.read().strip()

    role_id = (os.environ.get("OPENBAO_ROLE_ID") or "").strip()
    secret_id = (os.environ.get("OPENBAO_APPROLE_SECRET_ID") or "").strip()
    if role_id and secret_id:
        body = json.dumps({"role_id": role_id, "secret_id": secret_id}).encode()
        req = urllib.request.Request(
            f"{OPENBAO_ADDR}/v1/auth/approle/login",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.load(resp)
        return str(((payload.get("auth") or {}).get("client_token") or "")).strip()

    return ""


def read_secret(token: str) -> dict:
    req = urllib.request.Request(
        f"{OPENBAO_ADDR}/v1/{SECRET_PATH}",
        headers={"X-Vault-Token": token},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.load(resp)
    return (payload.get("data") or {}).get("data") or {}


def write_atomic(path: str, content: str, mode: int, owner: str | None) -> None:
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".yazio-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        if owner and os.geteuid() == 0:
            uid, gid = (int(x) for x in owner.split(":", 1))
            os.chown(tmp, uid, gid)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target-dir", default=DEFAULT_TARGET_DIR)
    ap.add_argument("--owner", default=DEFAULT_OWNER)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = vault_token()
    if not token:
        print("✗ Kein OpenBao-Token (OPENBAO_TOKEN / OPENBAO_TOKEN_FILE / AppRole)",
              file=sys.stderr)
        return 1

    try:
        secret = read_secret(token)
    except urllib.error.HTTPError as exc:
        print(f"✗ OpenBao verweigert {SECRET_PATH}: HTTP {exc.code}", file=sys.stderr)
        return 1

    values = {
        "YAZIO_USERNAME": str(secret.get("username") or "").strip(),
        "YAZIO_PASSWORD": str(secret.get("password") or "").strip(),
        "YAZIO_CLIENT_ID": str(secret.get("client_id") or "").strip(),
        "YAZIO_CLIENT_SECRET": str(secret.get("client_secret") or "").strip(),
    }
    for key, default in DEFAULTS.items():
        override = str(secret.get(key.lower()) or "").strip()
        values[key] = override or default

    missing = [k for k, v in values.items() if not v]
    if missing:
        print("✗ OpenBao unvollstaendig, fehlt: " + ", ".join(missing), file=sys.stderr)
        return 1

    values["YAZIO_TOKEN_CACHE_PATH"] = "/opt/data/yazio/token.json"
    values["YAZIO_MCP_STATE_PATH"] = "/opt/data/yazio/state.json"
    values["YAZIO_SAVED_MEALS_PATH"] = "/opt/data/yazio/saved-meals.json"

    body = "".join(
        f"{key}={shlex.quote(value)}\n" for key, value in values.items()
    )

    env_path = os.path.join(args.target_dir, ".env")
    run_path = os.path.join(args.target_dir, "run.sh")

    if args.dry_run:
        print(f"→ dry-run: wuerde {env_path} (0600) und {run_path} (0700) schreiben, "
              f"{len(values)} Variablen")
        return 0

    os.makedirs(args.target_dir, mode=0o750, exist_ok=True)
    write_atomic(env_path, body, 0o600, args.owner)
    write_atomic(run_path, RUN_SCRIPT, 0o700, args.owner)
    if os.geteuid() == 0:
        uid, gid = (int(x) for x in args.owner.split(":", 1))
        os.chown(args.target_dir, uid, gid)
        os.chmod(args.target_dir, 0o750)

    print(f"✓ {env_path} (0600) und {run_path} (0700) geschrieben")
    for key in values:
        print(f"  {key}: {len(values[key])} Zeichen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
