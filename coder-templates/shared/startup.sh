#!/bin/bash
# shared/startup.sh – Gemeinsames Setup für alle OpenCode-Workspaces
#
# Aufruf: startup.sh <profil>
#   infrastructure – HA + Server Management (ssh-mcp, authentik, HA, oCIS privat)
#   general        – Allgemeine Aufgaben (nur oCIS privat)
#   muellerconnect – MuellerConnect Mini-Job (nur oCIS Arbeit)
#
# Gemeinsam: OpenBao-Auth, Secrets, Git-Credentials, OpenCode V2, oCIS-MCP,
# Herdr, Env-Injektion. Profil-spezifisch: Secrets, MCP-Config, Skills,
# Herdr-Workspaces.

set -uo pipefail

PROFILE="${1:-}"
case "$PROFILE" in
  infrastructure|general|muellerconnect) ;;
  *) echo "ERROR: Profil angeben: infrastructure|general|muellerconnect"; exit 1 ;;
esac

# ── PATH sicherstellen ─────────────────────────────────────────────────
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:$PATH"
grep -q '/root/.local/bin' /root/.bashrc 2>/dev/null || echo 'export PATH="/root/.local/bin:$PATH"' >> /root/.bashrc
mkdir -p /root/.local/bin /usr/local/bin

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
echo "=== OpenBao: Authentifizierung ($PROFILE) ==="
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
    printf '%s="%s"\n' "${varname}" "${value}"  "  ✓ ${varname} geladen"
  fi
}

case "$PROFILE" in
  infrastructure)
    read_secret "secret/data/mcp/homeassistant"  "api_key"   "HA_LLA_TOKEN"
    read_secret "secret/data/mcp/authentik"      "api_key"   "AUTHENTIK_API_KEY"
    read_secret "secret/data/mcp/grafana"        "api_key"   "GRAFANA_API_KEY"
    read_secret "secret/data/mcp/opencloud"      "api_key"   "OPENCLOUD_API_KEY"
    read_secret "secret/data/mcp/opencloud"      "username"  "OPENCLOUD_USERNAME"
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_private_user"  "OCIS_PRIVATE_USER"
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_private_token" "OCIS_PRIVATE_TOKEN"
    read_secret "secret/data/mcp/tailscale"      "auth_key"  "TS_AUTHKEY"
    ;;
  general)
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_private_user"  "OCIS_PRIVATE_USER"
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_private_token" "OCIS_PRIVATE_TOKEN"
    ;;
  muellerconnect)
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_muellerconnect_user"  "OCIS_MUELLERCONNECT_USER"
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_muellerconnect_token" "OCIS_MUELLERCONNECT_TOKEN"
    ;;
esac
# OpenCode Go: mehrere Accounts (alle Profile)
read_secret "secret/data/mcp/opencode-go" "default1" "OC_GO_DEFAULT1"
read_secret "secret/data/mcp/opencode-go" "default2" "OC_GO_DEFAULT2"
read_secret "secret/data/mcp/opencode-go" "default3" "OC_GO_DEFAULT3"
read_secret "secret/data/mcp/opencode-go" "active"   "OC_GO_ACTIVE"

# ── GitHub-Credentials (git clone/push privater Repos) ──────────────────────
echo ""
echo "=== Git-Credentials (GitHub) ==="
if [ -f "$REPO_DIR/scripts/setup-git-credentials.sh" ]; then
  bash "$REPO_DIR/scripts/setup-git-credentials.sh" || echo "  ⚠ setup-git-credentials fehlgeschlagen"
else
  echo "  ⚠ scripts/setup-git-credentials.sh nicht gefunden"
fi

