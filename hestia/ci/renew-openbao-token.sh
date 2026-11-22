#!/usr/bin/env bash
# renew-openbao-token.sh — periodischen OpenBao-Token des GitHub-Runners erneuern
#
# Zweck: der switch-account-Job liest den Key des aktiven Kontos aus OpenBao
# (secret/data/mcp/opencode-go) mit einem PERIODISCHEN Token aus
# /opt/docker-compose/github-runner/openbao-switch.token (OpenBao-Policy
# "runner": read auf secret/data/mcp/opencode-go + auth/token/renew-self).
# Periodische Token laufen nach der Period (720 h) ab, wenn sie nicht erneuert
# werden — genau das war am 2026-11 der Fall (Stundenlang HTTP 403, dutzende
# CI-Failure-Mails). Dieses Skript erneuert den Token, damit er NIE still
# abläuft: der bestehende Runner-Watchdog auf dem Docker-Host
# (/opt/docker-compose/github-runner/watchdog.sh, systemd-Timer
# runner-watchdog.timer, alle 5 min) ruft es höchstens 1x pro Tag auf —
# gesteuert über den Stamp /var/lib/openbao-token-renew.stamp. Es ist
# jederzeit gefahrlos lauffähig: die Rest-TTL wird auf "increment" verlängert.
#
# Token anlegen (einmalig, mit Root-Token):
#   bao token create -policy=runner -period=720h \
#     -display-name=github-runner-switch -format=json | \
#     jq -r .auth.client_token > /opt/docker-compose/github-runner/openbao-switch.token
#   chmod 600 /opt/docker-compose/github-runner/openbao-switch.token
#
# Sicherheit: der Token selbst wird nie ausgegeben, nie als Kommandozeilen-
# Argument weitergegeben und steht nie im Log.
#
# Env:
#   OPENBAO_ADDR             Default https://openbao.mueller-nas.de
#   OPENBAO_TOKEN_FILE       Default /opt/docker-compose/github-runner/openbao-switch.token
#   OPENBAO_RENEW_INCREMENT  Default 720h
set -euo pipefail

OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
OPENBAO_TOKEN_FILE="${OPENBAO_TOKEN_FILE:-/opt/docker-compose/github-runner/openbao-switch.token}"
OPENBAO_RENEW_INCREMENT="${OPENBAO_RENEW_INCREMENT:-720h}"

if [[ ! -r "$OPENBAO_TOKEN_FILE" ]]; then
  echo "✗ Token-Datei nicht lesbar: $OPENBAO_TOKEN_FILE" >&2
  exit 1
fi
VAULT_TOKEN="$(cat "$OPENBAO_TOKEN_FILE")"

RESP="$(curl -sS -w $'\n__HTTP__%{http_code}' \
  -H "X-Vault-Token: $VAULT_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST -d "{\"increment\":\"$OPENBAO_RENEW_INCREMENT\"}" \
  "$OPENBAO_ADDR/v1/auth/token/renew-self")" || {
  echo "✗ OpenBao nicht erreichbar ($OPENBAO_ADDR)" >&2
  exit 1
}
HTTP_CODE="${RESP##*$'\n'__HTTP__}"
BODY="${RESP%$'\n'__HTTP__*}"

case "$HTTP_CODE" in
  200|201)
    # auth.ttl + auth.policies aus der Antwort lesen (nur diese, nie der Token).
    printf '%s' "$BODY" | python3 -I -c 'import sys, json
try:
    auth = (json.load(sys.stdin) or {}).get("auth") or {}
except ValueError:
    auth = {}
ttl = int(auth.get("lease_duration") or 0)
policies = ",".join(auth.get("policies") or []) or "?"
sys.stdout.write("✓ Token erneuert (TTL %ds, Policies %s)\n" % (ttl, policies))
'
    ;;
  403|404)
    echo "✗ Token ungültig/abgelaufen — neuer periodischer Token mit Policy 'runner' nötig" >&2
    exit 1
    ;;
  *)
    echo "✗ OpenBao-Antwort HTTP ${HTTP_CODE:-?}" >&2
    exit 1
    ;;
esac
