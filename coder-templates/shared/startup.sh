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

# ── Versions-Pins laden (repo-gesteuert; Anzeige: scripts/check-updates.sh) ─
if [ -f "$SCRIPT_DIR/../versions.env" ]; then
  # shellcheck source=../versions.env
  source "$SCRIPT_DIR/../versions.env"
else
  echo "⚠ coder-templates/versions.env nicht gefunden – Versionspins nicht verfügbar"
  SSH_MCP_VERSION=""
  OCIS_MCP_VERSION=""
  HERDR_VERSION=""
fi

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
    read_secret "secret/data/mcp/homeassistant-kleve" "api_key" "HA_KLEVE_LLA_TOKEN"
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
# KEIN read_secret mehr auf "active": der aktive Account wird allein von Hestia
# über die SOT .opencode/hestia-accounts.jsonc bestimmt (siehe unten).
# OpenCode-Server-Password (Fleet-Zugriff: Hestia + Hermes space-matrix)
read_secret "secret/data/mcp/hermes-opencode-service" "password" "OPENCODE_SERVER_PASSWORD"

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

  # ── uvx + ssh-mcp installieren (Pin via versions.env, Stempel-Datei) ──────
  echo ""
  echo "=== uvx / ssh-mcp ==="
  if [ ! -x "$HOME/.local/bin/uvx" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -2 || true
  fi
  SSH_MCP_STAMP="$HOME/.local/bin/.ssh-mcp-version"
  if [ -z "${SSH_MCP_VERSION}" ]; then
    echo "  ⚠ SSH_MCP_VERSION nicht gesetzt – ssh-mcp läuft weiter in aktueller Version"
  elif [ "$(cat "$SSH_MCP_STAMP" 2>/dev/null)" != "${SSH_MCP_VERSION}" ]; then
    echo "  → ssh-mcp ${SSH_MCP_VERSION} installieren..."
    if npm install -g "ssh-mcp@${SSH_MCP_VERSION}" >/tmp/npm-ssh-mcp.log 2>&1; then
      tail -2 /tmp/npm-ssh-mcp.log
      echo "${SSH_MCP_VERSION}" > "$SSH_MCP_STAMP"
      echo "  ✓ ssh-mcp ${SSH_MCP_VERSION} installiert"
    else
      tail -3 /tmp/npm-ssh-mcp.log
      echo "  ⚠ ssh-mcp ${SSH_MCP_VERSION} konnte nicht installiert werden – bisherige Version bleibt"
    fi
  else
    echo "  ✓ ssh-mcp bereits auf Pin (${SSH_MCP_VERSION})"
  fi

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

# ── OpenCode V2 (Hybrid: bei jedem Start auf npm-latest abgleichen) ────────
echo ""
echo "=== OpenCode V2 (immer latest) ==="
mkdir -p ~/.config/opencode
# Model-Router-Konfiguration liegt in .opencode/opencode-model-router.overrides.jsonc
# am Repo-Root (das Plugin durchsucht von cwd aufwärts bis Repo-Root) —
# kein Symlink/Cache-Eingriff mehr nötig. tiers.json entfernt (Historie in git).
echo 'alias update-opencode-config="cd ~/opencode-coder-env && git pull --ff-only"' >> ~/.bashrc

if command -v npm >/dev/null 2>&1; then
  CUR=$(opencode --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  NEW=$(npm view @opencode/cli version 2>/dev/null | tail -1)
  if [ -z "$CUR" ] || { [ -n "$NEW" ] && [ "$CUR" != "$NEW" ]; }; then
    echo "  → OpenCode installieren/aktualisieren: ${CUR:-fehlt} → ${NEW:-latest} ..."
    npm uninstall -g opencode 2>/dev/null || true
    npm uninstall -g @opencode-ai/opencode 2>/dev/null || true
    rm -f /usr/bin/opencode
    npm install -g @opencode/cli 2>&1 | tail -3
    GOT=$(opencode --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [ -n "$GOT" ]; then
      echo "  ✓ OpenCode: $(opencode --version 2>&1)"
      opencode service restart >/dev/null 2>&1 \
        && echo "  ✓ OpenCode-Service neu gestartet" \
        || echo "  ⚠ OpenCode-Service läuft noch nicht – Neustart wird ohnehin beim Boot erfolgen"
    else
      echo "  ⚠ OpenCode-Installation fehlgeschlagen: $(opencode --version 2>&1)"
    fi
  elif [ -z "$NEW" ]; then
    echo "  ⚠ npm-Abfrage fehlgeschlagen – OpenCode ${CUR:-?} bleibt bestehen"
  else
    echo "  ✓ OpenCode bereits aktuell: $(opencode --version 2>&1)"
  fi
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

# ── OpenCode-Theme: dauerhaft hell (cli.json, idempotent) ───────────────────
mkdir -p ~/.config/opencode
python3 - <<'PY' >/dev/null 2>&1
import os, json
p = os.path.expanduser("~/.config/opencode/cli.json")
try:
    d = json.load(open(p))
    if not isinstance(d, dict):
        d = {}
except Exception:
    d = {}
d.setdefault("$schema", "https://opencode.ai/v2/cli.json")
d["theme"] = {"name": "opencode", "mode": "light"}
with open(p, "w") as f:
    json.dump(d, f, indent=2)
os.chmod(p, 0o600)
PY

# ── Env-Injektion: HA / authentik (nur infrastructure) ──────────────────────
if [ "$PROFILE" = "infrastructure" ]; then
  if [ -n "${HA_LLA_TOKEN:-}" ]; then
    opencode service set env HA_LLA_TOKEN "$HA_LLA_TOKEN" >/dev/null 2>&1 \
      && echo "  ✓ HA_LLA_TOKEN im OpenCode-Service gesetzt" \
      || echo "  ⚠ HA_LLA_TOKEN konnte nicht gesetzt werden"
  fi
  if [ -n "${HA_KLEVE_LLA_TOKEN:-}" ]; then
    opencode service set env HA_KLEVE_LLA_TOKEN "$HA_KLEVE_LLA_TOKEN" >/dev/null 2>&1 \
      && echo "  ✓ HA_KLEVE_LLA_TOKEN im OpenCode-Service gesetzt" \
      || echo "  ⚠ HA_KLEVE_LLA_TOKEN konnte nicht gesetzt werden"
  fi
  if [ -n "${AUTHENTIK_API_KEY:-}" ]; then
    opencode service set env AUTHENTIK_API_KEY "$AUTHENTIK_API_KEY" >/dev/null 2>&1 \
      && echo "  ✓ AUTHENTIK_API_KEY im OpenCode-Service gesetzt" \
      || echo "  ⚠ AUTHENTIK_API_KEY konnte nicht gesetzt werden"
  fi
fi

# ── Model-Router: Overrides GLOBAL ablegen (vor dem Service-Start!) ─────────
# Sessions/Services mit cwd außerhalb des Repos (z. B. /root) finden die
# Tier-Konfiguration nur, wenn sie auch global liegt.
OVERRIDES_SRC="$REPO_DIR/.opencode/opencode-model-router.overrides.jsonc"
if [ -f "$OVERRIDES_SRC" ]; then
  install -m 600 "$OVERRIDES_SRC" /root/.config/opencode/opencode-model-router.overrides.jsonc \
    && echo "  ✓ Overrides global verfügbar (Service-Kontexte)" \
    || echo "  ⚠ Overrides konnten nicht global abgelegt werden"
fi

# ── OpenCode Go (mehrere Keys aus OpenBao) ──────────────────────────────────
# Der aktive Account kommt aus der SOT .opencode/hestia-accounts.jsonc (von
# Hestia gepflegt) — nicht aus dem OpenBao-Feld "active" (das wird nicht mehr
# gelesen/geschrieben). Ohne SOT (kein Repo-Checkout) greift der Default.
OC_GO_ACTIVE=""
SOT_OC_GO="${OVERRIDES_SRC%/opencode-model-router.overrides.jsonc}/hestia-accounts.jsonc"
if [ -r "$SOT_OC_GO" ]; then
  OC_GO_ACTIVE="$(python3 -c '
import json, re, sys
raw = open(sys.argv[1], encoding="utf-8").read()
try:
    import hjsonc
    cfg = hjsonc.parse(raw) or {}
except Exception:
    raw = re.sub(r"^[ \t]*//.*$|[ \t]+//.*$", "", raw, flags=re.M)
    cfg = json.loads(re.sub(r",(\s*[}\]])", r"\1", raw))
print(str(cfg.get("active") or ""))
' "$SOT_OC_GO" 2>/dev/null)"
fi
[ -z "$OC_GO_ACTIVE" ] && OC_GO_ACTIVE="${OC_GO_ACTIVE:-default1}"
ACTIVE_VAR="OC_GO_${OC_GO_ACTIVE^^}"
OPENCODE_API_KEY="${!ACTIVE_VAR:-}"

if [ -n "$OPENCODE_API_KEY" ]; then
  export OPENCODE_API_KEY
  # .bashrc IMMER erst deduppen, dann genau EINE Zeile anhängen (kein blindes >>).
  sed -i '/^export OPENCODE_API_KEY=/d' /root/.bashrc 2>/dev/null || true
  printf 'export OPENCODE_API_KEY="%s"\n' "$OPENCODE_API_KEY" >> /root/.bashrc
  # /root/.config/opencode/service.json ist EPHEMER → nach jedem Container-
  # Neustart hier den Key des aktiven Kontos neu ins Service-Env setzen.
  # Der eigentliche Service-Start passiert später; Fehler sind tolerierbar.
  opencode service set env OPENCODE_API_KEY "$OPENCODE_API_KEY" >/dev/null 2>&1 || true
  echo "  ✓ OpenCode Go aktiv: $OC_GO_ACTIVE (SOT)"
else
  echo "  ⚠ Kein OpenCode Go Key gefunden"
fi

# ── Primary-Agents (build/plan) aus @medium ableiten ───────────────────────
# build = @medium (Standardvariante), plan = @medium mit höchster Variante.
# Nutzt den laufenden OpenCode-Server für die Varianten-Liste.
echo ""
echo "=== Primary-Agents (build/plan) ==="
if [ -f "$OVERRIDES_SRC" ]; then
  # OpenCode-Server sicherstellen — die Varianten-Liste kommt von /api/model
  opencode service start >/dev/null 2>&1 || true
  REPO_DIR="$REPO_DIR" python3 "$REPO_DIR/coder-templates/shared/derive-agents.py" \
    || echo "  ⚠ Primary-Agents konnten nicht abgeleitet werden"
fi

# Helper zum Umschalten zwischen den OpenCode-Go-Keys
cat > /usr/local/bin/oc-go << 'OCGO'
#!/bin/bash
# OpenCode Go Key verwalten:
#   oc-go list              → lokal gesetzter Key/Account (verwaltet von Hestia)
#   oc-go use <name>        → Key lokal wechseln (.bashrc + Service-Env, kein OpenBao-Schreibzugriff)
# Hinweis: es gibt bewusst KEIN 'oc-go default' mehr. Welcher Account aktiv ist,
# entscheidet allein Hestia über .opencode/hestia-accounts.jsonc.
source /etc/opencode.env 2>/dev/null
case "${1:-list}" in
  list)
    echo "Verfügbare OpenCode-Go-Keys:"
    for n in default1 default2 default3; do
      v="OC_GO_${n^^}"; [ -n "${!v:-}" ] && echo "  - $n"
    done
    # Aktiv ist der LOKAL gesetzte Key (nicht mehr OpenBao). Quelle der Wahrheit
    # für die Auswahl bleibt Hestia; hier nur anzeigen, was tatsächlich gesetzt ist.
    ACT_KEY=""
    if [ -r /root/.bashrc ]; then
      ACT_KEY="$(grep -m1 '^export OPENCODE_API_KEY=' /root/.bashrc 2>/dev/null | cut -d= -f2- | tr -d "\"'")"
    fi
    [ -z "$ACT_KEY" ] && ACT_KEY="${OPENCODE_API_KEY:-}"
    ACT="unbekannt"
    if [ -n "$ACT_KEY" ]; then
      for n in default1 default2 default3; do
        v="OC_GO_${n^^}"
        [ -n "${!v:-}" ] && [ "$v" = "$ACT_KEY" ] && ACT="$n"
      done
    fi
    echo "Aktiv (lokal gesetzt): ${ACT}"
    echo "  Key: ${ACT_KEY:+gesetzt (${#ACT_KEY} Zeichen)}${ACT_KEY:-NICHT gesetzt}"
    echo "  Hinweis: Konto-Auswahl wird ausschließlich von Hestia (Panel) verwaltet —"
    echo "           SOT: .opencode/hestia-accounts.jsonc (Feld \"active\")."
    ;;
  use)
    NAME="${2:-}"
    [ -z "$NAME" ] && { echo "Nutzung: oc-go use <default1|default2|default3>"; exit 1; }
    VAR="OC_GO_${NAME^^}"; KEY="${!VAR:-}"
    [ -z "$KEY" ] && { echo "Unbekannter Key: $NAME"; exit 1; }
    # .bashrc erst deduppen, dann genau EINE Zeile anhängen (kein blindes >>).
    sed -i '/^export OPENCODE_API_KEY=/d' /root/.bashrc 2>/dev/null || true
    printf 'export OPENCODE_API_KEY="%s"\n' "$KEY" >> /root/.bashrc
    export OPENCODE_API_KEY="$KEY"
    opencode service set env OPENCODE_API_KEY "$KEY" >/dev/null 2>&1 || true
    echo "✓ Lokal aktiv: $NAME (nur dieser Container — dauerhaft umschalten: Hestia-Panel)"
    ;;
  default)
    echo "✗ 'oc-go default' ist entfernt."
    echo "  Der aktive OpenCode-Go-Account wird ausschließlich von Hestia (Panel)"
    echo "  über die SOT .opencode/hestia-accounts.jsonc (Feld \"active\") bestimmt."
    echo "  Für einen lokalen Test: oc-go use <default1|default2|default3>"
    exit 1
    ;;
  *) echo "Nutzung: oc-go [list | use <name>]";;
