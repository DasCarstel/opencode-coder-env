#!/usr/bin/env python3
"""Rendert .env und run.sh fuer den Picnic-MCP-Server aus OpenBao.

Der Server (ivo-toby/mcp-picnic) liest seine Zugangsdaten aus
Umgebungsvariablen. Damit keine Secrets in der von Hestia gepflegten
``config.yaml`` landen, wird derselbe Weg wie bei YAZIO benutzt:

  OpenBao ``secret/data/mcp/picnic``
    -> /etc/docker/hermes/data/picnic/.env      (0600, Umgebungsvariablen)
    -> /etc/docker/hermes/data/picnic/run.sh    (0700, Wrapper)

``config.yaml`` enthaelt danach nur ``command: /opt/data/picnic/run.sh``.

Die Session-Datei (``PICNIC_SESSION_FILE``) und die Device-ID
(``PICNIC_DEVICE_FILE``) liegen im selben Volume-Verzeichnis und werden von
diesem Skript **nicht** angefasst — sonst waere nach jedem Rendern eine neue
2FA-Verifikation faellig.

Aufruf:
  picnic_env.py [--dir PFAD] [--owner UID:GID] [--dry-run]

Token-Quellen, in dieser Reihenfolge:
  $OPENBAO_TOKEN, $OPENBAO_TOKEN_FILE, AppRole
  ($OPENBAO_ROLE_ID + $OPENBAO_APPROLE_SECRET_ID)

Default-Ziel: /etc/docker/hermes/data/picnic
(= im Hermes-Container /opt/data/picnic)

Ausgegeben werden nur Laengen, niemals Secrets.
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
SECRET_PATH = "secret/data/mcp/picnic"
DEFAULT_DIR = "/etc/docker/hermes/data/picnic"
DEFAULT_OWNER = "10000:10000"   # User hermes im Hermes-Container
DEFAULT_COUNTRY = "DE"

ENV_TEMPLATE = """\
# Von hestia/scripts/picnic_env.py erzeugt - nicht von Hand editieren.
PICNIC_USERNAME={username}
PICNIC_PASSWORD={password}
PICNIC_COUNTRY_CODE={country}
PICNIC_SESSION_FILE=/opt/data/picnic/picnic-session.json
PICNIC_DEVICE_FILE=/opt/data/picnic/picnic-device.json
"""

RUN_SH = """\
#!/bin/sh
# Von hestia/scripts/picnic_env.py erzeugt - nicht von Hand editieren.
set -eu
set -a
. /opt/data/picnic/.env
set +a
exec /usr/local/bin/node /usr/local/lib/node_modules/mcp-picnic/bin/mcp-server.js
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


def write_file(path: str, content: str, mode: int, owner: str) -> None:
    """Schreibt atomar (temp + replace) mit Modus und optionalem Owner."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".picnic-")
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
    ap.add_argument("--dir", default=DEFAULT_DIR,
                    help="Zielverzeichnis (Default: %(default)s)")
    ap.add_argument("--owner", default=DEFAULT_OWNER,
                    help="UID:GID fuer die Zieldateien (nur als root)")
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

    username = str(secret.get("username") or "").strip()
    password = str(secret.get("password") or "").strip()
    country = str(secret.get("country_code") or DEFAULT_COUNTRY).strip().upper()

    missing = [name for name, value in
               (("username", username), ("password", password)) if not value]
    if missing:
        print("✗ OpenBao unvollstaendig, fehlt: " + ", ".join(missing),
              file=sys.stderr)
        return 1
    if country not in ("DE", "NL", "FR"):
        print(f"✗ country_code muss DE, NL oder FR sein (ist: {country!r})",
              file=sys.stderr)
        return 1

    env_content = ENV_TEMPLATE.format(
        username=shlex.quote(username),
        password=shlex.quote(password),
        country=shlex.quote(country),
    )
    env_path = os.path.join(args.dir, ".env")
    run_path = os.path.join(args.dir, "run.sh")

    if args.dry_run:
        print(f"→ dry-run: {env_path} (0600) und {run_path} (0700) "
              f"wuerden geschrieben")
        print(f"  username: {len(username)} Zeichen, "
              f"password: {len(password)} Zeichen, country: {country}")
        return 0

    write_file(env_path, env_content, 0o600, args.owner)
    write_file(run_path, RUN_SH, 0o700, args.owner)

    session_path = os.path.join(args.dir, "picnic-session.json")
    device_path = os.path.join(args.dir, "picnic-device.json")
    print(f"✓ {env_path} geschrieben (0600, {args.owner})")
    print(f"  username: {len(username)} Zeichen")
    print(f"  password: {len(password)} Zeichen")
    print(f"  country:  {country}")
    print(f"✓ {run_path} geschrieben (0700, {args.owner})")
    print(f"  Session-Datei: {'vorhanden' if os.path.isfile(session_path) else 'fehlt (2FA noetig)'}")
    print(f"  Device-Datei:  {'vorhanden' if os.path.isfile(device_path) else 'wird beim Start erzeugt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
