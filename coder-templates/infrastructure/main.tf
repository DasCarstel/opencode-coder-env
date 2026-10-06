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
  default      = "e2d9db8f-d810-f11f-2ce0-5caf93f2a64e"
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

resource "docker_volume" "workspace_data" {
  name = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}-data"
}

resource "docker_container" "workspace" {
  count = data.coder_workspace.me.start_count

  image   = docker_image.workspace.image_id
  name    = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}"
  restart = "unless-stopped"

  env = [
    "CODER_AGENT_TOKEN=${coder_agent.main.token}",
    "OPENBAO_APPROLE_SECRET_ID=${data.coder_parameter.openbao_approle_secret_id.value}",
    "OPENBAO_ADDR=https://openbao.mueller-nas.de",
    "OPENBAO_ROLE_ID=${var.openbao_role_id}",
  ]

  command = ["sh", "-c", coder_agent.main.init_script]

  cpu_shares = 2048
  memory     = 2048
  shm_size   = 512

  networks_advanced {
    name = "coder-infra"
  }

  # Herdr-Binary vom Host einbinden (Fallback, wird bei Bedarf geladen)
  volumes {
    host_path      = "/opt/opencode-bin"
    container_path = "/opt/opencode-bin"
    read_only      = true
  }

  # Persistentes Volume für Workspace-Daten (überlebt Container-Neuerstellung)
  volumes {
    volume_name    = docker_volume.workspace_data.name
    container_path = "/home/${data.coder_workspace_owner.me.name}/opencode-coder-env"
  }

  # Persistenter Ordner für OpenCode-Sessions/Chats (auf dem Coder-Host unter /etc)
  volumes {
    host_path      = "/etc/opencode-data/${data.coder_workspace_owner.me.name}"
    container_path = "/root/.local/share/opencode"
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

    # 1. Grundlegende Tools
    export DEBIAN_FRONTEND=noninteractive
    apt-get update && apt-get install -y \
      curl git ca-certificates gnupg python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

    # 2. Node.js v22
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y nodejs

    # 3. Repo aus dem Docker-Image kopieren
    REPO_DIR="/home/${data.coder_workspace_owner.me.name}/opencode-coder-env"
    mkdir -p "/home/${data.coder_workspace_owner.me.name}"
    rm -rf "$REPO_DIR"
    cp -r /repo "$REPO_DIR"

    # 4. Verzeichnisse + Skills verlinken
    mkdir -p ~/.config/opencode ~/.config/ssh-mcp ~/.cache/opencode/opencode-model-router
    [ -f "$REPO_DIR/tiers.json" ] && ln -sf "$REPO_DIR/tiers.json" ~/.cache/opencode/opencode-model-router/tiers.json || true
    for skill_dir in "$REPO_DIR/skills/infrastructure"/*/; do
      [ -d "$skill_dir" ] || continue
      skill_name=$(basename "$skill_dir")
      mkdir -p ~/.config/opencode/skills/"$skill_name"
      ln -sf "$skill_dir/SKILL.md" ~/.config/opencode/skills/"$skill_name"/SKILL.md
    done

    # 5. ssh-mcp installieren
    npm install -g ssh-mcp@2.17.0 2>&1 | tail -2 || true

    # 6. Alias
    echo 'alias update-opencode-config="cd ~/opencode-coder-env && git pull --ff-only"' >> ~/.bashrc

    # 7. Komplettes Setup (Secrets, SSH-CA, Herdr, OpenCode, MCP)
    chmod +x "$REPO_DIR/coder-templates/infrastructure/startup.sh"
    bash "$REPO_DIR/coder-templates/infrastructure/startup.sh" 2>&1 || echo "⚠ setup mit Fehlern beendet"

    echo ""
    echo "=== infrastructure Workspace bereit ==="
  EOT
}

# ─── Coder Apps (Shortcuts in der UI) ────────────────────────────────────────

resource "coder_app" "herdr" {
  agent_id     = coder_agent.main.id
  slug         = "herdr"
  display_name = "Infrastructure Workspace"
  command      = "/root/.local/bin/herdr"
  icon         = "/icon/terminal.svg"
  share        = "owner"
}
