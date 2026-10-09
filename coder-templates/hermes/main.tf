# Coder Template: hermes (Hermes CLI MVP)
#
# Provisions einen Docker-Container als interaktiven Hermes-CLI-Workspace.
# Nur coder-infra; keine docker.sock, keine SSH/Tailscale, keine Host-Ports.

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

data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}
data "coder_provisioner" "me" {}

variable "openbao_role_id" {
  type        = string
  description = "OpenBao AppRole Role-ID für hermes-coder"
  default     = "7e551652-0f76-145c-284f-35cef083d927"
}

variable "openbao_addr" {
  type        = string
  description = "OpenBao Adresse (wird vom Workspace aus erreicht)"
  default     = "https://openbao.mueller-nas.de"
}

data "coder_parameter" "openbao_approle_secret_id" {
  name         = "openbao_approle_secret_id"
  display_name = "OpenBao AppRole Secret-ID"
  description  = "Secret-ID für die AppRole 'hermes-coder' in OpenBao."
  type         = "string"
  default      = ""
  order        = 1
  mutable      = true
}

resource "docker_image" "hermes" {
  name = "hermes-cli:latest"

  build {
    context    = "${path.module}"
    dockerfile = "Dockerfile"
    build_args = {
      CACHE_DATE = timestamp()
    }
  }
}

resource "docker_volume" "hermes_state" {
  name = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}-hermes-state"
}

resource "docker_container" "hermes" {
  count = data.coder_workspace.me.start_count

  image = docker_image.hermes.image_id
  name  = "coder-${data.coder_workspace_owner.me.name}-${data.coder_workspace.me.name}"

  command = ["/usr/local/bin/entrypoint.sh"]

  volumes {
    container_path = "/root/.hermes"
    volume_name    = docker_volume.hermes_state.name
    read_only      = false
  }

  env = [
    "OPENBAO_ADDR=${var.openbao_addr}",
    "OPENBAO_ROLE_ID=${var.openbao_role_id}",
    "OPENBAO_SECRET_ID=${data.coder_parameter.openbao_approle_secret_id.value}",
  ]

  memory = 2048

  networks_advanced {
    name = "coder-infra"
  }

  capabilities {
    add = []
  }

  # Kein docker.sock, keine Host-Ports, keine privileged Mode
  privileged = false
}
