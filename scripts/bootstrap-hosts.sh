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
#
# Nur eine neue AppRole-Secret-ID ausstellen (kein Bootstrap, kein SSH):
#   bash scripts/bootstrap-hosts.sh --issue-secret-id [ROLLE]   (Default: mcp-uni)
#   Die Secret-ID wird nie ausgegeben, sondern 0600 in
#   $OPENBAO_SECRET_ID_FILE (Default ~/.openbao/<rolle>.secret-id) abgelegt.

set -euo pipefail

OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
CA_KEY_PATH="/etc/ssh/openbao-ca.pub"

HOSTS=("docker:10.0.10.10" "proxmox:10.0.10.20" "truenas:10.0.10.30")

if [ -z "${OPENBAO_ROOT_TOKEN:-}" ]; then
  echo "ERROR: OPENBAO_ROOT_TOKEN ist nicht gesetzt."
  exit 1
fi

# ─── Modus: nur eine Secret-ID ausstellen ────────────────────────────────────
# Stellt eine frische Secret-ID fuer eine AppRole aus (Default mcp-uni). Der
# Wert wird NIE auf stdout ausgegeben; er landet 0600 in $OPENBAO_SECRET_ID_FILE
# (Default: ~/.openbao/<rolle>.secret-id). stdout nennt nur Laenge, Gueltigkeit
# und Ablageort - Details in OPENBAO.md.
issue_secret_id() {
  local role="$1" file out sid ttl uses role_json
  file="${OPENBAO_SECRET_ID_FILE:-$HOME/.openbao/${role}.secret-id}"
  umask 077
  out=$(curl -sS -X POST -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
        "${OPENBAO_ADDR}/v1/auth/approle/role/${role}/secret-id") || {
    echo "ERROR: OpenBao nicht erreichbar (${OPENBAO_ADDR})." >&2
    exit 1
  }
  sid=$(printf '%s' "$out" | python3 -c \
        'import sys,json; print(json.load(sys.stdin).get("data",{}).get("secret_id",""))' \
        2>/dev/null || echo "")
  if [ -z "$sid" ]; then
    echo "ERROR: keine Secret-ID fuer die AppRole '${role}' erhalten (existiert sie?)." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$file")"
  printf '%s\n' "$sid" > "$file"
  chmod 600 "$file"
  role_json=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
              "${OPENBAO_ADDR}/v1/auth/approle/role/${role}" 2>/dev/null || echo "{}")
  ttl=$(printf '%s' "$role_json" | python3 -c \
        'import sys,json; t=json.load(sys.stdin).get("data",{}).get("secret_id_ttl",0)
print("unbegrenzt" if not t else str(t) + "s")' 2>/dev/null || echo "?")
  uses=$(printf '%s' "$role_json" | python3 -c \
         'import sys,json; u=json.load(sys.stdin).get("data",{}).get("secret_id_num_uses",0)
print("unbegrenzt" if not u else u)' 2>/dev/null || echo "?")
  echo "=== OpenBao: Secret-ID fuer AppRole '${role}' ==="
  echo "  ✓ Secret-ID ausgestellt, Laenge ${#sid} Zeichen, gueltig fuer ${ttl} (secret_id_ttl), Nutzungen ${uses} (secret_id_num_uses)"
  echo "  ✓ Ablage: ${file} (0600) - der Wert wird hier bewusst nicht ausgegeben"
  echo "  → als Coder-Parameter 'openbao_approle_secret_id' des datascience-Workspace eintragen"
}

if [ "${1:-}" = "--issue-secret-id" ]; then
  issue_secret_id "${2:-mcp-uni}"
  exit 0
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
  "${OPENBAO_ADDR}/v1/sys/policies/acl/host-access" 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('policy',''))" 2>/dev/null || echo "")

if [ -z "$POLICY_EXISTS" ]; then
  echo "  → Policy wird erstellt..."
  curl -sS -X PUT -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "policy": "path \"ssh/sign/host-access\" { capabilities = [\"create\",\"update\"] }"
    }' \
    "${OPENBAO_ADDR}/v1/sys/policies/acl/host-access" >/dev/null 2>&1
  echo "  ✓ Policy erstellt"
