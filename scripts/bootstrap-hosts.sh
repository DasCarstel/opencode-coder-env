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

# Policy sicherstellen
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
