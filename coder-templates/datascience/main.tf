# Coder Template: datascience (FernUni Data Engineering, Docker-in-Docker)
#
# Workspace für das FernUni-Modul „Data Engineering für Data Science“:
#   * privilegierter Docker-in-Docker-Daemon im Container (KEIN Host-Socket!)
#     – die Studierenden starten im Workspace ./Deploy-DeDs-*.sh, was per
#       docker compose einen Stack (Jupyter/PySpark-Client + Hadoop-Edge +
#       2 Worker) hochzieht, und zusätzlich alpine-/mongodb-Container.
#   * Persistente Volumes: Repo, ~/uni und /var/lib/docker (Images überleben
#     Coder-„Stop“/„Restart“ – ein Template-Push erzeugt den Container neu).
#   * eigene, minimal berechtigte AppRole `mcp-uni` (fail-closed in startup.sh).
#   * Ports: 8890 = unser JupyterLab (ML/DL-Kernel),
#            8888 = Jupyter des FernUni-Compose-Stacks,
#            8088 = Hadoop ResourceManager
#            (8888/8088 erst erreichbar, nachdem der Student den Stack gestartet hat).
#
# Setup-Kette: entrypoint.sh (Repo) → /usr/local/bin/uni-bootstrap.sh
# (dockerd-Treiber-Probe, ~/uni-Struktur, JupyterLab) →
# coder-templates/shared/startup.sh (Profil: datascience).

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

# Role-ID + Secret-ID der dedizierten AppRole `mcp-uni` werden als
# Coder-Parameter gepflegt (kein UUID im Repo). Die AppRole darf NICHT
# `mcp-server` sein (die liest alle secret/data/mcp/*). Fehlt sie, bricht
# startup.sh fail-closed ab.
data "coder_parameter" "openbao_role_id" {
  name         = "openbao_role_id"
  display_name = "OpenBao AppRole Role-ID (mcp-uni)"
  description  = "Role-ID der AppRole 'mcp-uni' (Ausgabe von scripts/bootstrap-hosts.sh)."
  type         = "string"
  default      = ""
  order        = 0
  mutable      = true
}

data "coder_parameter" "openbao_approle_secret_id" {
  name         = "openbao_approle_secret_id"
  display_name = "OpenBao AppRole Secret-ID (mcp-uni)"
  description  = "Secret-ID der AppRole 'mcp-uni' in OpenBao."
  type         = "string"
  default      = ""
  order        = 1
  mutable      = true
}

# ─── Docker Workspace Container ────────────────────────────────────────────

resource "docker_image" "workspace" {
  name = "workspace-datascience:v2"

  build {
    context    = path.module
    dockerfile = "Dockerfile"
    build_args = {
      CACHE_DATE = timestamp()
    }
  }
}

# Repo + Persistenz: alles, was einen Container-Neubau überleben muss.
resource "docker_volume" "workspace_data" {
  name = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}-data"
}

# ~/uni: FernUni-Klon, workspace/-Bind-Mount-Ziel, Module, Material, envs, state.
resource "docker_volume" "workspace_uni" {
  name = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}-uni"
}

# /var/lib/docker: Images/Container des inneren Docker-Daemons (~2,8 GiB
# FernUni-Images). Ohne dieses Volume wäre nach jedem Neustart alles weg.
resource "docker_volume" "workspace_docker" {
  name = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}-docker"
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
    # Ownername für entrypoint.sh/uni-bootstrap.sh/startup.sh (~/uni-Pfade)
    "WORKSPACE_OWNER=${data.coder_workspace_owner.me.name}",
    "WORKSPACE_NAME=${data.coder_workspace.me.name}",
  ]

  # Coder-Agent-Bootstrap (offizielles init_script) als Payload nach dem ENTRYPOINT-Wrapper
  command = ["sh", "-c", coder_agent.main.init_script]

  cpu_shares = 4096
  memory     = 8192
  # MongoDB/Spark brauchen /dev/shm – 512 MB (Default in anderen Templates) reicht nicht.
  shm_size = 2048

  # ── Docker-in-Docker ──────────────────────────────────────────────────────
  # Verifiziert auf dem Coder-Host (CT 120): docker run --privileged alpine lief.
  # Der Host-Socket wird bewusst NICHT gemountet – der Daemon läuft im Container.
  privileged    = true
  cgroupns_mode = "host"
  security_opts = ["apparmor=unconfined", "seccomp=unconfined"]

  # Isoliertes Netzwerk (kein coder-infra → kein internes VLAN)
  networks_advanced {
    name = "coder-ds"
  }

  # Herdr-Binary vom Host einbinden (Fallback, wird bei Bedarf geladen)
  volumes {
    host_path      = "/opt/opencode-bin"
    container_path = "/opt/opencode-bin"
    read_only      = true
  }

  # Persistentes Volume für Repo + Workspace-Daten (überlebt Container-Neuerstellung)
  volumes {
    volume_name    = docker_volume.workspace_data.name
    container_path = "/home/${data.coder_workspace_owner.me.name}/opencode-coder-env"
  }

  # Persistentes Volume für die FernUni-Arbeit (~/uni)
  volumes {
    volume_name    = docker_volume.workspace_uni.name
    container_path = "/home/${data.coder_workspace_owner.me.name}/uni"
  }

  # Persistentes Volume für den inneren Docker-Daemon (Images/Container)
  volumes {
    volume_name    = docker_volume.workspace_docker.name
    container_path = "/var/lib/docker"
  }

  # Persistenter Ordner für OpenCode-Sessions/Chats (eigener Pfad, nicht geteilt)
  volumes {
    host_path      = "/etc/opencode-data/${data.coder_workspace_owner.me.name}-datascience"
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
  display_name = "Data Science Workspace"
  command      = "/usr/local/bin/herdr"
  icon         = "/icon/terminal.svg"
  share        = "owner"
}

# Unser JupyterLab (ML/DL-Kernel) – bewusst auf 8890, weil der FernUni-Stack
# sein eigenes Jupyter auf 127.0.0.1:8888 mitbringt.
# `url = http://localhost:<port>` lässt den Coder-Agent den Port im Workspace
# proxyen (kein `port`-Argument im coder-Provider). subdomain = true, weil
# JupyterLab/Hadoop absolute Pfade ausliefern (Wildcard-URL ist gesetzt).
resource "coder_app" "jupyter" {
  agent_id     = coder_agent.main.id
  slug         = "jupyter"
  display_name = "JupyterLab (ML/DL)"
  url          = "http://localhost:8890"
  subdomain    = true
  icon         = "/icon/terminal.svg"
  share        = "owner"
}

# Jupyter des FernUni-Compose-Stacks (erst nach ./Deploy-DeDs-*.sh erreichbar).
resource "coder_app" "fernuni_jupyter" {
  agent_id     = coder_agent.main.id
  slug         = "fernuni-jupyter"
  display_name = "FernUni Jupyter (Stack)"
  url          = "http://localhost:8888"
  subdomain    = true
  icon         = "/icon/terminal.svg"
  share        = "owner"
}

# Hadoop ResourceManager des FernUni-Stacks (Web-UI).
resource "coder_app" "hadoop_rm" {
  agent_id     = coder_agent.main.id
  slug         = "hadoop-rm"
  display_name = "Hadoop ResourceManager"
  url          = "http://localhost:8088"
  subdomain    = true
  icon         = "/icon/terminal.svg"
  share        = "owner"
}
