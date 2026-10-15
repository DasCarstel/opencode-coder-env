#!/bin/bash
# bootstrap-hosts.sh – Einmaliges Setup für OpenBao-SSH-CA auf den Ziel-Hosts
#
# Dieses Skript muss einmalig auf dem Coder-Host ausgeführt werden.
# Es richtet die OpenBao-Rolle `host-access` ein und verteilt den CA-Key
# auf Docker, Proxmox und TrueNAS.
#
# Voraussetzung: OPENBAO_ROOT_TOKEN muss als Umgebungsvariable gesetzt sein.
#
# Aufruf:
#   export OPENBAO_ROOT_TOKEN=<root-token>
#   bash scripts/bootstrap-hosts.sh

set -euo pipefail

OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
CA_KEY_PATH="/etc/ssh/openbao-ca.pub"

HOSTS=("docker:10.0.10.10" "proxmox:10.0.10.20" "truenas:10.0.10.30")

if [ -z "${OPENBAO_ROOT_TOKEN:-}" ]; then
  echo "ERROR: OPENBAO_ROOT_TOKEN ist nicht gesetzt."
  exit 1
fi

echo "=== OpenBao: Rolle host-access sicherstellen ==="

# Prüfen ob Rolle existiert
ROLE_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/auth/approle/role/host-access/role-id" 2>/dev/null | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('role_id',''))" 2>/dev/null || echo "")

if [ -z "$ROLE_EXISTS" ]; then
  echo "  → Rolle host-access wird erstellt..."
  curl -sS -X POST -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "token_ttl": "3600",
      "token_max_ttl": "86400",
      "token_policies": ["host-access"],
      "token_bound_cidrs": [],
      "token_explicit_max_ttl": "0",
      "token_no_default_policy": false,
      "token_num_uses": "0",
      "token_period": "0",
      "token_type": "default",
      "allowed_users": "root"
    }' \
    "${OPENBAO_ADDR}/v1/auth/approle/role/host-access" >/dev/null 2>&1
  echo "  ✓ Rolle erstellt"
else
  echo "  ✓ Rolle existiert bereits"
fi

# ─── Policy: host-access ─────────────────────────────────────────────────────

echo ""
echo "=== OpenBao: Policy host-access ==="
POLICY_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/sys/policy/acl/host-access" 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('rules',''))" 2>/dev/null || echo "")

if [ -z "$POLICY_EXISTS" ]; then
  echo "  → Policy wird erstellt..."
  curl -sS -X PUT -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "policy": "path \"ssh/sign/host-access\" { capabilities = [\"create\",\"update\"] }"
    }' \
    "${OPENBAO_ADDR}/v1/sys/policy/acl/host-access" >/dev/null 2>&1
  echo "  ✓ Policy erstellt"
else
  echo "  ✓ Policy existiert bereits"
fi

# ─── Policy: mcp-server ──────────────────────────────────────────────────────

echo ""
echo "=== OpenBao: Policy mcp-server ==="
POLICY_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/sys/policy/acl/mcp-server" 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('rules',''))" 2>/dev/null || echo "")

if [ -z "$POLICY_EXISTS" ]; then
  echo "  → Policy wird erstellt..."
  curl -sS -X PUT -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "policy": "path \"secret/data/mcp/*\" { capabilities = [\"read\",\"list\"] }"
    }' \
    "${OPENBAO_ADDR}/v1/sys/policy/acl/mcp-server" >/dev/null 2>&1
  echo "  ✓ Policy erstellt"
else
  echo "  ✓ Policy existiert bereits"
fi

# ─── Policy: mcp-server-general (Least Privilege für den general-Space) ─────

echo ""
echo "=== OpenBao: Policy mcp-server-general ==="
POLICY_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/sys/policy/acl/mcp-server-general" 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('rules',''))" 2>/dev/null || echo "")

if [ -z "$POLICY_EXISTS" ]; then
  echo "  → Policy wird erstellt..."
  curl -sS -X PUT -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "policy": "path \"secret/data/mcp/github\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/opencloud-ocis\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/opencode-go\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/hermes-opencode-service\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/ssh-mcp-pc\" { capabilities = [\"read\"] }\npath \"secret/metadata/mcp/github\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/opencloud-ocis\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/opencode-go\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/hermes-opencode-service\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/ssh-mcp-pc\" { capabilities = [\"list\",\"read\"] }\npath \"auth/token/renew-self\" { capabilities = [\"update\"] }\npath \"auth/token/revoke-self\" { capabilities = [\"update\"] }"
    }' \
    "${OPENBAO_ADDR}/v1/sys/policy/acl/mcp-server-general" >/dev/null 2>&1
  echo "  ✓ Policy erstellt"
else
  echo "  ✓ Policy existiert bereits"
fi

# ─── AppRole: mcp-general (Least-Privilege für den general-Space) ───────────

