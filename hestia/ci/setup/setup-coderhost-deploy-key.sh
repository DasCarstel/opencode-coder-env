#!/usr/bin/env bash
# setup-coderhost-deploy-key.sh — SSH-Deploy-Key für Hestia-Deploys anlegen
# (auf dem Docker-Host ausführen; Spieler via ssh-mcp privileged-command).
#
# Erstellt einen ed25519-Key, hinterlegt den Public-Key beim Coder-Host (CT 120)
# und sysft beim GitHub-Runner (falls vorhanden) vor.
set -euo pipefail

RUNNER_DIR="${GH_RUNNER_DIR:-/opt/docker-compose/github-runner}"
KEY_PATH="$RUNNER_DIR/coderdeploy_key"
CODER_HOST="${CODER_HOST:-10.0.10.17}"

mkdir -p "$RUNNER_DIR"
if [ ! -f "$KEY_PATH" ]; then
  ssh-keygen -t ed25519 -f "$KEY_PATH" -N "" -C "hestia-deploy-runner"
fi

echo "→ Public-Key freischalten auf dem Coder-Host (erlauben mit sudo/Root-Zugang):"
echo "    ssh root@$CODER_HOST 'mkdir -p ~/.ssh && echo \"$(cat $KEY_PATH.pub)\" >> ~/.ssh/authorized_keys'"
echo ""
echo "→ Privaten Schlüssel als GitHub-Secret 'CODER_DEPLOY_KEY' hinterlegen (Inhalt von $KEY_PATH)."
echo ""
echo "Beispielsbefehl:"
echo '  ba -e "cat key pub" | ssh root@10.0.10.17 "cat - >> ~/.ssh/authorized_keys"'
