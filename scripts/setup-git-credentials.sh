#!/bin/bash
# setup-git-credentials.sh – GitHub-Credentials für git aus OpenBao
#
# Liest secret/data/mcp/github (Key api_key) und konfiguriert git so, dass
# private Repos geklont/gepusht werden können. Gilt global im Container und
# damit in allen Workspaces.
#
# Voraussetzung: BAO_TOKEN (aus /etc/opencode.env oder der Umgebung).
# Aufruf z. B. aus startup.sh:
#   bash "$REPO_DIR/scripts/setup-git-credentials.sh"

set -uo pipefail

OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
GIT_NAME="${GIT_USER_NAME:-Carsten Müller}"
GIT_EMAIL="${GIT_USER_EMAIL:-carstenmueller2002@gmail.com}"

# BAO_TOKEN aus /etc/opencode.env laden, falls nicht in der Umgebung
if [ -z "${BAO_TOKEN:-}" ] && [ -f /etc/opencode.env ]; then
  # shellcheck disable=SC1091
  . /etc/opencode.env
fi

if [ -z "${BAO_TOKEN:-}" ]; then
  echo "  ⚠ Git-Credentials: kein BAO_TOKEN – übersprungen"
  exit 0
fi

GITHUB_TOKEN=$(curl -sS --max-time 10 -H "X-Vault-Token: ${BAO_TOKEN}" \
  "${OPENBAO_ADDR}/v1/secret/data/mcp/github" \
  | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['data']['data'].get('api_key', ''))
except Exception:
    print('')
")

if [ -z "$GITHUB_TOKEN" ]; then
  echo "  ⚠ Git-Credentials: GitHub-Token nicht gefunden (secret/data/mcp/github)"
  exit 0
fi

# Git-Credential-Store (0600) + Identität
printf 'https://x-access-token:%s@github.com\n' "$GITHUB_TOKEN" > /root/.git-credentials
chmod 600 /root/.git-credentials
git config --global credential.helper store
git config --global user.name "$GIT_NAME"
git config --global user.email "$GIT_EMAIL"

# Token zusätzlich als Env-Var (für Tools, die GITHUB_TOKEN lesen)
if [ -f /etc/opencode.env ]; then
  sed -i '/^GITHUB_TOKEN=/d' /etc/opencode.env 2>/dev/null || true
  printf 'GITHUB_TOKEN="%s"\n' "$GITHUB_TOKEN" >> /etc/opencode.env
fi

echo "  ✓ Git-Credentials für GitHub konfiguriert (clone/push privater Repos)"
