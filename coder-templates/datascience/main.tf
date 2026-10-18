# Coder Template: datascience (FernUni Data Engineering, Docker-in-Docker)
#
# Workspace für das FernUni-Modul „Data Engineering für Data Science“:
#   * privilegierter Docker-in-Docker-Daemon im Container (KEIN Host-Socket!)
#     – die Studierenden starten im Workspace ./Deploy-DeDs-*.sh, was per
#       docker compose einen Stack (Jupyter/PySpark-Client + Hadoop-Edge +
#       2 Worker) hochzieht, und zusätzlich alpine-/mongodb-Container.
#   * Persistenz: Repo und /var/lib/docker als Docker-Volumes (Images
#     überleben Coder-„Stop“/„Restart“ – ein Template-Push erzeugt den
#     Container neu); ~/uni dagegen als HOST-BIND-MOUNT auf
#     /etc/coder/uni-data. Der Host-Ordner steht außerhalb jeder Terraform-
#     Ressource und überlebt damit auch das Löschen und Neuanlegen des
#     Workspace. Repo-Volume und /var/lib/docker bleiben bewusst pro
#     Workspace (Begründung unten an den Volume-Ressourcen).
#   * eigene, minimal berechtigte AppRole `mcp-uni` (fail-closed in startup.sh).
#   * Ports: 8890 = unser JupyterLab (ML/DL-Kernel),
#            8888 = Jupyter des FernUni-Compose-Stacks,
#            8891 = On-Demand-Tor (fernuni-gate.py) – NUR darauf zeigt die
#                   Coder-App `fernuni-jupyter`; es startet den Stack auf 8888
#                   beim ersten Zugriff und stoppt ihn nach 3 h Leerlauf,
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
      version = "~> 2.18"
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

# ── ~/uni: HOST-BIND-MOUNT auf /etc/coder/uni-data ─────────────────────────
# Der Uni-Fortschritt (Moodle-Material, Modul-Repos, uv-Umgebungen) muss das
# Löschen und Neuanlegen des Workspace überleben. Er liegt deshalb NICHT in
# einem Docker-Volume, sondern in einem echten Ordner auf dem Coder-Host:
#     /etc/coder/uni-data
# Dieser Pfad ist KEINE Terraform-Ressource. Terraform erzeugt, verwaltet und
# löscht ihn nie – weder bei `apply` noch bei `destroy`. Ein `coder delete`
# entfernt nur Container + die workspace-eigenen Volumes und lässt den Ordner
# unangetastet; ein neu angelegter Workspace mountet denselben Stand wieder
# ein (kein zweiter Voll-Sync, kein erneuter Image-Download, keine leeren
# Modul-Repos).
#
# ⚠ EINE bewusste Kopplung: ALLE `datascience`-Workspaces eines Owners teilen
#   sich denselben Ordner und damit dasselbe ~/uni. Zwei gleichzeitig laufende
#   Workspaces schreiben also in denselben Studienstand. Das ist gewollt – der
#   Nutzer betreibt nur einen Workspace gleichzeitig. Ein früherer Entwurf mit
#   einem festen Docker-Volume (`coder-uni-<owner>-data`) plus
#   `lifecycle.prevent_destroy` ist genau daran gescheitert: das Volume
#   erreichte zwar „Daten überleben“, brach aber jedes `coder delete` hart mit
#   „Instance cannot be destroyed“ ab. Der Host-Ordner leistet dasselbe, ohne
#   den Workspace unzerstörbar zu machen.
#
# Anlegen (einmalig, außerhalb Terraform):
#     install -d -m 0755 /etc/coder/uni-data
# Der Container läuft als uid 0 und schreibt dort direkt.

