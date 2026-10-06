#!/bin/bash
# startup.sh – Infrastruktur-Setup fuer den Coder-Workspace
#
# 1. Secrets aus OpenBao (AppRole) laden
# 2. SSH-Zugang via OpenBao-SSH-CA (kurzlebige Zertifikate)
# 3. Herdr installieren
# 4. ssh-mcp konfigurieren
# 5. OpenCode V2 + globale MCP-Konfiguration (Workspace-Configs liegen im Repo)
# 6. Herdr-Server + zwei Workspaces (Home Assistant, Server Management)

set -uo pipefail

# ── PATH sicherstellen ─────────────────────────────────────────────────
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:$PATH"
# PATH auch in .bashrc setzen (für neue Shells)
grep -q '/root/.local/bin' /root/.bashrc 2>/dev/null || echo 'export PATH="/root/.local/bin:$PATH"' >> /root/.bashrc

# OpenBao-Adresse fest verdrahtet (die Container-ENV kann einen veralteten Wert enthalten)
OPENBAO_ADDR="https://openbao.mueller-nas.de"
OPENBAO_ROLE_ID="${OPENBAO_ROLE_ID:-mcp-server}"
HERDR_BIN="/usr/local/bin/herdr"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── /etc/opencode.env vorbereiten ───────────────────────────────────────────
: > /etc/opencode.env
chmod 600 /etc/opencode.env

if [ -z "${OPENBAO_APPROLE_SECRET_ID:-}" ]; then
  echo "ERROR: OPENBAO_APPROLE_SECRET_ID ist nicht gesetzt."
  exit 1
fi

# ── OpenBao: AppRole-Login ──────────────────────────────────────────────────
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
    print(json.load(sys.stdin)['auth']['client_token'])
except Exception:
    print('')
")
if [ -z "$BAO_TOKEN" ]; then
  echo "ERROR: OpenBao-Authentifizierung fehlgeschlagen."
  exit 1
fi
export BAO_TOKEN
printf 'BAO_TOKEN="%s"\n' "$BAO_TOKEN" >> /etc/opencode.env
echo "  ✓ OpenBao-Token erhalten"

# ── OpenBao: Secrets laden ──────────────────────────────────────────────────
echo ""
echo "=== OpenBao: Secrets laden ==="
read_secret() {
  local path="$1" key="$2" varname="$3"
  local value
  value=$(curl -sS --max-time 10 -H "X-Vault-Token: ${BAO_TOKEN}" "${OPENBAO_ADDR}/v1/${path}" \
    | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['data']['data'].get('${key}', ''))
except Exception:
    print('')
")
  if [ -z "$value" ]; then
    echo "  ⚠ ${varname}: nicht gefunden"
  else
    export "${varname}=${value}"
    printf '%s="%s"\n' "${varname}" "${value}" >> /etc/opencode.env
    echo "  ✓ ${varname} geladen"
  fi
}
read_secret "secret/data/mcp/homeassistant" "api_key" "HA_LLA_TOKEN"
read_secret "secret/data/mcp/authentik"     "api_key" "AUTHENTIK_API_KEY"
read_secret "secret/data/mcp/grafana"       "api_key" "GRAFANA_API_KEY"
read_secret "secret/data/mcp/opencloud"     "api_key" "OPENCLOUD_API_KEY"
read_secret "secret/data/mcp/opencloud"     "username" "OPENCLOUD_USERNAME"
# OpenCode Go: mehrere Accounts
read_secret "secret/data/mcp/opencode-go"   "default1" "OC_GO_DEFAULT1"
read_secret "secret/data/mcp/opencode-go"   "default2" "OC_GO_DEFAULT2"
read_secret "secret/data/mcp/opencode-go"   "default3" "OC_GO_DEFAULT3"
read_secret "secret/data/mcp/opencode-go"   "active"   "OC_GO_ACTIVE"

# ── SSH via OpenBao-CA (kurzlebige Zertifikate) ─────────────────────────────
echo ""
echo "=== SSH-Zugang (OpenBao-CA) ==="
mkdir -p /root/.ssh && chmod 700 /root/.ssh
KEY=/root/.ssh/id_ed25519_coder
rm -f "$KEY" "$KEY.pub" "$KEY-cert.pub"
ssh-keygen -t ed25519 -f "$KEY" -N "" -C "coder-workspace" -q

SIGNED=$(curl -sS --max-time 10 -X POST "${OPENBAO_ADDR}/v1/ssh/sign/host-access" \
  -H "X-Vault-Token: ${BAO_TOKEN}" -H "Content-Type: application/json" \
  -d "{\"public_key\":\"$(cat ${KEY}.pub)\",\"valid_principals\":\"root\",\"ttl\":\"24h\"}" \
  | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['data']['signed_key'])
except Exception:
    print('')