# ── SSH via OpenBao-CA + ssh-mcp (nur infrastructure) ──────────────────────
if [ "$PROFILE" = "infrastructure" ]; then
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

  # ── uvx + ssh-mcp installieren ────────────────────────────────────────────
  echo ""
  echo "=== uvx / ssh-mcp ==="
  if [ ! -x "$HOME/.local/bin/uvx" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -2 || true
  fi
  npm install -g ssh-mcp@2.17.0 2>&1 | tail -2 || true

  # ── ssh-mcp Konfiguration ───────────────────────────────────────────────────
  echo ""
  echo "=== ssh-mcp Config ==="
  mkdir -p /root/.config/ssh-mcp /root/.ssh-mcp-transfers && chmod 700 /root/.ssh-mcp-transfers
  cat > /root/.config/ssh-mcp/config.toml << 'MCPCFG'
[defaults]
defaultProfile = "docker-host"
approvalMode = "auto"
transferRoot = "/root/.ssh-mcp-transfers"

# Krefeld-Hosts (prod) + Kleve-Hosts (kleve): fuer server-management- und
# homeassistant-Space - alle Befehlsklassen pro Gruppe erlaubt.
# Die eingebaute Forbidden-Liste (rm -rf /, mkfs, ...) greift weiterhin.
[policy.roleBindings.admin]
prod = ["read-only", "safe", "destructive", "privileged"]
kleve = ["read-only", "safe", "destructive", "privileged"]

[[profiles]]
name = "docker-host"
host = "10.0.10.10"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "proxmox"
host = "10.0.10.20"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "truenas"
host = "10.0.10.30"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "pbs"
host = "10.0.10.21"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "coder"
host = "10.0.10.17"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "unifi"
host = "10.0.0.1"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "media"
host = "10.0.10.25"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "test"
host = "10.0.10.40"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

# Cleve Hosts (via Tailscale-Tunnel / Subnet-Route)
[[profiles]]
name = "cleve-proxmox"
host = "192.168.178.50"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-truenas"
host = "192.168.178.51"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-docker"
host = "192.168.178.52"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"
MCPCFG
  cat > /root/.config/ssh-mcp/config-full.toml << 'MCPCFG'
[defaults]
defaultProfile = "docker-host"
approvalMode = "auto"
transferRoot = "/root/.ssh-mcp-transfers"

# Krefeld-Hosts (prod) + Kleve-Hosts (kleve): fuer server-management- und
# homeassistant-Space - alle Befehlsklassen pro Gruppe erlaubt.
# Die eingebaute Forbidden-Liste (rm -rf /, mkfs, ...) greift weiterhin.
[policy.roleBindings.admin]
prod = ["read-only", "safe", "destructive", "privileged"]
kleve = ["read-only", "safe", "destructive", "privileged"]

[[profiles]]
name = "docker-host"
host = "10.0.10.10"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "proxmox"
host = "10.0.10.20"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "truenas"
host = "10.0.10.30"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "pbs"
host = "10.0.10.21"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "coder"
host = "10.0.10.17"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "unifi"
host = "10.0.0.1"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "media"
host = "10.0.10.25"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "test"
host = "10.0.10.40"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

# Cleve Hosts (via Tailscale-Tunnel / Subnet-Route)
[[profiles]]
name = "cleve-proxmox"
host = "192.168.178.50"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-truenas"
host = "192.168.178.51"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-docker"
host = "192.168.178.52"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"
MCPCFG
  cat > /root/.config/ssh-mcp/config-cleve.toml << 'MCPCFG'
[defaults]
defaultProfile = "cleve-docker"
approvalMode = "auto"
transferRoot = "/root/.ssh-mcp-transfers"

# Nur Kleve-Hosts (via Tailscale-Tunnel / Subnet-Route) - fuer den cleve-Space.
# Kein prod-Binding: Krefeld-Hosts sind hier bewusst nicht definiert
# (harte Trennlinie Site-Krefeld / Site-Kleve).
[policy.roleBindings.admin]
kleve = ["read-only", "safe", "destructive", "privileged"]

[[profiles]]
name = "cleve-proxmox"
host = "192.168.178.50"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-truenas"
host = "192.168.178.51"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-docker"
host = "192.168.178.52"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"
MCPCFG
  chmod 700 /root/.config/ssh-mcp
  chmod 600 /root/.config/ssh-mcp/config.toml /root/.config/ssh-mcp/config-full.toml /root/.config/ssh-mcp/config-cleve.toml
  echo "  ✓ ssh-mcp Config erstellt"
fi

# ── Tailscale (Cleve-Zugang, nur infrastructure) ────────────────────────────
if [ "$PROFILE" = "infrastructure" ]; then
  echo ""
  echo "=== Tailscale (Cleve) ==="
  if [ -n "${TS_AUTHKEY:-}" ]; then
    if ! command -v tailscale >/dev/null 2>&1; then
      echo "  → Tailscale wird installiert..."
      curl -fsSL https://tailscale.com/install.sh | sh 2>&1 | tail -2 || true
    fi
    if command -v tailscale >/dev/null 2>&1; then
      mkdir -p /var/lib/tailscale /var/run/tailscale
      TS_SOCK=/var/run/tailscale/tailscaled.sock
      if ! tailscale --socket="$TS_SOCK" status >/dev/null 2>&1; then
        echo "  → tailscaled wird gestartet..."
        nohup tailscaled --state=/var/lib/tailscale/tailscaled.state --socket="$TS_SOCK" \
          > /tmp/tailscaled.log 2>&1 &
        sleep 3
      fi
      tailscale --socket="$TS_SOCK" up \
        --authkey="$TS_AUTHKEY" \
        --hostname="coder-${PROFILE}" \
        --accept-routes \
        --accept-dns=false \
        --ssh=false \
        --timeout=30s 2>&1 | tail -3 || echo "  ⚠ tailscale up fehlgeschlagen"
      if tailscale --socket="$TS_SOCK" status >/dev/null 2>&1; then
        echo "  ✓ Tailscale aktiv: $(tailscale --socket="$TS_SOCK" ip -4 2>/dev/null | head -1)"
      else
        echo "  ⚠ Tailscale-Status nicht abrufbar"
      fi
    else
      echo "  ⚠ Tailscale nicht verfügbar"
    fi
  else
    echo "  ⚠ TS_AUTHKEY nicht gesetzt (secret/data/mcp/tailscale)"
  fi
fi

# ── OpenCode V2 ─────────────────────────────────────────────────────────────
echo ""
echo "=== OpenCode V2 ==="
mkdir -p ~/.config/opencode ~/.cache/opencode/opencode-model-router
if [ -f "$REPO_DIR/tiers.json" ]; then
  ln -sf "$REPO_DIR/tiers.json" ~/.cache/opencode/opencode-model-router/tiers.json || true
fi
echo 'alias update-opencode-config="cd ~/opencode-coder-env && git pull --ff-only"' >> ~/.bashrc

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

# ── MCP-Konfiguration (Config installieren, profil-spezifisch) ──────────────
echo ""
echo "=== MCP-Konfiguration ==="
case "$PROFILE" in
  infrastructure) GLOBAL_CFG_SRC="$REPO_DIR/coder-templates/infrastructure/config/global-opencode.json" ;;
  general)        GLOBAL_CFG_SRC="$REPO_DIR/workspaces/general/workspace-global.json" ;;
  muellerconnect)        GLOBAL_CFG_SRC="$REPO_DIR/workspaces/minijob/workspace-global.json" ;;
