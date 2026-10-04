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

# 4. Skills symlinken (optional, wenn global gewünscht)
# for skill in ~/opencode-coder-env/skills/homelab/*/; do
#   skill_name=$(basename $skill)
#   ln -sf ~/opencode-coder-env/skills/homelab/$skill_name/SKILL.md ~/.config/opencode/skills/$skill_name/SKILL.md
# done

# 5. Herdr Integration installieren
if command -v herdr &>/dev/null; then
  herdr integration install opencode 2>/dev/null || echo "WARN: Herdr Integration fehlgeschlagen"
fi

# 6. Alias für Updates
echo 'alias update-config="cd ~/opencode-coder-env && git pull"' >> ~/.bashrc

echo "=== homelab Workspace gestartet ==="
echo "OpenCode aus ~/opencode-coder-env/workspaces/homelab/homeassistant oder server-management starten"
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
