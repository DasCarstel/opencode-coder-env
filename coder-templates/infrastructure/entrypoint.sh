#!/bin/bash
# entrypoint.sh – Wrapper für robusten Container-Start
#
# Wird bei JEDEM Container-Start ausgeführt (nicht nur beim ersten).
# Codiert das Repo, führt startup.sh aus, startet herdr und hält den
# Container am Laufen (Watchdog).
#
# Problem: Coder führt das Agent-startup_script nur beim ersten Start
# aus. Ein UI-Restart startet den Container neu, aber startup.sh (und
# damit herdr) nicht erneut. Dieser Wrapper löst das.

set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:$PATH"

REPO_DIR="/home/carstenmueller2002/opencode-coder-env"

echo "=== entrypoint.sh: Container-Start ==="

# ── 1. Grund-Tools ─────────────────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq \
  curl git ca-certificates gnupg python3 python3-pip \
  && rm -rf /var/lib/apt/lists/*

# Node.js v22
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

# ── 2. Repo aktualisieren ──────────────────────────────────────────────────
mkdir -p "$(dirname "$REPO_DIR")"
if [ -d "$REPO_DIR/.git" ]; then
  echo "  → Repo aktualisieren..."
  cd "$REPO_DIR" \
    && git fetch --depth 1 origin master 2>&1 \
    && git -c core.fileMode=false reset --hard FETCH_HEAD 2>&1 \
    && git clean -fdq -- workspaces 2>/dev/null || true
else
  echo "  → Repo klonen..."
  find "$REPO_DIR" -mindepth 1 -delete 2>/dev/null || true
  git clone --depth 1 --branch master https://github.com/DasCarstel/opencode-coder-env.git "$REPO_DIR" 2>&1
fi

# ── 3. startup.sh ausführen (Secrets, SSH, herdr, OpenCode, MCP) ──────────
echo "  → startup.sh ausführen..."
bash "$REPO_DIR/coder-templates/shared/startup.sh" infrastructure 2>&1 || echo "  ⚠ startup.sh mit Fehlern beendet"

# ── 4. herdr starten (idempotent) ─────────────────────────────────────────
echo "  → herdr prüfen..."
if ! herdr status 2>/dev/null | grep -q "running"; then
  echo "  → herdr starten..."
  nohup herdr server > /tmp/herdr-server.log 2>&1 &
  sleep 3
fi
if herdr status 2>/dev/null | grep -q "running"; then
  echo "  ✓ herdr läuft"
else
  echo "  ⚠ herdr nicht erreichbar"
fi

# ── 5. Watchdog — Container bleibt am Laufen ──────────────────────────────────
echo "=== entrypoint.sh: Watchdog aktiv ==="
tail -f /dev/null
