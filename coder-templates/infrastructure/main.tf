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

variable "openbao_addr" {
  type        = string
  description = "OpenBao Adresse (wird vom Workspace aus erreicht)"
  default     = "http://10.0.10.10:8200"
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
  name = "workspace:latest"
  
  build {
    context    = "${path.module}"
    dockerfile = "Dockerfile"
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

    # 3. Repo klonen oder aktualisieren
    REPO_DIR="/home/${data.coder_workspace_owner.me.name}/opencode-coder-env"
    if [ -d "$REPO_DIR/.git" ]; then
      cd "$REPO_DIR"
      git fetch origin ${var.git_branch}
      git reset --hard origin/${var.git_branch}
    else
      git clone --branch ${var.git_branch} --single-branch "${var.git_repo_url}" "$REPO_DIR"
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

    # 7. OpenCode installieren
    curl -L https://opencode.ai/install.sh | sh

    # 8. ssh-mcp installieren
    npm install -g ssh-mcp@2.17.0

    # 9. ssh-mcp Konfiguration erstellen
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

    # 10. startup.sh ausführbar machen und Secrets aus OpenBao holen
    chmod +x "$REPO_DIR/coder-templates/infrastructure/startup.sh"
    bash "$REPO_DIR/coder-templates/infrastructure/startup.sh"

    # 11. Wrapper-Scripts für OpenCode-Projekte erstellen
    cat > /usr/local/bin/opencode-ha << 'WRAPPER'
#!/bin/bash
[ -f /etc/opencode.env ] && source /etc/opencode.env
cd "/home/${data.coder_workspace_owner.me.name}/opencode-coder-env/workspaces/infrastructure/homeassistant" && exec opencode "$@"
WRAPPER
    chmod +x /usr/local/bin/opencode-ha

    cat > /usr/local/bin/opencode-ssh << 'WRAPPER'
#!/bin/bash
[ -f /etc/opencode.env ] && source /etc/opencode.env
cd "/home/${data.coder_workspace_owner.me.name}/opencode-coder-env/workspaces/infrastructure/server-management" && exec opencode "$@"
WRAPPER
    chmod +x /usr/local/bin/opencode-ssh

    # 12. Alias für Update des Config-Repos
    echo 'alias update-opencode-config="cd ~/opencode-coder-env && git pull --ff-only"' >> ~/.bashrc

    echo ""
    echo "=== infrastructure Workspace bereit ==="
    echo "  HA-Projekt:     opencode-ha"
    echo "  SSH-Projekt:    opencode-ssh"
    echo "  Config-Update:  update-opencode-config"
  EOT
}

# ─── Coder Apps (Shortcuts in der UI) ────────────────────────────────────────

resource "coder_app" "opencode-ha" {
  agent_id     = coder_agent.main.id
  slug         = "opencode-ha"
  display_name = "OpenCode: Home Assistant"
  command      = "opencode-ha"
  icon         = "/icon/openai.svg"
  share        = "owner"
}

resource "coder_app" "opencode-ssh" {
  agent_id     = coder_agent.main.id
  slug         = "opencode-ssh"
  display_name = "OpenCode: Server Management"
  command      = "opencode-ssh"
  icon         = "/icon/terminal.svg"
  share        = "owner"
}
