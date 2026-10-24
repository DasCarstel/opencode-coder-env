#!/usr/bin/env python3
"""Einmalige OAuth-Autorisierung fuer den self-hosted Google-Calendar-MCP.

Wozu
----
Der Kalender laeuft ueber `@cocal/google-calendar-mcp` (stdio im Hermes-Container),
nicht mehr ueber den offiziellen Server von Google: der verlangt eine Enrollment
im Google Workspace Developer Preview Program und ist damit fuer private
@gmail.com-Konten unbrauchbar.

Der Server kann sich im Container nicht selbst autorisieren: sein Auth-Server
lauscht auf 127.0.0.1:3500 und oeffnet einen Browser — beides gibt es hier nicht.
Deshalb derselbe Authorization-Code-Flow in zwei Schritten, wie bei
`spotify_auth.py`:

  1) --print-url    gibt die Authorize-URL aus und merkt sich state (+ optional PKCE)
  2) --callback URL nimmt die komplette Redirect-URL aus der Adresszeile,
                    prueft den state und schreibt die Tokens in das Format des
                    Servers: /etc/docker/hermes/data/gcal/tokens.json
                    (Account-Map, "normal" = Default-Account)

Danach `docker restart hermes`. Der Refresh laeuft danach automatisch ueber den
Server (access_type=offline -> refresh_token, wird bei jedem Refresh
zurueckgeschrieben).

Aufruf (auf dem Coder-Host):
  python3 gcal_auth.py --print-url --state hermes-gcal
  python3 gcal_auth.py --callback 'http://127.0.0.1:8888/callback?code=...&state=...'

Die Redirect-Seite zeigt im Browser einen Fehler ("Seite nicht erreichbar") —
das ist erwartet, der Code steht in der Adresszeile.

Der OAuth-Client wird aus credentials.json gelesen (Web-Client, Redirect
http://127.0.0.1:8888/callback — genau so in der Google-Cloud-Console
eingetragen).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CREDENTIALS = "/etc/docker/hermes/data/gcal/credentials.json"
TOKENS = "/etc/docker/hermes/data/gcal/tokens.json"
ACCOUNT = "normal"

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
)

OWNER_UID = 10000
OWNER_GID = 10000


def state_path() -> str:
    return os.path.join(os.path.dirname(TOKENS), ".gcal-auth-state.json")


def write_private_json(path: str, payload: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    if os.geteuid() == 0:
        try:
            os.chown(tmp, OWNER_UID, OWNER_GID)
        except OSError:
            pass
    os.replace(tmp, path)


def client() -> dict:
    try:
        with open(CREDENTIALS, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise SystemExit(f"✗ Credentials nicht lesbar: {CREDENTIALS} ({exc})")
    block = data.get("web") or data.get("installed") or data
    if not block.get("client_id") or not block.get("client_secret"):
        raise SystemExit("✗ credentials.json ohne client_id/client_secret")
    redirect = (block.get("redirect_uris") or ["http://127.0.0.1:8888/callback"])[0]
    return {
        "client_id": block["client_id"],
        "client_secret": block["client_secret"],
        "redirect_uri": redirect,
    }


def print_url(state: str | None, use_pkce: bool) -> int:
    cfg = client()
    state = state or secrets.token_urlsafe(16)
    params = {
        "client_id": cfg["client_id"],
        "response_type": "code",
        "redirect_uri": cfg["redirect_uri"],
        "scope": " ".join(SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    verifier = ""
    # PKCE ist optional: der Client ist vertraulich (client_secret) und Google
    # verlangt es fuer "Web application"-Clients nicht. Ohne PKCE bleibt die
    # Authorize-URL kurz genug, um sie per Chat/Mail zu uebergeben.
    if use_pkce:
        verifier = secrets.token_urlsafe(64)
        params["code_challenge"] = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        params["code_challenge_method"] = "S256"

    write_private_json(state_path(), {"state": state, "verifier": verifier})
    print("Autorisierungs-URL (im Browser oeffnen, Zugriff bestaetigen):")
    print()
    print(f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}")
    print()
    print("Danach zeigt der Browser einen Fehler, weil 127.0.0.1:8888 nicht")
    print("antwortet. Die Adresszeile enthaelt die Redirect-URL mit ?code=... —")
    print("komplett kopieren und an --callback uebergeben.")
    return 0


def exchange(callback: str) -> int:
    cfg = client()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(callback.strip()).query)

    if "error" in query:
        print(f"✗ Google meldet: {query['error'][0]}", file=sys.stderr)
        return 1
    if "code" not in query:
        print("✗ Kein code= in der URL — komplette Redirect-URL uebergeben", file=sys.stderr)
        return 1

    try:
        with open(state_path(), encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        print("✗ Kein state gespeichert — erst --print-url ausfuehren", file=sys.stderr)
        return 1

    if query.get("state", [""])[0] != saved.get("state"):
        print("✗ state passt nicht zur Authorize-URL (alte URL verwendet?)", file=sys.stderr)
        return 1

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": query["code"][0],
        "redirect_uri": cfg["redirect_uri"],
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        **({"code_verifier": saved["verifier"]} if saved.get("verifier") else {}),
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            tokens = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print(f"✗ Token-Tausch fehlgeschlagen: HTTP {exc.code} {detail}", file=sys.stderr)
        return 1

    if not tokens.get("refresh_token"):
        print("✗ Keine refresh_token in der Antwort — Google hat keine Offline-"
              "Berechtigung erteilt (access_type=offline fehlt)", file=sys.stderr)
        return 1

    expires_in = int(tokens.get("expires_in", 3600))
    try:
        with open(TOKENS, encoding="utf-8") as fh:
            accounts = json.load(fh)
    except (OSError, ValueError):
        accounts = {}

    accounts[ACCOUNT] = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "scope": tokens.get("scope", " ".join(SCOPES)),
        "token_type": tokens.get("token_type", "Bearer"),
        # Der Server vergleicht expiry_date (Millisekunden) gegen Date.now().
        "expiry_date": int((time.time() + expires_in) * 1000),
    }
    write_private_json(TOKENS, accounts)
    try:
        os.unlink(state_path())
    except OSError:
        pass

    print(f"✓ Tokens gespeichert in {TOKENS} (Account '{ACCOUNT}')")
    print(f"  scope: {accounts[ACCOUNT]['scope']}")
    print(f"  access_token: {len(tokens['access_token'])} Zeichen, gueltig {expires_in} s")
    print(f"  refresh_token: {len(tokens['refresh_token'])} Zeichen")
    print()
    print("Naechster Schritt: docker restart hermes")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pkce", action="store_true",
                    help="PKCE (S256) verwenden — macht die URL zu lang fuer Chat-Transfer")
    ap.add_argument("--state", help="Fester state-Wert (Default: zufaellig)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--print-url", action="store_true")
    group.add_argument("--callback", metavar="URL")
    args = ap.parse_args()

    if args.print_url:
        return print_url(args.state, args.pkce)
    return exchange(args.callback)


if __name__ == "__main__":
    raise SystemExit(main())