")
if [ -n "$SIGNED" ]; then
  printf '%s\n' "$SIGNED" > "${KEY}-cert.pub"
  chmod 600 "$KEY" "${KEY}-cert.pub"
  echo "  ✓ SSH-Zertifikat ausgestellt"
else
  echo "  ⚠ SSH-Zertifikat konnte nicht ausgestellt werden"
fi

cat > /root/.ssh/config << 'SSHCFG'
Host docker
    HostName 10.0.10.10
    User root
    IdentityFile /root/.ssh/id_ed25519_coder
    CertificateFile /root/.ssh/id_ed25519_coder-cert.pub
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null

Host proxmox
    HostName 10.0.10.20
    User root
    IdentityFile /root/.ssh/id_ed25519_coder
    CertificateFile /root/.ssh/id_ed25519_coder-cert.pub
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null

Host truenas
    HostName 10.0.10.30
    User root
    IdentityFile /root/.ssh/id_ed25519_coder
    CertificateFile /root/.ssh/id_ed25519_coder-cert.pub
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
SSHCFG
chmod 600 /root/.ssh/config
echo "  ✓ SSH-Config erstellt"

# ── ssh-mcp: stabiler Key aus OpenBao (in authorized_keys der Zielhosts) ────
echo ""
echo "=== ssh-mcp Key (OpenBao) ==="
SSH_MCP_KEY=/root/.ssh/id_ed25519_mcp
MCP_KEY=$(curl -sS --max-time 10 -H "X-Vault-Token: ${BAO_TOKEN}" \
  "${OPENBAO_ADDR}/v1/secret/data/mcp/ssh-mcp" \
  | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['data']['data'].get('private_key', ''))
except Exception:
    print('')
")
if [ -n "$MCP_KEY" ]; then
  printf '%s\n' "$MCP_KEY" > "$SSH_MCP_KEY"
  chmod 600 "$SSH_MCP_KEY"
  echo "  ✓ ssh-mcp Key installiert ($SSH_MCP_KEY)"
else
  echo "  ⚠ ssh-mcp Key nicht gefunden (secret/data/mcp/ssh-mcp)"
fi

# ── Herdr installieren (robuste Version) ─────────────────────────────────────
echo ""
echo "=== Herdr ==="
mkdir -p /usr/local/bin
HERDR_VERSION="0.9.3"
HERDR_URLS=(
  "https://github.com/herdr-sh/herdr/releases/download/v${HERDR_VERSION}/herdr-linux-amd64"
  "https://github.com/herdr-sh/herdr/releases/latest/download/herdr-linux-amd64"
  "https://herdr.dev/api/releases/v${HERDR_VERSION}/linux-amd64"
)

if [ -x "$HERDR_BIN" ]; then
  echo "  ✓ Herdr vorhanden ($($HERDR_BIN --version 2>/dev/null))"
elif [ -f /opt/opencode-bin/herdr ]; then
  cp /opt/opencode-bin/herdr "$HERDR_BIN"
  chmod +x "$HERDR_BIN"
  echo "  ✓ Herdr aus /opt/opencode-bin installiert ($($HERDR_BIN --version 2>/dev/null))"
elif [ -f /usr/local/bin/herdr ]; then
  cp /usr/local/bin/herdr "$HERDR_BIN"
  chmod +x "$HERDR_BIN"
  echo "  ✓ Herdr aus /usr/local/bin installiert ($($HERDR_BIN --version 2>/dev/null))"
else
  echo "  → Herdr wird heruntergeladen..."
  INSTALLED=false
  for url in "${HERDR_URLS[@]}"; do
    echo "    Versuche: $url"
    if curl -fsSL --max-time 30 "$url" -o "$HERDR_BIN" 2>/dev/null; then
      chmod +x "$HERDR_BIN"
      if "$HERDR_BIN" --version >/dev/null 2>&1; then
        echo "  ✓ Herdr installiert ($($HERDR_BIN --version 2>/dev/null))"
        INSTALLED=true
        break
      else
        rm -f "$HERDR_BIN"
      fi
    fi
  done
  if [ "$INSTALLED" = false ]; then
    echo "  ⚠ Herdr-Download fehlgeschlagen – Herdr nicht verfügbar"
    echo "  Hinweis: Überspringe Herdr-Server-Start"
  fi
fi

# ── ssh-mcp Konfiguration ───────────────────────────────────────────────────
echo ""
echo "=== ssh-mcp Config ==="
mkdir -p /root/.config/ssh-mcp
cat > /root/.config/ssh-mcp/config.toml << 'MCPCFG'
[defaults]
defaultProfile = "docker-host"

[[profiles]]
name = "docker-host"
host = "10.0.10.10"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
approvalPolicy = "ask-destructive"

[[profiles]]
name = "proxmox"
host = "10.0.10.20"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "viewer"
approvalPolicy = "ask-all"