else
  echo "  ✓ Policy existiert bereits"
fi

# ─── Policy: mcp-server ──────────────────────────────────────────────────────

echo ""
echo "=== OpenBao: Policy mcp-server ==="
POLICY_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/sys/policies/acl/mcp-server" 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('policy',''))" 2>/dev/null || echo "")

if [ -z "$POLICY_EXISTS" ]; then
  echo "  → Policy wird erstellt..."
  curl -sS -X PUT -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "policy": "path \"secret/data/mcp/*\" { capabilities = [\"read\",\"list\"] }\npath \"secret/data/uni/*\" { capabilities = [\"read\", \"list\"] }\npath \"secret/metadata/uni/*\" { capabilities = [\"read\", \"list\"] }"
    }' \
    "${OPENBAO_ADDR}/v1/sys/policies/acl/mcp-server" >/dev/null 2>&1
  echo "  ✓ Policy erstellt"
else
  # Additive Erweiterung – die bestehenden Regeln müssen erhalten bleiben.
  python3 - "$OPENBAO_ADDR" "$OPENBAO_ROOT_TOKEN" <<'PY'
import json, sys, urllib.request
addr, tok = sys.argv[1:3]
hdrs = {"X-Vault-Token": tok, "Content-Type": "application/json"}
req = urllib.request.Request(addr + "/v1/sys/policies/acl/mcp-server",
                             headers=hdrs, method="GET")
policy = json.load(urllib.request.urlopen(req, timeout=15)).get("data", {}).get("policy", "")
if "secret/data/uni/" in policy:
    print("  ✓ Policy existiert bereits (inkl. uni-Regeln)")
else:
    zusatz = ('path "secret/data/uni/*" { capabilities = ["read", "list"] }\n'
              'path "secret/metadata/uni/*" { capabilities = ["read", "list"] }')
    body = json.dumps({"policy": policy.rstrip("\n") + "\n" + zusatz + "\n"}).encode()
    req = urllib.request.Request(addr + "/v1/sys/policies/acl/mcp-server",
                                 data=body, method="PUT", headers=hdrs)
    urllib.request.urlopen(req, timeout=15).read()
    print("  ✓ Policy additiv um die uni-Regeln erweitert")
PY
fi

# ─── Policy: mcp-server-general (Least Privilege für den general-Space) ─────

echo ""
echo "=== OpenBao: Policy mcp-server-general ==="
POLICY_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/sys/policies/acl/mcp-server-general" 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('policy',''))" 2>/dev/null || echo "")

if [ -z "$POLICY_EXISTS" ]; then
  echo "  → Policy wird erstellt..."
  curl -sS -X PUT -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "policy": "path \"secret/data/mcp/github\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/opencloud-ocis\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/opencode-go\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/hermes-opencode-service\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/ssh-mcp-pc\" { capabilities = [\"read\"] }\npath \"secret/metadata/mcp/github\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/opencloud-ocis\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/opencode-go\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/hermes-opencode-service\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/ssh-mcp-pc\" { capabilities = [\"list\",\"read\"] }\npath \"auth/token/renew-self\" { capabilities = [\"update\"] }\npath \"auth/token/revoke-self\" { capabilities = [\"update\"] }"
    }' \
    "${OPENBAO_ADDR}/v1/sys/policies/acl/mcp-server-general" >/dev/null 2>&1
  echo "  ✓ Policy erstellt"
else
  echo "  ✓ Policy existiert bereits"
fi

# Nachtrag: Super-Productivity-MCP. Idempotent, damit auch bereits bestehende
# Instanzen den neuen Pfad erhalten (der Block oben legt die Policy nur an).
_cur_policy=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/sys/policies/acl/mcp-server-general" 2>/dev/null | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('policy',''))" 2>/dev/null || echo "")
case "$_cur_policy" in
  *super-productivity*) ;;
  "")
    echo "  ⚠ Policy mcp-server-general nicht lesbar – Nachtrag übersprungen"
    ;;
  *)
    _new_policy="${_cur_policy}
