# Coder Template: infrastructure (OpenCode-Infrastruktur)
#
# Provisions einen Docker-Container als Workspace für OpenCode-Entwicklung.
# Secrets werden zur Laufzeit aus OpenBao via AppRole-Auth bezogen.
#
# Das komplette Setup (OpenBao, MCP, Herdr, OpenCode) liegt im gemeinsamen
# Skript coder-templates/shared/startup.sh (Profil: infrastructure).

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

# Die Role-ID ist KEIN Secret (vergleichbar einem Benutzernamen) und darf im
# Repo stehen. Die zugehörige Secret-ID kommt als Workspace-Parameter und steht
# NIEMALS im Repo (siehe unten / OPENBAO.md).
#
# ⚠️ Wird eine AppRole in OpenBao neu angelegt, ändert sich ihre Role-ID. Dann
# müssen dieser Default UND die Workspace-Parameter nachgezogen werden, sonst
# scheitert der AppRole-Login in startup.sh mit "invalid role or secret ID"
# (beobachtet am 2026-10-18: alle vier Workspaces standen still, weil die IDs
# nach einem Neu-Anlegen der Rollen veraltet waren).
data "coder_parameter" "openbao_role_id" {
  name         = "openbao_role_id"
  display_name = "OpenBao AppRole Role-ID (mcp-server)"
  description  = "Role-ID der AppRole 'mcp-server' in OpenBao (Ausgabe von scripts/bootstrap-hosts.sh)."
  type         = "string"
  default      = "b55cb212-c69d-379d-6cc7-3409637b0692"
  order        = 0
  mutable      = true
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
  # Bewusst leer: die Secret-ID ist ein Secret und wird pro Workspace gesetzt
  # (Coder-UI → Workspace → Settings → Secrets oder
  #  coder update <workspace> --parameter openbao_approle_secret_id=…).
  # Fehlt sie, bricht startup.sh fail-closed ab.
  default      = ""
  order        = 1
  mutable      = true
}

# ─── Docker Workspace Container ────────────────────────────────────────────

resource "docker_image" "workspace" {
  name = "workspace-infrastructure:v2"

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

# Persistenter Tailscale-State (Node-Identität überlebt Container-Neuerstellung)
resource "docker_volume" "tailscale_state" {
  name = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}-tailscale"
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
    "OPENBAO_ROLE_ID=${data.coder_parameter.openbao_role_id.value}",
  ]

  # Coder-Agent-Bootstrap (offizielles init_script) als Payload nach dem ENTRYPOINT-Wrapper
  command = ["sh", "-c", coder_agent.main.init_script]

  cpu_shares = 2048
  # 4 GB statt 2 GB: mit 2 GB hat der OpenCode-Server im Infrastructure-Workspace
  # regelmaessig OOM-Kills kassiert (cgroup memory.events oom_kill 48, Container
  # bei 1.78/2.0 GiB) — Folge waren abgebrochene Sessions. Das Limit ist keine
  # Reservierung; der Host (8 GB) alloziert nur den tatsaechlichen Verbrauch.
  memory     = 4096
  shm_size   = 512

  networks_advanced {
    name = "coder-infra"
  }

  # Tailscale: NET_ADMIN + TUN device für den VPN-Tunnel
  capabilities {
    add = ["NET_ADMIN"]
  }

  devices {
    host_path      = "/dev/net/tun"
    container_path = "/dev/net/tun"
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

  # Tailscale-State (Node-Identität überlebt Container-Neuerstellung)
  volumes {
    volume_name    = docker_volume.tailscale_state.name
    container_path = "/var/lib/tailscale"
  }
}

# ── Coder Agent ─────────────────────────────────────────────────────────────

resource "coder_agent" "main" {
  os   = "linux"
  arch = data.coder_provisioner.me.arch

  dir = "/home/${data.coder_workspace_owner.me.name}"
}

# ─── Coder Apps (Shortcuts in der UI) ────────────────────────────────────────

resource "coder_app" "herdr" {
  agent_id     = coder_agent.main.id
  slug         = "herdr"
  display_name = "Infrastructure Workspace"
  command      = "/usr/local/bin/herdr"
  icon         = "/icon/terminal.svg"
  share        = "owner"
}
