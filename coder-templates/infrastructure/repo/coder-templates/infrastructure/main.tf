# Coder Template: infrastructure (OpenCode-Infrastruktur)
#
# Provisions einen Docker-Container als Workspace für OpenCode-Entwicklung.
# Secrets werden zur Laufzeit aus OpenBao via AppRole-Auth bezogen.

terraform {
  required_providers {
    coder = {
      source  = "coder/coder"
      version = ">= 1.0"
    }
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

# ─── Workspace-Daten ────────────────────────────────────────────────────────

data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}
data "coder_provisioner" "me" {}

# ─── Template-Parameter ─────────────────────────────────────────────────────

variable "git_repo_url" {
  type        = string
  description = "URL zum opencode-coder-env Repository"
  default     = "https://github.com/DasCarstel/opencode-coder-env.git"
}

variable "git_branch" {
  type        = string
  description = "Branch, der geklont werden soll"
  default     = "master"
}

variable "openbao_role_id" {
  type        = string
  description = "OpenBao AppRole Role-ID (UUID, nicht der Rollenname)"
  default     = "a18d13b0-9ccd-304b-da63-db3545546b1d"
}

variable "openbao_addr" {
  type        = string
  description = "OpenBao Adresse (wird vom Workspace aus erreicht)"
  default     = "https://openbao.mueller-nas.de"
}

data "coder_parameter" "openbao_approle_secret_id" {
  name         = "openbao_approle_secret_id"
  display_name = "OpenBao AppRole Secret-ID"
  description  = "Secret-ID für die AppRole 'mcp-server' in OpenBao."
  type         = "string"
  order        = 1
  mutable      = true
}

# ─── Docker Workspace Container ────────────────────────────────────────────

resource "docker_image" "workspace" {
  name = "workspace:v2"
  
  build {
    context    = "${path.module}"
    dockerfile = "Dockerfile"
    build_args = {
      CACHE_DATE = timestamp()
    }
  }
}

resource "docker_container" "workspace" {
  count = data.coder_workspace.me.start_count

  image   = docker_image.workspace.image_id
  name    = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}"
  restart = "unless-stopped"

  env = [
    "CODER_AGENT_TOKEN=${coder_agent.main.token}",
    "OPENBAO_APPROLE_SECRET_ID=${data.coder_parameter.openbao_approle_secret_id.value}",
    "OPENBAO_ADDR=${var.openbao_addr}",
    "OPENBAO_ROLE_ID=${var.openbao_role_id}",
  ]

  command = ["sh", "-c", coder_agent.main.init_script]

  cpu_shares = 2048
  memory     = 2048
  shm_size   = 512

  networks_advanced {
    name = "coder-infra"
  }
}

# ── Coder Agent ─────────────────────────────────────────────────────────────