esac
OCGO
chmod +x /usr/local/bin/oc-go
echo "  ✓ Helper 'oc-go' installiert"

# ── OpenCode-Server (Service): 0.0.0.0 + OpenBao-paritätisches Passwort ─────
# Der v2-Background-Service regeneriert bei fehlender Konfig Passwort und
# bindet loopback — für Hestia/Hermes-Fleet-Zugriff (Port 49374) brauchen wir
# 0.0.0.0 + das Passwort aus secret/data/mcp/hermes-opencode-service (Goldquelle).
echo ""
echo "=== OpenCode-Server-Binding (Service) ==="
if [ -n "${OPENCODE_SERVER_PASSWORD:-}" ]; then
  opencode service set env OPENCODE_SERVER_PASSWORD "${OPENCODE_SERVER_PASSWORD}" >/dev/null 2>&1 || true
fi
mkdir -p ~/.config/opencode
python3 - <<'PY' >/dev/null 2>&1
import os, json
p = os.path.expanduser("~/.config/opencode/service.json")
try:
    d = json.load(open(p))
except Exception:
    d = {}
d["hostname"] = "0.0.0.0"
d.setdefault("port", 49374)
pw = os.environ.get("OPENCODE_SERVER_PASSWORD")
if pw:
    d["password"] = pw