[[profiles]]
name = "truenas"
host = "10.0.10.30"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "viewer"
approvalPolicy = "ask-all"
MCPCFG
chmod 700 /root/.config/ssh-mcp
chmod 600 /root/.config/ssh-mcp/config.toml
echo "  ✓ ssh-mcp Config erstellt"

# ── OpenCode V2 ─────────────────────────────────────────────────────────────
echo ""
echo "=== OpenCode V2 ==="
# npm im PATH sicherstellen
if command -v npm >/dev/null 2>&1; then
  if ! opencode --version 2>/dev/null | grep -q "v2"; then
    echo "  → OpenCode wird installiert..."
    npm uninstall -g opencode 2>/dev/null || true
    npm uninstall -g @opencode-ai/opencode 2>/dev/null || true
    rm -f /usr/bin/opencode
    npm install -g @opencode/cli 2>&1 | tail -5
  fi
  echo "  ✓ OpenCode: $(opencode --version 2>&1)"
else
  echo "  ⚠ npm nicht verfügbar – OpenCode kann nicht installiert werden"
fi

# ── MCP-Konfiguration ───────────────────────────────────────────────────────
echo ""
echo "=== MCP-Konfiguration ==="
mkdir -p /root/.config/opencode

# Globale Config (nur ssh-mcp) aus dem Repo installieren
GLOBAL_CFG_SRC="$REPO_DIR/coder-templates/infrastructure/config/global-opencode.json"
if [ -f "$GLOBAL_CFG_SRC" ]; then
  install -m 600 "$GLOBAL_CFG_SRC" /root/.config/opencode/opencode.json
  echo "  ✓ Globale Config installiert (nur SSH)"
else
  echo "  ⚠ Globale Config nicht gefunden: $GLOBAL_CFG_SRC"
fi

# Secrets in die OpenCode-Service-Umgebung injizieren.
# Die workspace-spezifischen Configs (im Repo) referenzieren {env:...}.
if [ -n "${HA_LLA_TOKEN:-}" ]; then
  opencode service set env HA_LLA_TOKEN "$HA_LLA_TOKEN" >/dev/null 2>&1 \
    && echo "  ✓ HA_LLA_TOKEN im OpenCode-Service gesetzt" \
    || echo "  ⚠ HA_LLA_TOKEN konnte nicht gesetzt werden"
fi
if [ -n "${AUTHENTIK_API_KEY:-}" ]; then
  opencode service set env AUTHENTIK_API_KEY "$AUTHENTIK_API_KEY" >/dev/null 2>&1 \
    && echo "  ✓ AUTHENTIK_API_KEY im OpenCode-Service gesetzt" \
    || echo "  ⚠ AUTHENTIK_API_KEY konnte nicht gesetzt werden"
fi

# ── OpenCode Go (mehrere Keys aus OpenBao) ──────────────────────────────────
OC_GO_ACTIVE="${OC_GO_ACTIVE:-default2}"
ACTIVE_VAR="OC_GO_${OC_GO_ACTIVE^^}"
OPENCODE_API_KEY="${!ACTIVE_VAR:-}"

if [ -n "$OPENCODE_API_KEY" ]; then
  export OPENCODE_API_KEY
  sed -i '/^export OPENCODE_API_KEY=/d' /root/.bashrc 2>/dev/null || true
  printf 'export OPENCODE_API_KEY="%s"\n' "$OPENCODE_API_KEY" >> /root/.bashrc
  opencode service set env OPENCODE_API_KEY "$OPENCODE_API_KEY" >/dev/null 2>&1 || true
  echo "  ✓ OpenCode Go aktiv: $OC_GO_ACTIVE"
else
  echo "  ⚠ Kein OpenCode Go Key gefunden"
fi

