#!/bin/bash
# entrypoint.sh – Wrapper für robusten Container-Start (Profil: datascience)
#
# Wird bei JEDEM Container-Start ausgeführt (nicht nur beim ersten):
#   1. Grund-Tools (nur wenn fehlend)
#   2. Repo klonen bzw. git fetch + reset --hard FETCH_HEAD
#   3. Docker-in-Docker + ~/uni + JupyterLab + startup.sh im HINTERGRUND
#      (/usr/local/bin/uni-bootstrap.sh) – der Coder-Agent soll nicht warten
#   4. exec "$@" → Payload aus main.tf (Coder-Agent-Bootstrap)
#
# Reihenfolge im Hintergrund (siehe uni-bootstrap.sh):
#   dockerd-Treiber-Probe → ~/uni-Struktur → workspace-Symlink →
#   uv-Umgebungen → JupyterLab (8890) → startup.sh datascience

set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:$PATH"

# Ownername kommt aus main.tf (docker_container.env = Coder-Workspace-Owner);
# Fallback wie in den übrigen Templates (dort fest verdrahtet).
WS_OWNER="${WORKSPACE_OWNER:-carstenmueller2002}"
REPO_DIR="/home/${WS_OWNER}/opencode-coder-env"
UNI_DIR="/home/${WS_OWNER}/uni"
PROFILE="datascience"

# Für uni-bootstrap.sh und startup.sh (dort wird UNI_DIR/WS_OWNER gelesen)
export WS_OWNER UNI_DIR

echo "=== entrypoint.sh: Container-Start ($PROFILE, owner=$WS_OWNER) ==="
# Messbasis: alles bis „Payload starten“ läuft BLOCKIEREND vor dem Coder-Agenten
# – genau diese Zahl ist die gefühlte Startdauer (Node/Repo sind ins Image
# gebacken, hier sollte nur noch der Repo-Abgleich übrig bleiben).
T0=$SECONDS

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
  # Rettungsanker: der Hestia-Rollout startet diese Container regelmäßig neu
  # (Push auf .opencode/opencode-model-router.overrides.jsonc) und der harte
  # Reset unten verwirft dabei uncommittete Arbeit. Vorher alles in den Stash
  # legen — der liegt im .git des persistenten Volumes und bleibt per
  # `git stash list` / `git stash pop` wiederherstellbar.
  SKIP_RESET=""
  if cd "$REPO_DIR"; then
    if [ -n "$(git status --porcelain 2>/dev/null || true)" ]; then
      STASH_MSG="auto-stash vor Rollout $(date -Is)"
      if git stash push --include-untracked -m "$STASH_MSG" >/dev/null 2>&1; then
        echo "  ✓ Uncommittete Änderungen gesichert: $STASH_MSG (git stash list)"
      else
        echo "  ⚠ Auto-Stash fehlgeschlagen – Repo-Reset wird übersprungen (Arbeit bleibt liegen)"
        SKIP_RESET="1"
      fi
    fi
    if [ -z "$SKIP_RESET" ]; then
      git fetch --depth 1 origin master 2>&1 \
        && git -c core.fileMode=false reset --hard FETCH_HEAD 2>&1 \
        && git clean -fdq -- workspaces 2>/dev/null || true
    fi
  else
    echo "  ⚠ Repo-Verzeichnis nicht erreichbar – Repo-Abgleich übersprungen"
  fi
else
  echo "  → Repo klonen..."
  find "$REPO_DIR" -mindepth 1 -delete 2>/dev/null || true
  git clone --depth 1 --branch master https://github.com/DasCarstel/opencode-coder-env.git "$REPO_DIR" 2>&1
fi

# ── 3. DinD + Uni-Setup im Hintergrund ────────────────────────────────────
mkdir -p "$UNI_DIR/logs"
if [ -x /usr/local/bin/uni-bootstrap.sh ]; then
  echo "  → DinD-/Uni-Setup im Hintergrund starten (Log: $UNI_DIR/logs/uni-bootstrap.log)"
  nohup /usr/local/bin/uni-bootstrap.sh >> "$UNI_DIR/logs/uni-bootstrap.log" 2>&1 &
else
  echo "  ⚠ /usr/local/bin/uni-bootstrap.sh fehlt – DinD/Uni-Setup übersprungen"
fi

# ── 4. Payload aus main.tf ausführen (Coder-Agent-Bootstrap) ──────────────
echo "=== entrypoint.sh: Payload starten (nach $((SECONDS-T0))s) ==="
exec "$@"
