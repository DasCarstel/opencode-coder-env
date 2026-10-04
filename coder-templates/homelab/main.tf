# Coder Template: homelab (OpenCode-Infrastruktur)
#
# Dieses Template provisioniert einen Workspace für OpenCode-Entwicklung.
# Infrastruktur-spezifische Teile (Docker/Proxmox) müssen an die eigene Umgebung angepasst werden.

terraform {
  required_providers {
    coder = {
      source = "coder/coder"
    }
    # TODO: Infra-Provider hinzufügen (z.B. docker, proxmox, kubernetes)
    # docker = { source = "kreuzwerker/docker" }
  }
}

# Workspace- und Owner-Daten von Coder
data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}

# ─── Template-Parameter ──────────────────────────────────────────────────────

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

# ─── Compute-Resource ────────────────────────────────────────────────────────
# TODO: An die eigene Infrastruktur anpassen.
#
# Beispiel Docker:
#   resource "docker_container" "workspace" {
#     count = data.coder_workspace.me.start_count
#     image = "ubuntu:22.04"
#     name  = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}"
#     env   = ["CODER_AGENT_TOKEN=${coder_agent.main.token}"]
#     command = ["sh", "-c", coder_agent.main.init_script]
#   }
#
# Beispiel Proxmox:
#   resource "proxmox_vm_qemu" "workspace" { ... }
#
# Beispiel Kubernetes:
#   resource "kubernetes_pod" "workspace" { ... }

# ─── Coder Agent ─────────────────────────────────────────────────────────────

resource "coder_agent" "main" {
  os   = "linux"
  arch = "amd64"

  # Der init_script wird vom Coder-Provisioner in das Workspace-Image injiziert
  dir = "/home/${data.coder_workspace_owner.me.name}"

  env = {
    # HA_LLA_TOKEN wird als Coder Secret gesetzt und automatisch injiziert
    # (kein hardcoded Wert im Template!)
  }

  startup_script = <<-EOT
    #!/bin/bash
    set -euo pipefail

    echo "=== homelab Workspace: Repository wird synchronisiert ==="

    # Repo klonen oder aktualisieren
    REPO_DIR="/home/${data.coder_workspace_owner.me.name}/opencode-coder-env"
    if [ -d "$REPO_DIR/.git" ]; then
      cd "$REPO_DIR"
      git fetch origin ${var.git_branch}
      git reset --hard origin/${var.git_branch}
    else
      git clone --branch ${var.git_branch} --single-branch "${var.git_repo_url}" "$REPO_DIR"
    fi

    # Verzeichnisse anlegen
    mkdir -p ~/.config/opencode
    mkdir -p ~/.cache/opencode/opencode-model-router

    # tiers.json für Model-Router verlinken
    ln -sf "$REPO_DIR/tiers.json" ~/.cache/opencode/opencode-model-router/tiers.json

    # Skills verlinken (jeder Skill einzeln, damit keine unerwünschten Skills kommen)
    for skill_dir in "$REPO_DIR/skills/homelab"/*/; do
      [ -d "$skill_dir" ] || continue
      skill_name=$(basename "$skill_dir")
      mkdir -p ~/.config/opencode/skills/"$skill_name"
      ln -sf "$skill_dir/SKILL.md" ~/.config/opencode/skills/"$skill_name"/SKILL.md
    done

    # Alias für Update des Config-Repos
    echo 'alias update-opencode-config="cd ~/opencode-coder-env && git pull --ff-only"' >> ~/.bashrc 2>/dev/null || true

    echo "=== homelab Workspace bereit ==="
    echo "  HA-Projekt:     cd ~/opencode-coder-env/workspaces/homelab/homeassistant && opencode"
    echo "  SSH-Projekt:    cd ~/opencode-coder-env/workspaces/homelab/server-management && opencode"
  EOT
}

# ─── Coder App (optionaler Shortcut in der UI) ───────────────────────────────

resource "coder_app" "opencode-ha" {
  agent_id     = coder_agent.main.id
  slug         = "opencode-ha"
  display_name = "OpenCode: Home Assistant"
  command      = "opencode"
  icon         = "/icon/openai.svg"
  subdomain    = false
  share        = "owner"
  # Startet im homeassistant-Verzeichnis, damit die dortige opencode.json greift
  # (Coder unterstützt cwd nicht direkt – Workspace muss ins richtige Verzeichnis wechseln)
}
