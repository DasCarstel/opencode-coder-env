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

# FORCE: Immer die korrekte URL (überschreibt ggf. gespeicherte Container-Env aus alten Template-Versionen)
export OPENBAO_ADDR="https://openbao.mueller-nas.de"
OPENBAO_ROLE_ID="${OPENBAO_ROLE_ID:-mcp-server}"

# /etc/opencode.env vorbereiten
: > /etc/opencode.env
chmod 600 /etc/opencode.env

if [ -z "${OPENBAO_APPROLE_SECRET_ID:-}" ]; then
  echo "ERROR: OPENBAO_APPROLE_SECRET_ID ist nicht gesetzt."
  echo "  Bitte in Coder → Templates → infrastructure → Secrets eintragen."
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
    echo "${varname}=\"${value}\"" >> /etc/opencode.env
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

# SSH-Key für Infrastruktur-Zugriffe generieren (falls nicht vorhanden)
echo "=== SSH-Key-Setup ==="
if [ ! -f /root/.ssh/id_ed25519_coder ]; then
  mkdir -p /root/.ssh
  chmod 700 /root/.ssh
  ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519_coder -N "" -C "coder-workspace-infrastructure" >/dev/null 2>&1
  chmod 600 /root/.ssh/id_ed25519_coder
  echo "  ✓ SSH-Key generiert"
else
  echo "  ✓ SSH-Key vorhanden"
fi

# SSH-Config erstellen
echo "=== SSH-Config ==="
cat > /root/.ssh/config << 'EOF'
Host docker
    HostName 10.0.10.10
    User root
    IdentityFile /root/.ssh/id_ed25519_coder
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null

Host proxmox
    HostName 10.0.10.20
    User root
    IdentityFile /root/.ssh/id_ed25519_coder
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null

Host truenas
    HostName 10.0.10.30
    User root
    IdentityFile /root/.ssh/id_ed25519_coder
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
EOF
chmod 600 /root/.ssh/config
echo "  ✓ SSH-Config erstellt"

# Herdr installieren (falls nicht vorhanden)
echo ""
echo "=== Herdr-Installation ==="
if ! command -v herdr &> /dev/null; then
  set +e
  curl -fsSL https://herdr.dev/install.sh | sh
  HERDR_EXIT=$?
  set -e
  if [ $HERDR_EXIT -eq 0 ]; then
    echo 'export PATH="/root/.local/bin:$PATH"' >> /root/.bashrc
    echo "  ✓ Herdr installiert"
  else
    echo "  ⚠ Herdr-Installation fehlgeschlagen (kein Internet?)"
  fi
else
  echo "  ✓ Herdr vorhanden"
fi

# ssh-mcp Config erstellen/aktualisieren
echo ""
echo "=== ssh-mcp Config ==="
mkdir -p ~/.config/ssh-mcp
cat > ~/.config/ssh-mcp/config.toml << 'EOF'
[defaults]
defaultProfile = "docker-host"

[[profiles]]
name = "docker-host"
host = "10.0.10.10"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_coder"
role = "admin"
approvalPolicy = "ask-destructive"

[[profiles]]
name = "proxmox"
host = "10.0.10.20"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_coder"
role = "viewer"
approvalPolicy = "ask-all"

[[profiles]]
name = "truenas"
host = "10.0.10.30"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_coder"
role = "viewer"
approvalPolicy = "ask-all"
EOF
chmod 700 ~/.config/ssh-mcp
chmod 600 ~/.config/ssh-mcp/config.toml
echo "  ✓ ssh-mcp Config erstellt"

# OpenCode V2 installieren/upgraden
echo ""
echo "=== OpenCode V2 Installation ==="
CURRENT_VERSION=$(opencode --version 2>&1 | grep -oP 'v\K[0-9]+' | head -1 || echo "0")
if [ "$CURRENT_VERSION" != "2" ]; then
  echo "  Deinstalliere OpenCode V1..."
  npm uninstall -g opencode 2>/dev/null || true
  rm -f /usr/bin/opencode
  echo "  Installiere OpenCode V2..."
  npm install -g @opencode/cli 2>&1 | tail -5
  echo "  ✓ OpenCode V2 installiert"
else
  echo "  ✓ OpenCode V2 bereits installiert"
fi