esac
if [ -f "$GLOBAL_CFG_SRC" ]; then
  install -m 600 "$GLOBAL_CFG_SRC" /root/.config/opencode/opencode.json
  echo "  ✓ Config installiert ($PROFILE)"
else
  echo "  ⚠ Config nicht gefunden: $GLOBAL_CFG_SRC"
fi

# ── Env-Injektion: HA / authentik (nur infrastructure) ──────────────────────
if [ "$PROFILE" = "infrastructure" ]; then
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

# ── oCIS MCP Server (OpenCloud / Obsidian) ──────────────────────────────────
echo ""
echo "=== oCIS MCP Server (OpenCloud) ==="
OCIS_MCP_BIN="/root/.local/bin/ocis-mcp-server"
if [ ! -x "$OCIS_MCP_BIN" ]; then
  echo "  → lade ocis-mcp-server herunter..."
  curl -fsSL -o /tmp/ocis-mcp.tar.gz "https://github.com/owncloud/ocis-mcp-server/releases/download/v1.1.0/ocis-mcp-server_1.1.0_linux_amd64.tar.gz" \
    && tar xzf /tmp/ocis-mcp.tar.gz -C /tmp \
    && install -m 0755 /tmp/ocis-mcp-server "$OCIS_MCP_BIN" \
    && rm -f /tmp/ocis-mcp.tar.gz /tmp/ocis-mcp-server \
    && echo "  ✓ ocis-mcp-server installiert" \
    || echo "  ⚠ ocis-mcp-server konnte nicht heruntergeladen werden"
