#!/usr/bin/env python3
"""Rendert spotify-config.json fuer den Spotify-MCP-Server aus OpenBao.

Der Server (marcelmarais/spotify-mcp-server) liest seine Zugangsdaten
ausschliesslich aus einer JSON-Datei — Env-Variablen wertet er nicht aus.
Client-ID und Client-Secret werden deshalb in OpenBao gepflegt
(``secret/data/mcp/spotify``) und von diesem Skript in die Datei gerendert.
Damit ist OpenBao die Quelle der Wahrheit und die Datei nur abgeleiteter
Laufzeit-Zustand.

Vorhandene Tokens (accessToken/refreshToken/expiresAt) bleiben beim Rendern
erhalten — sonst wuerde jeder Lauf die OAuth-Sitzung des Servers zerstoeren.
Der Server schreibt erneuerte Tokens selbst in die Datei zurueck.

Aufruf:
  spotify_config.py [--target PFAD] [--owner UID:GID] [--dry-run]

Token-Quellen, in dieser Reihenfolge:
  $OPENBAO_TOKEN, $OPENBAO_TOKEN_FILE, AppRole
  ($OPENBAO_ROLE_ID + $OPENBAO_APPROLE_SECRET_ID)

Default-Ziel: /etc/docker/hermes/data/spotify/spotify-config.json
(= im Hermes-Container /opt/data/spotify/spotify-config.json)

Ausgegeben werden nur Laengen und Praefixe kurzer Felder, niemals Secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "https://openbao.mueller-nas.de")
SECRET_PATH = "secret/data/mcp/spotify"
DEFAULT_TARGET = "/etc/docker/hermes/data/spotify/spotify-config.json"
DEFAULT_OWNER = "10000:10000"   # User hermes im Hermes-Container
FIELDS = ("clientId", "clientSecret", "redirectUri")


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--owner", default=DEFAULT_OWNER,
                    help="UID:GID fuer die Zieldatei (nur als root)")
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

    mapping = {
        "clientId": str(secret.get("client_id") or "").strip(),
        "clientSecret": str(secret.get("client_secret") or "").strip(),
        "redirectUri": str(secret.get("redirect_uri") or "").strip(),
    }
    missing = [k for k, v in mapping.items() if not v]
    if missing:
        print("✗ OpenBao unvollstaendig, fehlt: " + ", ".join(missing), file=sys.stderr)
        return 1

    config = {}
    if os.path.isfile(args.target):
        try:
            with open(args.target, encoding="utf-8") as fh:
                config = json.load(fh) or {}
        except (OSError, ValueError) as exc:
            print(f"✗ Bestehende Datei nicht lesbar ({exc}) — Abbruch, "
                  "damit keine Tokens verloren gehen", file=sys.stderr)
            return 1

    changed = [k for k in FIELDS if config.get(k) != mapping[k]]
    config.update(mapping)

    if args.dry_run:
        print(f"→ dry-run: {len(changed)} Feld(er) wuerden geaendert: "
              f"{', '.join(changed) or 'keine'}")
        return 0

    os.makedirs(os.path.dirname(args.target), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(args.target), prefix=".spotify-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        if args.owner and os.geteuid() == 0:
            uid, gid = (int(x) for x in args.owner.split(":", 1))
            os.chown(tmp, uid, gid)
        os.replace(tmp, args.target)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    has_tokens = bool(config.get("refreshToken"))
    print(f"✓ {args.target} geschrieben (0600, {args.owner})")
    for key in FIELDS:
        print(f"  {key}: {len(config[key])} Zeichen")
    print(f"  OAuth-Tokens vorhanden: {'ja' if has_tokens else 'nein (spotify_auth.py noetig)'}")
    if changed:
        print(f"  aus OpenBao aktualisiert: {', '.join(changed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