echo ""
echo "=== OpenBao: AppRole mcp-general ==="
ROLE_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/auth/approle/role/mcp-general/role-id" 2>/dev/null | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('role_id',''))" 2>/dev/null || echo "")

if [ -z "$ROLE_EXISTS" ]; then
  echo "  → Rolle mcp-general wird erstellt..."
  curl -sS -X POST -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "token_policies": ["mcp-server-general"],
      "token_ttl": "3600",
      "token_max_ttl": "86400",
      "secret_id_ttl": "0",
      "secret_id_num_uses": "0",
      "token_num_uses": "0"
    }' \
    "${OPENBAO_ADDR}/v1/auth/approle/role/mcp-general" >/dev/null 2>&1
  echo "  ✓ Rolle erstellt"
else
  echo "  ✓ Rolle existiert bereits"
fi

# ─── Secret: ssh-mcp-pc (exklusiver Key für den Carsten-PC) ─────────────────

echo ""
echo "=== OpenBao: Secret ssh-mcp-pc ==="
PC_PUB_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/secret/data/mcp/ssh-mcp-pc" 2>/dev/null | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('data',{}).get('public_key',''))" 2>/dev/null || echo "")

if [ -z "$PC_PUB_EXISTS" ]; then
  echo "  → ssh-mcp-PC-Key wird erzeugt..."
  TMPKEY_DIR=$(mktemp -d)
  ssh-keygen -t ed25519 -f "$TMPKEY_DIR/id_ed25519_pc" -N "" -C "ssh-mcp-carsten-pc" -q
  PC_PRIV=$(cat "$TMPKEY_DIR/id_ed25519_pc")
  PC_PUB=$(cat "$TMPKEY_DIR/id_ed25519_pc.pub")
  rm -rf "$TMPKEY_DIR"
  python3 - "$OPENBAO_ADDR" "$OPENBAO_ROOT_TOKEN" "$PC_PRIV" "$PC_PUB" <<'PY'
import json, sys, urllib.request
addr, tok, priv, pub = sys.argv[1:5]
body = json.dumps({"data": {"private_key": priv, "public_key": pub}}).encode()
req = urllib.request.Request(addr + "/v1/secret/data/mcp/ssh-mcp-pc", data=body,
                             method="POST",
                             headers={"X-Vault-Token": tok, "Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=15).read()
PY
  echo "  ✓ Secret erstellt (privater + öffentlicher Key)"
else
  echo "  ✓ Secret existiert bereits"
fi

echo "  → Public-Key für den Carsten-PC (in authorized_keys des Users 'carst'):"
curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/secret/data/mcp/ssh-mcp-pc" 2>/dev/null | \
  python3 -c "import sys,json; print('    ' + (json.load(sys.stdin).get('data',{}).get('data',{}).get('public_key','') or 'FEHLT').strip())"

# ─── general-Template: Role-ID ausgeben ─────────────────────────────────────

echo ""
echo "=== general-Template: Role-ID / Secret-ID ==="
GENERAL_ROLE_ID=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/auth/approle/role/mcp-general/role-id" 2>/dev/null | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('role_id',''))" 2>/dev/null || echo "")
echo "  Role-ID  (mcp-general): ${GENERAL_ROLE_ID:-FEHLT}"
echo "  Secret-ID ausstellen:"
echo "    curl -sS -X POST -H \"X-Vault-Token: \$OPENBAO_ROOT_TOKEN\" \\"
echo "      ${OPENBAO_ADDR}/v1/auth/approle/role/mcp-general/secret-id \\"
echo "      | python3 -c \"import sys,json; print(json.load(sys.stdin)['data']['secret_id'])\""
echo "  → im Coder-Template 'general' als openbao_role_id / openbao_approle_secret_id eintragen."

# CA-Key holen
echo ""
echo "=== OpenBao: CA-Key abrufen ==="
CA_KEY=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/ssh/public_key" 2>/dev/null | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('public_key',''))" 2>/dev/null || echo "")

if [ -z "$CA_KEY" ]; then
  echo "  ⚠ CA-Key konnte nicht abgerufen werden"
  exit 1
fi
echo "  ✓ CA-Key erhalten"

# CA-Key auf Hosts verteilen
echo ""
echo "=== CA-Key auf Hosts verteilen ==="
for entry in "${HOSTS[@]}"; do
  HOST_NAME="${entry%%:*}"
  HOST_IP="${entry#*:}"
  echo "  → $HOST_NAME ($HOST_IP)..."
  ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "root@${HOST_IP}" \
    "mkdir -p /etc/ssh && echo '${CA_KEY}' > ${CA_KEY_PATH} && chmod 644 ${CA_KEY_PATH} && systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true" \
    2>&1 | tail -1
  echo "  ✓ $HOST_NAME aktualisiert"
done

echo ""
echo "=== Bootstrap abgeschlossen ==="
echo "  CA-Key Fingerprint:"
echo "$CA_KEY" | ssh-keygen -lf - 2>/dev/null | sed 's/^/    /'
