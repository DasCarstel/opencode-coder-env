#!/usr/bin/env bash
# setup-runner.sh — Self-hosted GitHub-Actions-Runner auf dem Docker-Host (CT 100).
#
# Einmalige Ausführung. Env:
#   HESTIA_GITHUB_TOKEN  Runner-Registration-Token (GitHub: Repo → Settings →
#                        Actions → Runners → New self-hosted runner)
#   GH_RUNNER_DIR        Ziel-Ordner (Default /opt/docker-compose/github-runner)
#   GH_REPO_URL          Repo (Default DasCarstel/opencode-coder-env)
set -euo pipefail

GH_RUNNER_DIR="${GH_RUNNER_DIR:-/opt/docker-compose/github-runner}"
GH_REPO_URL="${GH_REPO_URL:-https://github.com/DasCarstel/opencode-coder-env}"
RUNNER_NAME="${RUNNER_NAME:-docker-host-hestia}"
RUNNER_VERSION="2.327.1"

: "${HESTIA_GITHUB_TOKEN:?HESTIA_GITHUB_TOKEN fehlt (Runner-Registration-Token)}"

mkdir -p "$GH_RUNNER_DIR"
cd "$GH_RUNNER_DIR"

if [ ! -f config.sh ]; then
  echo "→ Lade actions-runner..."
  curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  tar xzf runner.tar.gz && rm -f runner.tar.gz
fi

if [ ! -f .runner ]; then
  echo "→ Registriere Runner (Label: docker-host)..."
  ./config.sh \
    --url "$GH_REPO_URL" \
    --token "$HESTIA_GITHUB_TOKEN" \
    --name "$RUNNER_NAME" \
    --labels docker-host \
    --work /opt/github-runners/_work \
    --unattended --replace
else
  echo "✓ Runner bereits registriert"
fi

if ! id -u github-runner >/dev/null 2>&1; then
  useradd -m -u 1200 github-runner 2>/dev/null || true
fi
chown -R github-runner:github-runner "$GH_RUNNER_DIR" /opt/github-runners 2>/dev/null || true

# als systemd-User-Service installieren
./svc.sh install github-runner
./svc.sh start
systemctl is-active "actions.runner.*" 2>/dev/null && echo "✓ Runner läuft (Label: docker-host)"
