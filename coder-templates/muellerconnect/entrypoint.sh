#!/bin/bash
# entrypoint.sh – Wrapper für robusten Container-Start (muellerconnect)
#
# Wird bei JEDEM Container-Start ausgeführt (nicht nur beim ersten).
# Codiert das Repo, führt startup.sh aus, startet herdr — alles im
# Hintergrund, damit der Coder-Agent schnell verbinden kann.
# Danach: exec "$@" (der Payload aus main.tf = Coder-Agent-Bootstrap).

set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:$PATH"

REPO_DIR="/home/carstenmueller2002/opencode-coder-env"
PROFILE="muellerconnect"

echo "=== entrypoint.sh: Container-Start ($PROFILE) ==="

# ── 1. Grund-Tools (nur wenn fehlend) ─────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
if ! command -v curl >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
  echo "  → Grund-Tools installieren..."
  apt-get update -qq && apt-get install -y -qq \
    curl git ca-certificates gnupg python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
fi

# Node.js v22 (nur wenn fehlend)
if ! command -v node >/dev/null 2>&1; then
  echo "  → Node.js installieren..."
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

# ── 3. Setup im Hintergrund (Agent soll nicht warten) ─────────────────────
echo "  → Setup im Hintergrund starten..."
nohup bash -c "
  bash '$REPO_DIR/coder-templates/shared/startup.sh' '$PROFILE' 2>&1
  echo '  → herdr prüfen...'
  if ! herdr status 2>/dev/null | grep -q 'running'; then
    echo '  → herdr starten...'
    nohup herdr server > /tmp/herdr-server.log 2>&1 &
    sleep 3
  fi
  if herdr status 2>/dev/null | grep -q 'running'; then
    echo '  ✓ herdr läuft'
  else
    echo '  ⚠ herdr nicht erreichbar'
  fi
" > /tmp/entrypoint-setup.log 2>&1 &

# ── 4. Payload aus main.tf ausführen (Coder-Agent-Bootstrap) ──────────────
echo "=== entrypoint.sh: Payload starten ==="
exec "$@"