path \"secret/data/mcp/super-productivity\" { capabilities = [\"read\"] }
path \"secret/metadata/mcp/super-productivity\" { capabilities = [\"list\",\"read\"] }"
    python3 -c 'import json,sys; print(json.dumps({"policy": sys.stdin.read()}))' <<< "$_new_policy" | \
      curl -sS -X PUT -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
        -H "Content-Type: application/json" --data-binary @- \
        "${OPENBAO_ADDR}/v1/sys/policies/acl/mcp-server-general" >/dev/null 2>&1
    echo "  ✓ Policy mcp-server-general um super-productivity ergänzt"
    ;;
esac

# ─── Policy: mcp-server-uni (Least Privilege für den uni-Space) ─────────────

echo ""
echo "=== OpenBao: Policy mcp-server-uni ==="
POLICY_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/sys/policies/acl/mcp-server-uni" 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('policy',''))" 2>/dev/null || echo "")

if [ -z "$POLICY_EXISTS" ]; then
  echo "  → Policy wird erstellt..."
  curl -sS -X PUT -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "policy": "path \"secret/data/uni/*\" { capabilities = [\"read\",\"list\"] }\npath \"secret/metadata/uni/*\" { capabilities = [\"list\",\"read\"] }\npath \"secret/data/mcp/github\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/opencloud-ocis\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/opencode-go\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/hermes-opencode-service\" { capabilities = [\"read\"] }\npath \"secret/data/mcp/ssh-mcp-pc\" { capabilities = [\"read\"] }\npath \"secret/metadata/mcp/github\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/opencloud-ocis\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/opencode-go\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/hermes-opencode-service\" { capabilities = [\"list\",\"read\"] }\npath \"secret/metadata/mcp/ssh-mcp-pc\" { capabilities = [\"list\",\"read\"] }\npath \"auth/token/renew-self\" { capabilities = [\"update\"] }\npath \"auth/token/revoke-self\" { capabilities = [\"update\"] }"
    }' \
    "${OPENBAO_ADDR}/v1/sys/policies/acl/mcp-server-uni" >/dev/null 2>&1
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

# ─── AppRole: mcp-uni (Least-Privilege für den uni-Space) ───────────────────

echo ""
echo "=== OpenBao: AppRole mcp-uni ==="
ROLE_EXISTS=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/auth/approle/role/mcp-uni/role-id" 2>/dev/null | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('role_id',''))" 2>/dev/null || echo "")

if [ -z "$ROLE_EXISTS" ]; then
  echo "  → Rolle mcp-uni wird erstellt..."
  curl -sS -X POST -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "token_policies": ["mcp-server-uni"],
      "token_ttl": "3600",
      "token_max_ttl": "86400",
      "secret_id_ttl": "0",
      "secret_id_num_uses": "0",
      "token_num_uses": "0"
    }' \
    "${OPENBAO_ADDR}/v1/auth/approle/role/mcp-uni" >/dev/null 2>&1
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

# ─── uni-Workspace: Role-ID ausgeben ────────────────────────────────────────

echo ""
echo "=== uni-Workspace: Role-ID / Secret-ID ==="
UNI_ROLE_ID=$(curl -sS -H "X-Vault-Token: $OPENBAO_ROOT_TOKEN" \
  "${OPENBAO_ADDR}/v1/auth/approle/role/mcp-uni/role-id" 2>/dev/null | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('role_id',''))" 2>/dev/null || echo "")
echo "  Role-ID  (mcp-uni): ${UNI_ROLE_ID:-FEHLT}"
echo "  Secret-ID ausstellen:"
echo "    curl -sS -X POST -H \"X-Vault-Token: \$OPENBAO_ROOT_TOKEN\" \\"
echo "      ${OPENBAO_ADDR}/v1/auth/approle/role/mcp-uni/secret-id \\"
echo "      | python3 -c \"import sys,json; print(json.load(sys.stdin)['data']['secret_id'])\""
echo "  → für den uni-Workspace als openbao_role_id / openbao_approle_secret_id hinterlegen."

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