json.dump(d, open(p, "w"), indent=2)
PY
opencode service restart >/dev/null 2>&1 || true
ss -tlnp 2>/dev/null | grep 49374 >/dev/null \
  && echo "  ✓ OpenCode-Server: 0.0.0.0:49374" \
  || echo "  ⚠ OpenCode-Server-Binding unklar"

# ── oCIS MCP Server (OpenCloud / Obsidian) ──────────────────────────────────
echo ""
echo "=== oCIS MCP Server (OpenCloud) ==="
OCIS_MCP_BIN="/root/.local/bin/ocis-mcp-server"
OCIS_MCP_STAMP="/root/.local/bin/.ocis-mcp-server-version"
if [ -z "${OCIS_MCP_VERSION}" ]; then
  echo "  ⚠ OCIS_MCP_VERSION nicht gesetzt – ocis-mcp-server läuft weiter in aktueller Version"
elif [ ! -x "$OCIS_MCP_BIN" ] || [ "$(cat "$OCIS_MCP_STAMP" 2>/dev/null)" != "${OCIS_MCP_VERSION}" ]; then
  echo "  → lade ocis-mcp-server v${OCIS_MCP_VERSION} herunter..."
  if curl -fsSL -o /tmp/ocis-mcp.tar.gz \
      "https://github.com/owncloud/ocis-mcp-server/releases/download/v${OCIS_MCP_VERSION}/ocis-mcp-server_${OCIS_MCP_VERSION}_linux_amd64.tar.gz" \
     && tar xzf /tmp/ocis-mcp.tar.gz -C /tmp \
     && install -m 0755 /tmp/ocis-mcp-server "$OCIS_MCP_BIN" \
     && rm -f /tmp/ocis-mcp.tar.gz /tmp/ocis-mcp-server; then
    echo "${OCIS_MCP_VERSION}" > "$OCIS_MCP_STAMP"
    echo "  ✓ ocis-mcp-server installiert (v${OCIS_MCP_VERSION})"
  else
    echo "  ⚠ ocis-mcp-server konnte nicht auf v${OCIS_MCP_VERSION} gebracht werden – alte Version (falls vorhanden) bleibt"
  fi
