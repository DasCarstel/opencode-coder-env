# Coder Template: general (Allgemeine OpenCode-Aufgaben)
#
# Workspace mit dedizierter, minimal berechtigter OpenBao-AppRole (`mcp-general`).
# Netzwerk: coder-gen (dediziert, kein coder-infra).
# MCP: oCIS (privater Obsidian-Vault) + pdf + ssh-mcp.
# ssh-mcp erhält einen eigenen, exklusiven Key (secret/data/mcp/ssh-mcp-pc) und
# eine config-general.toml mit ausschließlich dem Profil `carsten-pc` — Krefeld-
# und Kleve-Hosts existieren dort nicht (harte Grenze, analog zu config-cleve.toml).
#
# Das komplette Setup liegt in coder-templates/shared/startup.sh (Profil: general).

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
  default     = "https://openbao.mueller-nas.de"
}

# Role-ID + Secret-ID der dedizierten AppRole `mcp-general` werden als
# Coder-Parameter gepflegt (kein UUID im Repo). Die AppRole entsteht über
# scripts/bootstrap-hosts.sh und darf NICHT `mcp-server` sein (die liest alle
# secret/data/mcp/*). Fehlt sie, bricht startup.sh fail-closed ab.
data "coder_parameter" "openbao_role_id" {
  name         = "openbao_role_id"
  display_name = "OpenBao AppRole Role-ID (mcp-general)"
  description  = "Role-ID der AppRole 'mcp-general' (Ausgabe von scripts/bootstrap-hosts.sh)."
  type         = "string"
  default      = ""
  order        = 0
  mutable      = true
}

data "coder_parameter" "openbao_approle_secret_id" {
  name         = "openbao_approle_secret_id"
  display_name = "OpenBao AppRole Secret-ID (mcp-general)"
  description  = "Secret-ID der AppRole 'mcp-general' in OpenBao."
  type         = "string"
  default      = ""
  order        = 1
  mutable      = true
}

# ─── Docker Workspace Container ────────────────────────────────────────────

resource "docker_image" "workspace" {
  name = "workspace-general:v2"

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
    "OPENBAO_ROLE_ID=${data.coder_parameter.openbao_role_id.value}",
  ]

  # Coder-Agent-Bootstrap (offizielles init_script) als Payload nach dem ENTRYPOINT-Wrapper
  command = ["sh", "-c", coder_agent.main.init_script]

  cpu_shares = 2048
  memory     = 2048
  shm_size   = 512

  # Isoliertes Netzwerk (kein coder-infra → kein internes VLAN)
  networks_advanced {
    name = "coder-gen"
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

  # Persistenter Ordner für OpenCode-Sessions/Chats (eigener Pfad, nicht geteilt)
  volumes {
    host_path      = "/etc/opencode-data/${data.coder_workspace_owner.me.name}-general"
    container_path = "/root/.local/share/opencode"
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
  display_name = "General Workspace"
  command      = "/usr/local/bin/herdr"
  icon         = "/icon/terminal.svg"
  share        = "owner"
}
