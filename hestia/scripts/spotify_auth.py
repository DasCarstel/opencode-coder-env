#!/usr/bin/env python3
"""Einmalige OAuth-Autorisierung fuer den Spotify-MCP-Server.

Der Server kann sich im Container nicht selbst autorisieren: er startet einen
Callback-Server auf 127.0.0.1 und oeffnet einen Browser — beides gibt es im
Hermes-Container nicht. Deshalb derselbe Authorization-Code-Flow in zwei
Schritten von Hand:

  1) --print-url    gibt die Authorize-URL aus und merkt sich den state
  2) --callback URL nimmt die komplette Redirect-URL aus der Adresszeile des
                    Browsers, prueft den state und tauscht den Code gegen
                    Tokens; accessToken/refreshToken/expiresAt landen in der
                    spotify-config.json

Aufruf:
  spotify_auth.py --print-url
  spotify_auth.py --callback 'http://127.0.0.1:8888/callback?code=...&state=...'

Die Redirect-Adresse muss mit der in der Spotify-Developer-App eingetragenen
und der in der Config hinterlegten URI uebereinstimmen. Die Seite selbst zeigt
im Browser einen Fehler ("Seite nicht erreichbar") — das ist erwartet, der
Code steht in der Adresszeile.

Der Client-Zugang (clientId/clientSecret) wird aus der Config gelesen; diese
wird aus OpenBao gerendert (siehe spotify_config.py).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_CONFIG = "/etc/docker/hermes/data/spotify/spotify-config.json"
TOKEN_URL = "https://accounts.spotify.com/api/token"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
# Muss zur SPOTIFY_SCOPES-Liste des Servers passen (src/utils.ts).
SCOPES = (
    "user-read-private",
    "user-read-email",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "user-read-playback-position",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-library-read",
    "user-library-modify",
    "user-read-recently-played",
    "user-top-read",
    "user-follow-read",
)


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_config(path: str, config: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    if os.geteuid() == 0:
        try:
            os.chown(tmp, 10000, 10000)
        except OSError:
            pass
    os.replace(tmp, path)


def state_path(config_path: str) -> str:
    return config_path + ".state"


def print_url(config_path: str) -> int:
    config = load_config(config_path)
    missing = [k for k in ("clientId", "clientSecret", "redirectUri") if not config.get(k)]
    if missing:
        print("✗ Config unvollstaendig: " + ", ".join(missing), file=sys.stderr)
        return 1

    state = secrets.token_urlsafe(16)
    with open(state_path(config_path), "w", encoding="utf-8") as fh:
        json.dump({"state": state}, fh)
    os.chmod(state_path(config_path), 0o600)

    params = urllib.parse.urlencode({
        "client_id": config["clientId"],
        "response_type": "code",
        "redirect_uri": config["redirectUri"],
        "scope": " ".join(SCOPES),
        "state": state,
        "show_dialog": "true",
    })
    print("Autorisierungs-URL (im Browser oeffnen, Zugriff bestaetigen):")
    print()
    print(f"{AUTHORIZE_URL}?{params}")
    print()
    print("Danach zeigt der Browser einen Fehler, weil 127.0.0.1:8888 nicht")
    print("antwortet. Die Adresszeile enthaelt die Redirect-URL mit ?code=... —")
    print("komplett kopieren und an spotify_auth.py --callback uebergeben.")
    return 0


def exchange(config_path: str, callback: str) -> int:
    config = load_config(config_path)
    parsed = urllib.parse.urlsplit(callback.strip())
    query = urllib.parse.parse_qs(parsed.query)

    if "error" in query:
        print(f"✗ Spotify meldet: {query['error'][0]}", file=sys.stderr)
        return 1
    if "code" not in query:
        print("✗ Kein code= in der URL — komplette Redirect-URL uebergeben",
              file=sys.stderr)
        return 1

    try:
        with open(state_path(config_path), encoding="utf-8") as fh:
            expected = json.load(fh).get("state")
    except (OSError, ValueError):
        print("✗ Kein state gespeichert — erst --print-url ausfuehren",
              file=sys.stderr)
        return 1

    if query.get("state", [""])[0] != expected:
        print("✗ state passt nicht zur Authorize-URL (alte URL verwendet?)",
              file=sys.stderr)
        return 1

    basic = base64.b64encode(
        f"{config['clientId']}:{config['clientSecret']}".encode()
    ).decode()
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": query["code"][0],
        "redirect_uri": config["redirectUri"],
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            tokens = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        print(f"✗ Token-Tausch fehlgeschlagen: HTTP {exc.code} {detail}",
              file=sys.stderr)
        return 1

    if not tokens.get("refresh_token"):
        print("✗ Keine refresh_token in der Antwort — App neu autorisieren "
              "(show_dialog=true erzwingt eine neue Zustimmung)", file=sys.stderr)
        return 1

    config["accessToken"] = tokens["access_token"]
    config["refreshToken"] = tokens["refresh_token"]
    config["expiresAt"] = int(__import__("time").time() * 1000) + int(tokens["expires_in"]) * 1000
    save_config(config_path, config)
    try:
        os.unlink(state_path(config_path))
    except OSError:
        pass

    print(f"✓ Tokens gespeichert in {config_path}")
    print(f"  scope: {tokens.get('scope', '?')}")
    print(f"  accessToken: {len(tokens['access_token'])} Zeichen, "
          f"gueltig {tokens.get('expires_in')} s")
    print(f"  refreshToken: {len(tokens['refresh_token'])} Zeichen")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--print-url", action="store_true")
    group.add_argument("--callback", metavar="URL")
    args = ap.parse_args()

    if not os.path.isfile(args.config):
        print(f"✗ Config fehlt: {args.config} — erst spotify_config.py laufen lassen",
              file=sys.stderr)
        return 1

    if args.print_url:
        return print_url(args.config)
    return exchange(args.config, args.callback)


if __name__ == "__main__":
    raise SystemExit(main())