else
  echo "  ✓ ocis-mcp-server bereits installiert"
fi

# Profil-spezifische Token-Env + Hard-Guardrail (jeder Workspace sieht nur
# seinen eigenen Space; das jeweils andere Token wird aktiv entfernt).
case "$PROFILE" in
  infrastructure|general)
    if [ -n "${OCIS_PRIVATE_USER:-}" ] && [ -n "${OCIS_PRIVATE_TOKEN:-}" ]; then
      opencode service set env OCIS_PRIVATE_USER "$OCIS_PRIVATE_USER" >/dev/null 2>&1 || true
      opencode service set env OCIS_PRIVATE_TOKEN "$OCIS_PRIVATE_TOKEN" >/dev/null 2>&1 || true
      echo "  ✓ oCIS Private-Token im OpenCode-Service gesetzt"
    fi
    opencode service unset env OCIS_MUELLERCONNECT_USER  >/dev/null 2>&1 || true
    opencode service unset env OCIS_MUELLERCONNECT_TOKEN >/dev/null 2>&1 || true
    ;;
  muellerconnect)
    if [ -n "${OCIS_MUELLERCONNECT_USER:-}" ] && [ -n "${OCIS_MUELLERCONNECT_TOKEN:-}" ]; then
      opencode service set env OCIS_MUELLERCONNECT_USER "$OCIS_MUELLERCONNECT_USER" >/dev/null 2>&1 || true
      opencode service set env OCIS_MUELLERCONNECT_TOKEN "$OCIS_MUELLERCONNECT_TOKEN" >/dev/null 2>&1 || true
      echo "  ✓ oCIS MuellerConnect-Token im OpenCode-Service gesetzt"
    fi
    opencode service unset env OCIS_PRIVATE_USER  >/dev/null 2>&1 || true
    opencode service unset env OCIS_PRIVATE_TOKEN >/dev/null 2>&1 || true
    ;;
esac

# ── Herdr installieren ─────────────────────────────────────────────────────
echo ""
echo "=== Herdr ==="
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

  # Profil-spezifische Herdr-Workspaces (label:relpath:agentname)
  # relpath = "." → Workspace-Basisverzeichnis selbst
  case "$PROFILE" in
    infrastructure)
      WS_BASE="$REPO_DIR/workspaces/infrastructure"
      WS_LIST=("Home Assistant:homeassistant:opencode-ha" "Server Management:server-management:opencode-sm" "Svelte:svelte:opencode-svelte" "Cleve:cleve:opencode-cleve")
      ;;
    general)
      WS_BASE="$REPO_DIR/workspaces/general"
      WS_LIST=("General:.:opencode-general")
      ;;
    muellerconnect)
      WS_BASE="$REPO_DIR/workspaces/minijob"
      WS_LIST=("MuellerConnect:.:opencode-mc")
      ;;
  esac

  for entry in "${WS_LIST[@]}"; do
    LABEL="${entry%%:*}"; REST="${entry#*:}"; RELPATH="${REST%%:*}"; NAME="${REST##*:}"
    if [ "$RELPATH" = "." ]; then WS_DIR="$WS_BASE"; else WS_DIR="$WS_BASE/$RELPATH"; fi

    # Workspace anlegen (idempotent)
    "$HERDR_BIN" workspace list 2>/dev/null | grep -q "$LABEL" \
      || "$HERDR_BIN" workspace create --cwd "$WS_DIR" --label "$LABEL" >/dev/null 2>&1

    # Pane ermitteln und OpenCode starten
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
echo "=== $PROFILE Workspace bereit ==="
echo "  Öffne die App in Coder."
echo ""
