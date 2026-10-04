#!/bin/bash
# Coder Workspace Startup Script
# Holt Secrets aus OpenBao zur Laufzeit (nicht persistent gespeichert)

set -euo pipefail

OPENBAO_ADDR="http://10.0.10.10:8200"
OPENBAO_ROLE_ID="mcp-server"
OPENBAO_SECRET_ID="${OPENBAO_APPROLE_SECRET_ID:?Error: OPENBAO_APPROLE_SECRET_ID nicht gesetzt}"

echo "=== OpenBao Authentifizierung ==="

# AppRole-Authentifizierung
RESPONSE=$(curl -sS --max-time 10 -X POST "$OPENBAO_ADDR/v1/auth/approle/login" \
  -H "Content-Type: application/json" \
  -d "{\"role_id\":\"$OPENBAO_ROLE_ID\",\"secret_id\":\"$OPENBAO_SECRET_ID\"}" 2>&1)

BAO_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['auth']['client_token'])" 2>/dev/null || echo "")

if [ -z "$BAO_TOKEN" ]; then
  echo "FEHLER: OpenBao-Authentifizierung fehlgeschlagen"
  exit 1
fi

echo "✓ OpenBao-Token erhalten (kurzlebig)"

# HA API Key aus OpenBao lesen
echo "=== Home Assistant API Key ==="
HA_TOKEN=$(curl -sS --max-time 10 -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/homeassistant" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['data']['api_key'])" 2>/dev/null || echo "")

if [ -z "$HA_TOKEN" ]; then
  echo "FEHLER: HA API Key konnte nicht gelesen werden"
  exit 1
fi

# Token als Umgebungsvariable für OpenCode setzen (nur im RAM)
export HA_LLA_TOKEN="$HA_TOKEN"
echo "✓ HA_LLA_TOKEN gesetzt (nur für diese Session)"

# OpenCode starten
echo "=== OpenCode ==="
cd ~/opencode-coder-env/workspaces/homelab/homeassistant
exec opencode "$@"
