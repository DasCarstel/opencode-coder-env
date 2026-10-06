#!/bin/bash
# bootstrap-hosts.sh – Einmaliges Setup für OpenBao: Policies, AppRoles und SSH-CA-Verteilung
#
# Erstellt (idempotent) ALLE Ressourcen, die die Coder-Infrastruktur benötigt:
#   Policy   mcp-server   – Secret-Zugriff (secret/data/mcp/*, secret/data/ssh/unifi)
#                           + SSH-Zertifikat-Signierung (ssh/sign/host-access)
#   Policy   host-access  – SSH-Zertifikat-Signierung (ssh/sign/host-access)
#   AppRole  mcp-server   – Login für Coder-Workspaces (Policy: mcp-server)
#   AppRole  host-access  – Login für Hosts (Policy: host-access)
#   Der SSH-CA-Key wird auf docker, proxmox und truenas verteilt.
#
# Wichtig: Es wird der kanonische Endpoint /v1/sys/policies/acl/<name> verwendet.
#          Der Legacy-Endpoint /v1/sys/policy/<name> unterstützt in OpenBao 2.x
#          kein PUT mehr ("unsupported operation")!
#
# Voraussetzung: OPENBAO_ROOT_TOKEN muss als Umgebungsvariable gesetzt sein.
#
# Aufruf:
#   export OPENBAO_ROOT_TOKEN=<root-token>
#   bash scripts/bootstrap-hosts.sh
#
# Optional: Neues AppRole Secret-ID für mcp-server ausstellen (für Coder-UI):
#   bash scripts/bootstrap-hosts.sh --issue-secret-id

set -euo pipefail

OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
CA_KEY_PATH="/etc/ssh/openbao-ca.pub"

HOSTS=("docker:10.0.10.10" "proxmox:10.0.10.20" "truenas:10.0.10.30")

if [ -z "${OPENBAO_ROOT_TOKEN:-}" ]; then
  echo "ERROR: OPENBAO_ROOT_TOKEN ist nicht gesetzt."
  exit 1
fi

# ── Hilfsfunktionen ─────────────────────────────────────────────────────────

bao_get() {  # path – gibt Body zurück
  curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" "${OPENBAO_ADDR}/v1/$1"
}

bao_write() {  # path json – PUT, gibt Body zurück
  curl -sS -X PUT -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" -d "$2" \
    "${OPENBAO_ADDR}/v1/$1"
}

bao_write_silent() {  # path json – PUT ohne Ausgabe
  bao_write "$1" "$2" >/dev/null
}

# Extrahiert einen Wert aus einer JSON-Antwort (Pfad in Punktnotation).
# Bei Fehlern (z. B. 404 → {"errors":[]}) wird ein leerer String zurückgegeben.
json_get() {  # json "data.policy"
  printf '%s' "$1" | python3 -c "
import sys, json
try:
    v = json.load(sys.stdin)
    for k in '$2'.split('.'):
        v = v[k]
    print(v if v is not None else '')
except Exception:
    print('')
"
}

echo "=== OpenBao Bootstrap (${OPENBAO_ADDR}) ==="

# ── Policy mcp-server ────────────────────────────────────────────────────────

echo ""
echo "=== Policy mcp-server ==="
RESP=$(bao_get "sys/policies/acl/mcp-server")
if [ -z "$(json_get "$RESP" "data.policy")" ]; then
  echo "  → wird erstellt..."
  bao_write_silent "sys/policies/acl/mcp-server" '{
    "policy": "path \"secret/data/mcp/*\" {\n  capabilities = [\"read\", \"list\"]\n}\npath \"secret/data/ssh/unifi\" {\n  capabilities = [\"read\"]\n}\npath \"secret/metadata/mcp/*\" {\n  capabilities = [\"list\", \"read\"]\n}\npath \"secret/metadata/ssh/unifi\" {\n  capabilities = [\"read\"]\n}\npath \"auth/token/renew-self\" {\n  capabilities = [\"update\"]\n}\npath \"auth/token/revoke-self\" {\n  capabilities = [\"update\"]\n}\npath \"ssh/sign/host-access\" {\n  capabilities = [\"update\"]\n}"
  }'
  echo "  ✓ erstellt"
else
  echo "  ✓ existiert bereits"
fi

# ── Policy host-access ───────────────────────────────────────────────────────

echo ""
echo "=== Policy host-access ==="
RESP=$(bao_get "sys/policies/acl/host-access")
if [ -z "$(json_get "$RESP" "data.policy")" ]; then
  echo "  → wird erstellt..."
  bao_write_silent "sys/policies/acl/host-access" '{
    "policy": "path \"ssh/sign/host-access\" {\n  capabilities = [\"update\"]\n}"
  }'
  echo "  ✓ erstellt"