# /var/lib/docker: Images/Container des inneren Docker-Daemons (~2,8 GiB
# FernUni-Images). Ohne dieses Volume wäre nach jedem Neustart alles weg.
# BEWUSST pro Workspace (`…-docker`): zwei gleichzeitig laufende Workspaces
# mit demselben data-root würden sich gegenseitig die Images/Container
# zerstören (gemeinsamer Image-Store, gleiche Container-Namen, Port-Belegung).
# Preis der Trennung: ein NEUER Workspace zieht die FernUni-Images einmal neu
# (~2,8 GiB, registry.dbis-pro1.fernuni-hagen.de) – reproduzierbar, kein
# Datenverlust. Deshalb wird dieses Volume nicht „fest“ gemacht.
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

  # Gemeinsamer npm-Cache (Host-Bind): alle Workspaces nutzen denselben warmen
  # Download-Cache für Nachinstallationen bei veraltetem Bake. Liegt außerhalb
  # von Terraform (/etc/npm-cache), überlebt destroy und ersetzt den früheren
  # ~1 GB npm-Cache je Image (der liegt jetzt im BuildKit-Cache des Builds).
  volumes {
    host_path      = "/etc/npm-cache"
    container_path = "/root/.npm"
  }

  # Persistentes Volume für Repo + Workspace-Daten (überlebt Container-Neuerstellung)
  volumes {
    volume_name    = docker_volume.workspace_data.name
    container_path = "/home/${data.coder_workspace_owner.me.name}/opencode-coder-env"
  }

  # FernUni-Arbeit (~/uni) als HOST-BIND-MOUNT: /etc/coder/uni-data liegt
  # außerhalb jeder Terraform-Ressource, wird beim `destroy` nie angefasst und
  # überlebt damit das Löschen + Neuanlegen des Workspace. Alle
  # `datascience`-Workspaces des Owners teilen sich diesen Ordner (gewollt,
  # Begründung im Kommentar oben an den Volume-Ressourcen).
  volumes {
    host_path      = "/etc/coder/uni-data"
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
  # Bewusst kein `dir`: Standard ist $HOME des Workspace-Users (= /home/<owner>).
  # Explizites `dir` ist im Coder-Provider abgekündigt ("dir" is deprecated) und
  # bricht bei Werten != $HOME den Datei-Sync der Coder-Desktop-App.
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
# sein eigenes Jupyter auf 8888 mitbringt.
# `url = http://127.0.0.1:<port>` lässt den Coder-Agent den Port im Workspace
# proxyen (kein `port`-Argument im coder-Provider). subdomain = true, weil
# JupyterLab/Hadoop absolute Pfade ausliefern (Wildcard-URL ist gesetzt).
#
# Bewusst 127.0.0.1 statt `localhost`: in diesem Workspace löst `localhost`
# zuerst auf ::1 (IPv6) auf, die Listener hier sind aber reine IPv4-Bindungen
# (JupyterLab 0.0.0.0:8890; FernUni-Compose veröffentlicht 8888 auf IPv4).
# Der Agent erreicht IPv4 zwar auch über `localhost` (er probiert 127.0.0.1
# und [::1] durch), die explizite IPv4-Adresse vermeidet die IPv6-Umleitung
# aber von vornherein – u. a. für Clients, die ::1 zuerst versuchen.
resource "coder_app" "jupyter" {
  agent_id     = coder_agent.main.id
  slug         = "jupyter"
  display_name = "JupyterLab (ML/DL)"
  url          = "http://127.0.0.1:8890"
  subdomain    = true
  icon         = "/icon/terminal.svg"
  share        = "owner"
}

# Jupyter des FernUni-Compose-Stacks (erst nach ./Deploy-DeDs-*.sh erreichbar !).
# Der Stack veröffentlicht 8888 auf IPv4 (docker compose `- 8888:8888`, im
# Container `--ip 0.0.0.0`) – deshalb hier ebenfalls explizit 127.0.0.1 und
# nicht `localhost` (siehe Begründung bei coder_app.jupyter).
#
# ── On-Demand-Tor (2026-10-16) ───────────────────────────────────────────
# Die App zeigt NICHT mehr auf 8888, sondern auf das Tor
#      8891 → 8888  (coder-templates/datascience/fernuni-gate.py)
# Das Tor ist ein roher TCP-Proxy (stdlib/asyncio) und versteht deshalb auch
# die JupyterLab-WebSockets:
#   * Beim ersten Zugriff lauscht 8888 nicht → das Tor startet selbst
#     `env UID=1000 GID=1000 docker compose -f docker-compose.yml up -d
#     client-node` im Repo ~/uni/data-engineering-infrastructure und liefert
#     währenddessen eine HTTP-503-Seite mit Auto-Refresh („Beim ersten Mal
#     dauert es ein bis zwei Minuten“) aus. Eine Sperre verhindert, dass jede
#     der vielen Browser-Anfragen einer JupyterLab-Seite einen eigenen Start
#     auslöst.
#   * Nach 3 h ohne Zugriff (--idle-seconds / FERNUNI_GATE_IDLE_SECONDS)
#     stoppt es den Stack (`docker compose stop client-node`) – aber NUR,
#     wenn es ihn selbst gestartet hat. Einen per `deds candle` im Terminal
#     gestarteten Stack proxyt es nur und fasst ihn nie an.
#   * Log: ~/uni/logs/fernuni-gate.log (eine Zeile je Ereignis).
#   * Gestartet wird das Tor idempotent von uni-bootstrap.sh, es übersteht
#     damit einen Workspace-Neustart.
# Der Stack selbst bleibt UNVERÄNDERT auf Port 8888: kein Port-Remapping,
# `deds candle`, docker-compose.yml und die Kursanleitung funktionieren
# weiterhin genau wie gedruckt. Nur die Coder-App-URL hat sich geändert.
resource "coder_app" "fernuni_jupyter" {
  agent_id     = coder_agent.main.id
  slug         = "fernuni-jupyter"
  display_name = "FernUni Jupyter (Stack)"
  url          = "http://127.0.0.1:8891"
  subdomain    = true
  icon         = "/icon/terminal.svg"
  share        = "owner"
}

# Hadoop ResourceManager des FernUni-Stacks (Web-UI, IPv4 wie oben).
resource "coder_app" "hadoop_rm" {
  agent_id     = coder_agent.main.id
  slug         = "hadoop-rm"
  display_name = "Hadoop ResourceManager"
  url          = "http://127.0.0.1:8088"
  subdomain    = true
  icon         = "/icon/terminal.svg"
  share        = "owner"
}
