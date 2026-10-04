#!/bin/bash
# startup.sh – Holt Secrets aus OpenBao via AppRole-Auth
#
# Umgebungsvariablen (von Coder Secrets injiziert):
#   OPENBAO_APPROLE_SECRET_ID  – AppRole Secret-ID (MUSS gesetzt sein)
#   OPENBAO_ADDR               – OpenBao Adresse (default: http://10.0.10.10:8200)
#   OPENBAO_ROLE_ID            – AppRole Name     (default: mcp-server)
#
# Exportierte Umgebungsvariablen:
#   HA_LLA_TOKEN       – Home Assistant API-Key
#   AUTHENTIK_API_KEY  – Authentik API-Key
#   GRAFANA_API_KEY    – Grafana API-Key
#   OPENCLOUD_API_KEY  – OpenCloud API-Key
#   OPENCLOUD_USERNAME – OpenCloud Username
#   BAO_TOKEN          – Kurzlebiger OpenBao-Session-Token
#
# Schreibt auch /etc/opencode.env (für coder_app commands)

set -euo pipefail

OPENBAO_ADDR="${OPENBAO_ADDR:-http://10.0.10.10:8200}"
OPENBAO_ROLE_ID="${OPENBAO_ROLE_ID:-mcp-server}"

# /etc/opencode.env vorbereiten
: > /etc/opencode.env
chmod 600 /etc/opencode.env

if [ -z "${OPENBAO_APPROLE_SECRET_ID:-}" ]; then
  echo "ERROR: OPENBAO_APPROLE_SECRET_ID ist nicht gesetzt."
  echo "  Bitte in Coder → Templates → homelab → Secrets eintragen."
  exit 1
fi

echo "=== OpenBao: Authentifizierung ==="

RESPONSE=$(curl -sS --max-time 10 -X POST "${OPENBAO_ADDR}/v1/auth/approle/login" \
  -H "Content-Type: application/json" \
  -d "{\"role_id\":\"${OPENBAO_ROLE_ID}\",\"secret_id\":\"${OPENBAO_APPROLE_SECRET_ID}\"}" 2>&1) || {
  echo "ERROR: OpenBao nicht erreichbar unter ${OPENBAO_ADDR}"
  exit 1
}

BAO_TOKEN=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['auth']['client_token'])
except (KeyError, json.JSONDecodeError):
    print('')
" 2>/dev/null) || true

if [ -z "$BAO_TOKEN" ]; then
  echo "ERROR: OpenBao-Authentifizierung fehlgeschlagen."
  echo "  Prüfe OPENBAO_APPROLE_SECRET_ID und OPENBAO_ROLE_ID."
  exit 1
fi

export BAO_TOKEN
echo "  ✓ OpenBao-Token erhalten (kurzlebig, 1h TTL)"

echo ""
echo "=== OpenBao: Secrets laden ==="

read_secret() {
  local path="$1"
  local key="$2"
  local varname="$3"

  local value
  value=$(curl -sS --max-time 10 \
    -H "X-Vault-Token: ${BAO_TOKEN}" \
    "${OPENBAO_ADDR}/v1/${path}" \
    | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['data']['data'].get('${key}', ''))
except (KeyError, json.JSONDecodeError):
    print('')
" 2>/dev/null) || true

  if [ -z "$value" ]; then
    echo "  ⚠ ${varname}: nicht gefunden unter ${path} → ${key}"
  else
    export "${varname}=${value}"
    echo "${varname}=${value}" >> /etc/opencode.env
    echo "  ✓ ${varname} geladen"
  fi
}

read_secret "secret/data/mcp/homeassistant" "api_key" "HA_LLA_TOKEN"
read_secret "secret/data/mcp/authentik" "api_key" "AUTHENTIK_API_KEY"
read_secret "secret/data/mcp/grafana" "api_key" "GRAFANA_API_KEY"
read_secret "secret/data/mcp/opencloud" "api_key" "OPENCLOUD_API_KEY"
read_secret "secret/data/mcp/opencloud" "username" "OPENCLOUD_USERNAME"

echo ""
echo "=== OpenBao: Fertig ==="
echo "  Secrets verfügbar: $(grep -c '=' /etc/opencode.env 2>/dev/null || echo 0)/5"
echo ""