else
  echo "  ✓ ocis-mcp-server bereits auf Pin (v${OCIS_MCP_VERSION})"
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

# ── Herdr installieren (Pin via versions.env; Quelle: herdr.dev-API,
#    Fallback: Host-Bind-Mount /opt/opencode-bin) ───────────────────────────
echo ""
echo "=== Herdr ==="
herdrver() { "$1" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1; }

HERDR_CUR=$(herdrver "$HERDR_BIN")
HERDR_HOSTBIN=/opt/opencode-bin/herdr
if [ -z "${HERDR_VERSION}" ]; then
  echo "  ⚠ HERDR_VERSION nicht gesetzt – Herdr läuft weiter in Version ${HERDR_CUR:-?}"
elif [ "$HERDR_CUR" = "${HERDR_VERSION}" ]; then
  echo "  ✓ Herdr bereits auf Pin (${HERDR_VERSION})"
elif [ -x "$HERDR_HOSTBIN" ] && [ "$(herdrver "$HERDR_HOSTBIN")" = "${HERDR_VERSION}" ]; then
  cp "$HERDR_HOSTBIN" "$HERDR_BIN" \
    && chmod +x "$HERDR_BIN" \
    && echo "  ✓ Herdr ${HERDR_VERSION} aus /opt/opencode-bin installiert" \
    || echo "  ⚠ Herdr aus /opt/opencode-bin konnte nicht installiert werden"