# Helper zum Umschalten zwischen den OpenCode-Go-Keys
cat > /usr/local/bin/oc-go << 'OCGO'
#!/bin/bash
# OpenCode Go Key verwalten:
#   oc-go list              → verfügbare Keys + aktiver Key
#   oc-go use <name>        → Key wechseln (Session, Service-Restart)
#   oc-go default <name>    → Standard-Key dauerhaft setzen (OpenBao + .bashrc)
source /etc/opencode.env 2>/dev/null
case "${1:-list}" in
  list)
    echo "Verfügbare OpenCode-Go-Keys:"
    for n in default1 default2 default3; do
      v="OC_GO_${n^^}"; [ -n "${!v:-}" ] && echo "  - $n"
    done
    echo "Aktiv: ${OC_GO_ACTIVE:-default2}"
    ;;
  use)
    NAME="${2:-}"
    [ -z "$NAME" ] && { echo "Nutzung: oc-go use <default1|default2|default3>"; exit 1; }
    VAR="OC_GO_${NAME^^}"; KEY="${!VAR:-}"
    [ -z "$KEY" ] && { echo "Unbekannter Key: $NAME"; exit 1; }
    sed -i '/^export OPENCODE_API_KEY=/d' /root/.bashrc 2>/dev/null || true
    printf 'export OPENCODE_API_KEY="%s"\n' "$KEY" >> /root/.bashrc
    export OPENCODE_API_KEY="$KEY"
    opencode service set env OPENCODE_API_KEY "$KEY" >/dev/null 2>&1 || true
    echo "✓ Aktiv: $NAME (OpenCode-Service neu gestartet)"
    ;;
  default)
    NAME="${2:-}"
    [ -z "$NAME" ] && { echo "Nutzung: oc-go default <default1|default2|default3>"; exit 1; }
    VAR="OC_GO_${NAME^^}"; KEY="${!VAR:-}"
    [ -z "$KEY" ] && { echo "Unbekannter Key: $NAME"; exit 1; }
    # In OpenBao schreiben (damit startup.sh den neuen Standard liest)
    if [ -n "${BAO_TOKEN:-}" ]; then
      curl -sS --max-time 10 -X POST \
        -H "X-Vault-Token: ${BAO_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"data\":{\"active\":\"${NAME}\"}}" \
        "https://openbao.mueller-nas.de/v1/secret/data/mcp/opencode-go" >/dev/null 2>&1 \
        && echo "✓ Standard-Key in OpenBao gesetzt: $NAME" \
        || echo "⚠ OpenBao-Update fehlgeschlagen (Token abgelaufen?)"
    else
      echo "⚠ Kein OpenBao-Token verfügbar – nur .bashrc aktualisiert"
    fi
    # .bashrc aktualisieren
    sed -i '/^export OC_GO_ACTIVE=/d' /root/.bashrc 2>/dev/null || true
    printf 'export OC_GO_ACTIVE="%s"\n' "$NAME" >> /root/.bashrc
    echo "✓ Standard-Key: $NAME"
    ;;
  *) echo "Nutzung: oc-go [list | use <name> | default <name>]";;
esac
OCGO
chmod +x /usr/local/bin/oc-go
echo "  ✓ Helper 'oc-go' installiert"

# ── Herdr-Server + Workspaces ───────────────────────────────────────────────
echo ""
echo "=== Herdr-Server & Workspaces ==="
export PATH="/root/.local/bin:$PATH"

if [ -x "$HERDR_BIN" ]; then
  echo "  → Herdr-Server wird gestartet..."
  if ! "$HERDR_BIN" status 2>/dev/null | grep -q "status: running"; then
    nohup "$HERDR_BIN" server > /tmp/herdr-server.log 2>&1 &
    sleep 3
  fi
  if "$HERDR_BIN" status 2>/dev/null | grep -q "status: running"; then
    echo "  ✓ Herdr-Server läuft"
  else
    echo "  ⚠ Herdr-Server-Start fehlgeschlagen"
  fi

  INFRA="$REPO_DIR/workspaces/infrastructure"

  # Workspaces anlegen (idempotent)
  "$HERDR_BIN" workspace list 2>/dev/null | grep -q "Home Assistant" \
    || "$HERDR_BIN" workspace create --cwd "$INFRA/homeassistant" --label "Home Assistant" >/dev/null 2>&1
  "$HERDR_BIN" workspace list 2>/dev/null | grep -q "Server Management" \
    || "$HERDR_BIN" workspace create --cwd "$INFRA/server-management" --label "Server Management" >/dev/null 2>&1

  # Panes ermitteln und OpenCode starten
  for pair in "Home Assistant:homeassistant:opencode-ha" "Server Management:server-management:opencode-sm"; do
    LABEL="${pair%%:*}"; REST="${pair#*:}"; NAME="${REST##*:}"
    PANE=$("$HERDR_BIN" workspace list 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for w in d['result']['workspaces']:
        if w['label'] == '$LABEL':
            print(w['active_tab_id'].split(':')[0] + ':p1')
            break
except Exception:
    pass
")
    if [ -n "$PANE" ]; then
      if ! "$HERDR_BIN" agent list 2>/dev/null | grep -q "\"$NAME\""; then
        "$HERDR_BIN" agent start "$NAME" --kind opencode --pane "$PANE" >/dev/null 2>&1 \
          && echo "  ✓ OpenCode gestartet: $LABEL ($PANE)" \
          || echo "  ⚠ OpenCode-Start fehlgeschlagen: $LABEL"
      else
        echo "  ✓ OpenCode läuft bereits: $LABEL"
      fi
    fi
  done
else
  echo "  ⚠ Herdr nicht installiert – Server und Workspaces übersprungen"
fi

echo ""
echo "=== Infrastructure Workspace bereit ==="
echo "  Öffne die App 'Infrastructure Workspace' in Coder."
echo ""