# Globale OpenCode Konfiguration erstellen (mit Python um $schema korrekt zu schreiben)
echo ""
echo "=== Globale OpenCode Konfiguration ==="
mkdir -p /root/.config/opencode
python3 << 'PYEOF'
import json
config = {
    "$schema": "https://opencode.ai/config.json",
    "mcp": {
        "servers": {
            "homeassistant": {
                "type": "remote",
                "url": "https://intern-homeassistant.mueller-nas.de/api/mcp",
                "headers": {
                    "Authorization": "Bearer {env:HA_LLA_TOKEN}",
                    "Accept": "application/json"
                }
            },
            "ssh-mcp": {
                "type": "local",
                "command": ["ssh-mcp"],
                "env": {
                    "SSH_AUTH_SOCK": "/tmp/ssh-auth.sock"
                }
            }
        }
    }
}
with open('/root/.config/opencode/opencode.json', 'w') as f:
    json.dump(config, f, indent=2)
print("  ✓ Globale Config erstellt")
PYEOF

# SSH-Key auf Infrastructure-Hosts deployen
echo ""
echo "=== SSH-Key Deployment ==="
if [ -f /root/.ssh/id_ed25519_coder.pub ]; then
  PUBKEY=$(cat /root/.ssh/id_ed25519_coder.pub)
  
  # Deploy to Docker
  ssh -o StrictHostKeyChecking=no root@10.0.10.10 "mkdir -p ~/.ssh && echo '$PUBKEY' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" 2>/dev/null && echo "  ✓ Key deployed to Docker" || echo "  ⚠ Docker deployment failed"
  
  # Deploy to Proxmox
  ssh -o StrictHostKeyChecking=no root@10.0.10.20 "mkdir -p ~/.ssh && echo '$PUBKEY' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" 2>/dev/null && echo "  ✓ Key deployed to Proxmox" || echo "  ⚠ Proxmox deployment failed"
  
  # Deploy to TrueNAS
  ssh -o StrictHostKeyChecking=no root@10.0.10.30 "mkdir -p ~/.ssh && echo '$PUBKEY' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" 2>/dev/null && echo "  ✓ Key deployed to TrueNAS" || echo "  ⚠ TrueNAS deployment failed"
else
  echo "  ⚠ No SSH key found, skipping deployment"
fi

# Herdr Setup - Startet beide Workspaces in separaten Sessions
echo ""
echo "=== Herdr Setup ==="
export PATH="/root/.local/bin:$PATH"

# Starte Herdr Server im Hintergrund
if ! pgrep -f "herdr serve" > /dev/null; then
  nohup herdr serve > /tmp/herdr-server.log 2>&1 &
  sleep 3
  if pgrep -f "herdr serve" > /dev/null; then
    echo "  ✓ Herdr Server gestartet"
  else
    echo "  ⚠ Herdr Server Start fehlgeschlagen"
    cat /tmp/herdr-server.log 2>/dev/null || true
  fi
else
  echo "  ✓ Herdr Server läuft bereits"
fi

# Erstelle Herdr Workspaces
echo "  Erstelle Herdr Workspaces..."

# Home Assistant Workspace
herdr workspace create --label "Home Assistant" --directory "/home/carstenmueller2002/opencode-coder-env/workspaces/infrastructure/homeassistant" 2>&1 || echo "  Workspace 'Home Assistant' existiert bereits"

# Server Management Workspace  
herdr workspace create --label "Server Management" --directory "/home/carstenmueller2002/opencode-coder-env/workspaces/infrastructure/server-management" 2>&1 || echo "  Workspace 'Server Management' existiert bereits"

echo "  ✓ Herdr Workspaces erstellt"

# Starte opencode in jedem Workspace
echo "  Starte OpenCode in den Workspaces..."
herdr run --workspace "Home Assistant" -- opencode 2>&1 || echo "  ⚠ OpenCode in 'Home Assistant' konnte nicht gestartet werden"
herdr run --workspace "Server Management" -- opencode 2>&1 || echo "  ⚠ OpenCode in 'Server Management' konnte nicht gestartet werden"
echo "  ✓ OpenCode in beiden Workspaces gestartet"

echo ""
echo "=========================================="
echo "Infrastructure Workspace Setup abgeschlossen"
echo "=========================================="
echo ""
echo "Verfügbare Komponenten:"
echo "  • OpenCode V2 mit HA MCP-Server (27 Tools)"
echo "  • SSH-Verbindungen: docker, proxmox, truenas"
echo "  • ssh-mcp mit 3 Host-Profilen"
echo "  • Herdr 0.9.3 für Multi-Session-Management"
echo ""
echo "Nutzung:"
echo "  • Öffne die 'Infrastructure Workspace' App in Coder"
echo "  • Wechsle zwischen 'Home Assistant' und 'Server Management' Workspaces"
echo "  • Beide OpenCode-Instanzen laufen parallel ohne Konflikte"
echo ""
