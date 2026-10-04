# Coder Template: homelab (OpenCode-Infrastruktur)
#
# Dieses Template provisioniert einen Docker-Container als Workspace für OpenCode-Entwicklung.

terraform {
  required_providers {
    coder = {
      source  = "coder/coder"
      version = ">= 1.0"
    }
  }
}

# ─── Workspace-Daten ────────────────────────────────────────────────────────

data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}

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

# ── Docker Workspace Container (offizielles Coder-Modul) ────────────────────

module "docker-container" {
  source          = "registry.coder.com/modules/docker-container/coder"
  agent_id        = coder_agent.main.id
  container_name  = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}"
  cpu             = 2
  memory          = 2048
  disk            = 20
  image           = "ubuntu:22.04"
  run_command     = "sh -c '${coder_agent.main.init_script}'"
}

# ─── Coder Agent ─────────────────────────────────────────────────────────────

resource "coder_agent" "main" {
  os   = "linux"
  arch = "amd64"

  dir = "/home/${data.coder_workspace_owner.me.name}"

  startup_script = <<-EOT
    #!/bin/bash
    set -euo pipefail

    echo "=== homelab Workspace: Initialisierung ==="

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
    for skill_dir in "$REPO_DIR/skills/homelab"/*/; do
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

    # 10. Alias für Update des Config-Repos
    echo 'alias update-opencode-config="cd ~/opencode-coder-env && git pull --ff-only"' >> ~/.bashrc

    echo "=== homelab Workspace bereit ==="
    echo "  HA-Projekt:     cd ~/opencode-coder-env/workspaces/homelab/homeassistant && opencode"
    echo "  SSH-Projekt:    cd ~/opencode-coder-env/workspaces/homelab/server-management && opencode"
  EOT
}

# ─── Coder Apps (Shortcuts in der UI) ────────────────────────────────────────

resource "coder_app" "opencode-ha" {
  agent_id     = coder_agent.main.id
  slug         = "opencode-ha"
  display_name = "OpenCode: Home Assistant"
  command      = "opencode"
  icon         = "/icon/openai.svg"
  subdomain    = false
  share        = "owner"
}

resource "coder_app" "opencode-ssh" {
  agent_id     = coder_agent.main.id
  slug         = "opencode-ssh"
  display_name = "OpenCode: Server Management"
  command      = "opencode"
  icon         = "/icon/terminal.svg"
  subdomain    = false
  share        = "owner"
}