resource "coder_agent" "main" {
  os   = "linux"
  arch = data.coder_provisioner.me.arch

  dir = "/home/${data.coder_workspace_owner.me.name}"

  startup_script = <<-EOT
    #!/bin/bash
    set -euo pipefail

    echo "=== infrastructure Workspace: Initialisierung ==="

    # 1. Grundlegende Tools installieren
    export DEBIAN_FRONTEND=noninteractive
    apt-get update && apt-get install -y \
      curl \
      git \
      ca-certificates \
      gnupg \
      python3 \
      python3-pip \
    && rm -rf /var/lib/apt/lists/*

    # 2. Node.js v22 installieren (für ssh-mcp)
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y nodejs

    # 3. Repo aus Docker-Image kopieren (bereits eingebaut)
    REPO_DIR="/home/${data.coder_workspace_owner.me.name}/opencode-coder-env"
    mkdir -p "/home/${data.coder_workspace_owner.me.name}"
    if [ ! -d "$REPO_DIR" ]; then
      cp -r /repo "$REPO_DIR"
    else
      cd "$REPO_DIR"
      git fetch origin master 2>/dev/null || true
      git reset --hard origin/master 2>/dev/null || true
    fi

    # 4. Verzeichnisse anlegen
    mkdir -p ~/.config/opencode
    mkdir -p ~/.cache/opencode/opencode-model-router
    mkdir -p ~/.config/ssh-mcp

    # 5. tiers.json für Model-Router verlinken
    ln -sf "$REPO_DIR/tiers.json" ~/.cache/opencode/opencode-model-router/tiers.json

    # 6. Skills verlinken
    for skill_dir in "$REPO_DIR/skills/infrastructure"/*/; do
      [ -d "$skill_dir" ] || continue
      skill_name=$(basename "$skill_dir")
      mkdir -p ~/.config/opencode/skills/"$skill_name"
      ln -sf "$skill_dir/SKILL.md" ~/.config/opencode/skills/"$skill_name"/SKILL.md
    done

    # 7. OpenCode installieren (via npm, da Node.js bereits installiert)
    echo "=== Schritt 7: OpenCode installieren ==="
    npm install -g opencode-ai@latest 2>&1 | tail -5
    echo "✓ OpenCode installiert"

    # 8. ssh-mcp installieren
    echo "=== Schritt 8: ssh-mcp installieren ==="
    npm install -g ssh-mcp@2.17.0 2>&1 | tail -5
    echo "✓ ssh-mcp installiert"

    # 9. ssh-mcp Konfiguration erstellen
    echo "=== Schritt 9: ssh-mcp Konfiguration ==="
    cat > ~/.config/ssh-mcp/config.toml << 'SSHCONFIG'
[defaults]
defaultProfile = "docker"
approvalMode = "ask-destructive"

[[profiles]]
name = "docker"
host = "10.0.10.10"
port = 22
user = "root"
auth = "key"
keyRef = "~/.ssh/id_ed25519_agent"
role = "admin"
approvalPolicy = "auto"
SSHCONFIG
    chmod 700 ~/.config/ssh-mcp
    chmod 600 ~/.config/ssh-mcp/config.toml
    echo "✓ ssh-mcp konfiguriert"

    # 10. startup.sh ausführbar machen und Secrets aus OpenBao holen
    echo "=== Schritt 10: Secrets aus OpenBao laden ==="
    chmod +x "$REPO_DIR/coder-templates/infrastructure/startup.sh"
    bash "$REPO_DIR/coder-templates/infrastructure/startup.sh" 2>&1
    echo "✓ Secrets geladen"

    # 11. Herdr Server starten und Workspaces erstellen
    echo "=== Schritt 11: Herdr Setup ==="
    export PATH="/root/.local/bin:$PATH"
    
    # Start Herdr server in background
    if ! pgrep -f "herdr serve" > /dev/null; then
      nohup herdr serve > /tmp/herdr.log 2>&1 &
      sleep 3
      if pgrep -f "herdr serve" > /dev/null; then
        echo "  ✓ Herdr Server gestartet"
      else
        echo "  ⚠ Herdr Server Start fehlgeschlagen"
      fi
    else
      echo "  ✓ Herdr Server läuft bereits"
    fi
    
    # Create Herdr workspaces
    echo "  Erstelle Herdr Workspaces..."
    herdr workspace create --label "Home Assistant" --directory "/home/${data.coder_workspace_owner.me.name}/opencode-coder-env/workspaces/infrastructure/homeassistant" 2>&1 || echo "  Workspace existiert bereits"
    herdr workspace create --label "Server Management" --directory "/home/${data.coder_workspace_owner.me.name}/opencode-coder-env/workspaces/infrastructure/server-management" 2>&1 || echo "  Workspace existiert bereits"
    echo "  ✓ Herdr Workspaces erstellt"

    # 12. Alias für Update des Config-Repos
    echo 'alias update-opencode-config="cd ~/opencode-coder-env && git pull --ff-only"' >> ~/.bashrc

    echo ""
    echo "=== infrastructure Workspace bereit ==="
    echo "  Herdr UI:       Öffne die 'Infrastructure Workspace' App"
    echo "  Workspaces:     Home Assistant, Server Management"
    echo "  Config-Update:  update-opencode-config"
  EOT
}

# ─── Coder Apps (Shortcuts in der UI) ────────────────────────────────────────

resource "coder_app" "herdr" {
  agent_id     = coder_agent.main.id
  slug         = "herdr"
  display_name = "Infrastructure Workspace"
  command      = "herdr"
  icon         = "/icon/terminal.svg"
  share        = "owner"
}