else
  echo "  → Herdr ${HERDR_VERSION} wird heruntergeladen (herdr.dev)..."
  if curl -fsSL --max-time 60 -o "$HERDR_BIN.tmp" \
      "https://herdr.dev/api/releases/v${HERDR_VERSION}/linux-amd64" \
     && chmod +x "$HERDR_BIN.tmp" \
     && [ "$(herdrver "$HERDR_BIN.tmp")" = "${HERDR_VERSION}" ]; then
    mv "$HERDR_BIN.tmp" "$HERDR_BIN"
    echo "  ✓ Herdr installiert (${HERDR_VERSION})"
  else
    rm -f "$HERDR_BIN.tmp"
    if [ -x "$HERDR_HOSTBIN" ]; then
      cp "$HERDR_HOSTBIN" "$HERDR_BIN" && chmod +x "$HERDR_BIN" \
        && echo "  ⚠ Download fehlgeschlagen – bleibe bei Version $(herdrver "$HERDR_BIN") aus /opt/opencode-bin" \
        || echo "  ⚠ Download fehlgeschlagen und Host-Fallback nicht installierbar"
    else
      echo "  ⚠ Herdr ${HERDR_VERSION} weder per Download noch Host-Fallback verfügbar"
    fi
  fi
fi

# ── Herdr-Server + Workspaces ───────────────────────────────────────────────
echo ""
echo "=== Herdr-Server & Workspaces ==="
export PATH="/root/.local/bin:$PATH"

# ── Herdr-Theme: dauerhaft hell (config.toml [theme], idempotent) ───────────
mkdir -p /root/.config/herdr
python3 - <<'PY' >/dev/null 2>&1
import os
p = "/root/.config/herdr/config.toml"
try:
    with open(p, encoding="utf-8") as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    lines = []
want = [("name", 'name = "catppuccin-latte"'), ("auto_switch", "auto_switch = false")]
start = None
for i, l in enumerate(lines):
    if l.strip() == "[theme]":
        start = i
        break
if start is None:
    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.append("[theme]")
    lines += [v for _, v in want]
else:
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].lstrip().startswith("["):
            end = j
            break
    for key, val in want:
        idx = None
        for j in range(start + 1, end):
            if "=" in lines[j] and lines[j].split("=", 1)[0].strip() == key:
                idx = j
                break
        if idx is not None:
            lines[idx] = val
        else:
            lines.insert(end, val)
            end += 1
with open(p, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
os.chmod(p, 0o600)
PY
if [ -x "$HERDR_BIN" ] && "$HERDR_BIN" status 2>/dev/null | grep -q "status: running"; then
  "$HERDR_BIN" server reload-config >/dev/null 2>&1 || true
fi

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

# ~ == HA-Kleve TCP-Relay (fuer Container ohne Tailscale, z. B. hermes) == ~
HA_FORWARD_SCRIPT="/home/carstenmueller2002/opencode-coder-env/coder-templates/shared/ha-forward.py"
if [ -f "$HA_FORWARD_SCRIPT" ]; then
  if ! curl -sS --max-time 3 -o /dev/null http://127.0.0.1:18123/api/ 2>/dev/null; then
    echo "  > HA-Kleve TCP-Relay starten (127.0.0.1:18123 -> 192.168.178.52:8123)..."
    pkill -f "ha-forward.py" 2>/dev/null || true
    nohup python3 "$HA_FORWARD_SCRIPT" 18123 192.168.178.52 8123 > /tmp/ha-forward.log 2>&1 &
    sleep 1
  else
    echo "  > HA-Kleve TCP-Relay laeuft bereits"
  fi
fi

echo ""