else
  echo "  ✓ existiert bereits"
fi

# ── AppRole mcp-server ───────────────────────────────────────────────────────
# secret_id_ttl=0 (läuft nie ab), secret_id_num_uses=0 (unbegrenzt nutzbar)

echo ""
echo "=== AppRole mcp-server ==="
RESP=$(bao_get "auth/approle/role/mcp-server")
if [ -z "$(json_get "$RESP" "data.bind_secret_id")" ]; then
  echo "  → wird erstellt..."
  bao_write_silent "auth/approle/role/mcp-server" '{
    "secret_id_ttl": 0,
    "secret_id_num_uses": 0,
    "token_ttl": "1h",
    "token_max_ttl": "24h",
    "token_policies": ["mcp-server"],
    "token_bound_cidrs": [],
    "token_explicit_max_ttl": 0,
    "token_no_default_policy": false,
    "token_num_uses": 0,
    "token_type": "default"
  }'
  echo "  ✓ erstellt"
else
  echo "  ✓ existiert bereits"
fi

# ── AppRole host-access ──────────────────────────────────────────────────────

echo ""
echo "=== AppRole host-access ==="
RESP=$(bao_get "auth/approle/role/host-access")
if [ -z "$(json_get "$RESP" "data.bind_secret_id")" ]; then
  echo "  → wird erstellt..."
  bao_write_silent "auth/approle/role/host-access" '{
    "secret_id_ttl": 0,
    "secret_id_num_uses": 0,
    "token_ttl": "1h",
    "token_max_ttl": "24h",
    "token_policies": ["host-access"],
    "token_bound_cidrs": [],
    "token_explicit_max_ttl": 0,
    "token_no_default_policy": false,
    "token_num_uses": 0,
    "token_type": "default"
  }'
  echo "  ✓ erstellt"
else
  echo "  ✓ existiert bereits"
fi

# ── CA-Key abrufen (Rohtext, KEIN JSON!) ─────────────────────────────────────

echo ""
echo "=== OpenBao: CA-Key abrufen ==="
CA_KEY=$(bao_get "ssh/public_key")

if [ -z "$CA_KEY" ]; then
  echo "  ⚠ CA-Key konnte nicht abgerufen werden"
  exit 1
fi
echo "  ✓ CA-Key erhalten"

# ── CA-Key auf Hosts verteilen (einzelne Fehler sind nicht fatal) ────────────

echo ""
echo "=== CA-Key auf Hosts verteilen ==="
for entry in "${HOSTS[@]}"; do
  HOST_NAME="${entry%%:*}"
  HOST_IP="${entry#*:}"
  echo "  → $HOST_NAME ($HOST_IP)..."
  if ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no "root@${HOST_IP}" \
    "mkdir -p /etc/ssh && echo '${CA_KEY}' > ${CA_KEY_PATH} && chmod 644 ${CA_KEY_PATH} && (systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true)"; then
    echo "  ✓ $HOST_NAME aktualisiert"
  else
    echo "  ⚠ $HOST_NAME: Verteilung fehlgeschlagen (CA-Key ggf. manuell prüfen)"
  fi
done

# ── Optional: Secret-ID für mcp-server ausstellen ────────────────────────────

if [ "${1:-}" = "--issue-secret-id" ]; then
  echo ""
  echo "=== Neues AppRole Secret-ID (mcp-server) ==="
  ROLE_ID=$(json_get "$(bao_get "auth/approle/role/mcp-server/role-id")" "data.role_id")
  SECRET_ID=$(json_get "$(bao_write "auth/approle/role/mcp-server/secret-id" "{}")" "data.secret_id")
  echo ""
  echo "  Role-ID:   ${ROLE_ID}"
  echo "  Secret-ID: ${SECRET_ID}"
  echo ""
  echo "  → In der Coder-UI (Workspace-Parameter 'openbao_approle_secret_id')"
  echo "    bzw. im Template main.tf (default) eintragen."
fi

# ── Zusammenfassung ──────────────────────────────────────────────────────────

echo ""
echo "=== Bootstrap abgeschlossen ==="
echo "  Policies:  mcp-server, host-access"
echo "  AppRoles:  mcp-server, host-access"
echo "  CA-Key Fingerprint:"
echo "$CA_KEY" | ssh-keygen -lf - 2>/dev/null | sed 's/^/    /'
