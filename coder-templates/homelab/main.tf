# Coder Template: homelab
# OpenCode + Herdr Infrastruktur-Workspace

variable "git_url" {
  default = "https://github.com/DasCarstel/opencode-coder-env.git"
}

resource "coder_workspace" "me" {
  name = "homelab"
}

resource "coder_agent" "main" {
  os   = "linux"
  arch = "amd64"
  
  startup_script = <<EOF
#!/bin/bash
set -euo pipefail

# 1. Repo klonen/syncen
if [ -d ~/opencode-coder-env ]; then
  cd ~/opencode-coder-env
  git pull --ff-only || echo "WARN: git pull fehlgeschlagen, manuell prüfen"
else
  git clone ${var.git_url} ~/opencode-coder-env
fi

# 2. Verzeichnisse erstellen
mkdir -p ~/.config/opencode
mkdir -p ~/.cache/opencode/opencode-model-router

# 3. tiers.json symlinken
ln -sf ~/opencode-coder-env/tiers.json ~/.cache/opencode/opencode-model-router/tiers.json

# 4. Startup-Script ausführbar machen
chmod +x ~/opencode-coder-env/coder-templates/homelab/startup.sh

# 5. Alias für manuellen Start mit OpenBao-Auth
echo 'alias open-ha="~/opencode-coder-env/coder-templates/homelab/startup.sh"' >> ~/.bashrc

echo "=== homelab Workspace gestartet ==="
echo "OpenCode mit HA-Integration: open-ha"
echo "Oder manuell: ~/opencode-coder-env/coder-templates/homelab/startup.sh"
EOF
}

resource "coder_app" "opencode" {
  agent_id     = coder_agent.main.id
  slug         = "opencode"
  display_name = "OpenCode"
  command      = "opencode"
  icon         = "/icon/openai.svg"
  subdomain    = false
  share        = "owner"
}

EOF
