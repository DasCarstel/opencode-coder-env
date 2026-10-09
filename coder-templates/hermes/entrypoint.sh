#!/bin/bash
set -euo pipefail

# Hermes-Workspace Bootstrap
# Liest Secrets aus OpenBao und konfiguriert Hermes MCPs

export OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
export OPENBAO_ROLE_ID="${OPENBAO_ROLE_ID:-}"
export OPENBAO_SECRET_ID="${OPENBAO_SECRET_ID:-}"

if [ -z "$OPENBAO_ROLE_ID" ] || [ -z "$OPENBAO_SECRET_ID" ]; then
    echo "ERROR: OPENBAO_ROLE_ID oder OPENBAO_SECRET_ID nicht gesetzt"
    exit 1
fi

# OpenBao Token holen
BAO_TOKEN=$(curl -s -X POST \
    -d "{\"role_id\":\"$OPENBAO_ROLE_ID\",\"secret_id\":\"$OPENBAO_SECRET_ID\"}" \
    "$OPENBAO_ADDR/v1/auth/approle/login" | jq -r '.auth.client_token')

if [ -z "$BAO_TOKEN" ] || [ "$BAO_TOKEN" = "null" ]; then
    echo "ERROR: OpenBao Login fehlgeschlagen"
    exit 1
fi

# Secrets lesen
HA_TOKEN=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/homeassistant" | jq -r '.data.data.api_key')
OC_GO_ACTIVE=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/opencode-go" | jq -r '.data.data.active')
OC_GO_DEFAULT1=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/opencode-go" | jq -r '.data.data.default1')
OC_GO_DEFAULT2=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/opencode-go" | jq -r '.data.data.default2')
OC_GO_DEFAULT3=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/opencode-go" | jq -r '.data.data.default3')
OC_PRIVATE_USER=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/opencloud-ocis" | jq -r '.data.data.private_user')
OC_PRIVATE_TOKEN=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/opencloud-ocis" | jq -r '.data.data.private_token')
OC_MC_USER=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/opencloud-ocis" | jq -r '.data.data.muellerconnect_user')
OC_MC_TOKEN=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/opencloud-ocis" | jq -r '.data.data.muellerconnect_token')
OC_PASSWORD=$(curl -s -H "X-Vault-Token: $BAO_TOKEN" "$OPENBAO_ADDR/v1/secret/data/mcp/hermes-opencode-service" | jq -r '.data.data.password')

# OpenBao Token nicht weitergeben
unset BAO_TOKEN

# .env für Hermes erstellen (nur freigegebene Werte)
cat > /root/.hermes/.env <<EOF
OPENCODE_GO_API_KEY=${OC_GO_ACTIVE}
OPENCODE_GO_API_KEY_2=${OC_GO_DEFAULT1}
OPENCODE_GO_API_KEY_3=${OC_GO_DEFAULT2}
HA_LLA_TOKEN=${HA_TOKEN}
OPENCODE_SERVER_PASSWORD=${OC_PASSWORD}
OCIS_PRIVATE_USER=${OC_PRIVATE_USER}
OCIS_PRIVATE_TOKEN=${OC_PRIVATE_TOKEN}
OCIS_MUELLERCONNECT_USER=${OC_MC_USER}
OCIS_MUELLERCONNECT_TOKEN=${OC_MC_TOKEN}
EOF

chmod 600 /root/.hermes/.env

# Hermes Config erstellen
mkdir -p /root/.hermes
cat > /root/.hermes/config.yaml <<'EOF'
model:
  provider: opencode-go
  model: opencode-go/mimo-v2.5

credential_pool_strategies:
  opencode-go: fill_first

mcp_servers:
  homeassistant:
    type: http
    url: https://intern-homeassistant.mueller-nas.de/api/mcp
    headers:
      Accept: application/json
      Authorization: Bearer ${HA_LLA_TOKEN}

  opencode:
    type: stdio
    command: ["npx", "-y", "opencode-mcp@3.0.0"]
    env:
      OPENCODE_BASE_URL: http://coder-carstenmueller2002-infrastructure:49374
      OPENCODE_SERVER_USERNAME: opencode
      OPENCODE_SERVER_PASSWORD: ${OPENCODE_SERVER_PASSWORD}
      OPENCODE_TOOL_PROFILE: essential

  ocis-private:
    type: stdio
    command: ["ocis-mcp-server"]
    env:
      OCIS_URL: https://opencloud.mueller-nas.de
      OCIS_USER: ${OCIS_PRIVATE_USER}
      OCIS_TOKEN: ${OCIS_PRIVATE_TOKEN}

  ocis-muellerconnect:
    type: stdio
    command: ["ocis-mcp-server"]
    env:
      OCIS_URL: https://opencloud.mueller-nas.de
      OCIS_USER: ${OCIS_MUELLERCONNECT_USER}
      OCIS_TOKEN: ${OCIS_MUELLERCONNECT_TOKEN}

toolsets:
  enabled:
    - mcp-homeassistant
    - mcp-opencode
    - mcp-ocis-private
    - mcp-ocis-muellerconnect
    - clarify

trust: untrusted
EOF

chmod 600 /root/.hermes/config.yaml

# Hermes CLI starten (interaktiv)
exec hermes
