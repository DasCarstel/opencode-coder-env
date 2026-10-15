#!/bin/bash
# uni-bootstrap.sh – Hintergrund-Setup des datascience-Workspaces (Docker-in-Docker)
#
# Reihenfolge (so entschieden):
#   1. /var/lib/docker vorbereiten + dockerd mit Treiber-Probe
#      (overlay2 → fuse-overlayfs → vfs; KEIN Abbruch, wenn Docker scheitert)
#   2. cgroup-Nachweis: "swap limit"-Warnung darf leer sein (Limits wirken)
#   3. ~/uni-Struktur anlegen (persistentes Volume)
#   4. workspace/-Symlink im FernUni-Klon auf ~/uni/workspace setzen
#   5. uv-Umgebungen (ml/dl) im Hintergrund anstoßen (uni-envs-setup.sh)
#   6. JupyterLab auf Port 8890 starten (8888 gehört dem FernUni-Stack)
#   7. startup.sh datascience (letzter Schritt)
#
# Wird von entrypoint.sh per nohup gestartet; bewusst OHNE `set -e`, damit ein
# Fehler (z. B. dockerd) den Workspace-Start nicht verhindert.

set -uo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:$PATH"

WS_OWNER="${WS_OWNER:-${WORKSPACE_OWNER:-carstenmueller2002}}"
UNI_DIR="${UNI_DIR:-/home/${WS_OWNER}/uni}"
REPO_DIR="/home/${WS_OWNER}/opencode-coder-env"
PROFILE="datascience"

DOCKER_DATA_ROOT="/var/lib/docker"
DOCKER_LOG="/var/log/dockerd.log"
LOG_DIR="$UNI_DIR/logs"
DOCKER_SUMMARY="$LOG_DIR/dockerd.log"
ENV_LOG="$LOG_DIR/env-setup.log"
JUPYTER_LOG="$LOG_DIR/jupyter.log"
SYNC_LOG="$LOG_DIR/sync.log"
JUPYTER_PORT=8890
JUPYTER_BIN="/opt/jupyter/bin/jupyter-lab"
KERNEL_DIR="/root/.local/share/jupyter/kernels"
FERNUNI_REPO="$UNI_DIR/data-engineering-infrastructure"

export UNI_DIR WS_OWNER

echo "=== $(date -Is) uni-bootstrap gestartet (owner=$WS_OWNER) ==="

# ── 0. ~/uni-Struktur (persistentes Volume) ───────────────────────────────
mkdir -p "$UNI_DIR"/module "$UNI_DIR"/material "$UNI_DIR"/state \
         "$UNI_DIR"/logs "$UNI_DIR"/workspace "$UNI_DIR"/envs/ml "$UNI_DIR"/envs/dl
: >> "$ENV_LOG"; : >> "$SYNC_LOG"; : >> "$DOCKER_SUMMARY"

log_docker() {
  printf '%s %s\n' "$(date -Is)" "$*" >> "$DOCKER_SUMMARY"
}

# ── 1. dockerd starten: Treiber-Probe overlay2 → fuse-overlayfs → vfs ─────
mkdir -p "$DOCKER_DATA_ROOT"
rm -f /var/run/docker.pid
DOCKER_OK=""

for drv in overlay2 fuse-overlayfs vfs; do
  echo "  → dockerd starten (storage-driver=$drv)..."
  dockerd --host=unix:///var/run/docker.sock --storage-driver="$drv" --iptables=true \
          --data-root="$DOCKER_DATA_ROOT" >"$DOCKER_LOG" 2>&1 &

  for _ in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then DOCKER_OK="$drv"; break; fi
    sleep 1
  done
  [ -n "$DOCKER_OK" ] && break

  echo "  ⚠ storage-driver=$drv fehlgeschlagen – nächster Versuch"
  pkill -x dockerd 2>/dev/null || true
  pkill -x containerd 2>/dev/null || true
  sleep 2
done

if [ -n "$DOCKER_OK" ]; then
  echo "  ✓ dockerd läuft (storage-driver=$DOCKER_OK)"
  log_docker "storage-driver=$DOCKER_OK"
  log_docker "docker-server=$(docker version --format '{{.Server.Version}}' 2>/dev/null)"
  log_docker "compose=$(docker compose version --short 2>/dev/null || echo 'nicht verfuegbar')"
  log_docker "data-root=$DOCKER_DATA_ROOT images=$(docker images -q 2>/dev/null | wc -l)"
else
  echo "  ⚠ WARNUNG: dockerd konnte nicht gestartet werden (siehe $DOCKER_LOG)"
  log_docker "WARNUNG: dockerd-Start fehlgeschlagen (siehe $DOCKER_LOG)"
  tail -n 5 "$DOCKER_LOG" 2>/dev/null >> "$DOCKER_SUMMARY" || true
fi

# ── 2. cgroup-Nachweis: Speicher-/CPU-Limits der inneren Container wirken ──
if docker info >/dev/null 2>&1; then
  SWAP_WARN="$(docker info 2>&1 | grep -i 'swap limit' || true)"
  if [ -n "$SWAP_WARN" ]; then
    echo "  ⚠ cgroup-Warnung: $SWAP_WARN"
    log_docker "cgroup-WARNUNG: $SWAP_WARN"
  else
    echo "  ✓ cgroup: keine 'swap limit'-Warnung – Limits der inneren Container wirken"
    log_docker "cgroup: keine 'swap limit'-Warnung (Limits wirken)"
  fi
fi

# ── 3. workspace/-Symlink im FernUni-Klon (> ~/uni/workspace) ─────────────
# Das FernUni-Repo bringt einen eigenen workspace/-Ordner mit; der Compose-Stack
# bind-mountet ihn. Er wird durch einen Symlink auf das persistente
# ~/uni/workspace ersetzt – aber nur, wenn der Klon existiert und der Symlink
# nicht schon passt.
if [ -d "$FERNUNI_REPO" ]; then
  if [ -L "$FERNUNI_REPO/workspace" ]; then
    ZIEL="$(readlink "$FERNUNI_REPO/workspace")"
    if [ "$ZIEL" = "$UNI_DIR/workspace" ]; then
      echo "  ✓ workspace-Symlink vorhanden → $ZIEL"
    else
      ln -sfn "$UNI_DIR/workspace" "$FERNUNI_REPO/workspace"
      echo "  ✓ workspace-Symlink korrigiert ($ZIEL → $UNI_DIR/workspace)"
    fi
  else
    rm -rf "$FERNUNI_REPO/workspace"
    ln -sfn "$UNI_DIR/workspace" "$FERNUNI_REPO/workspace"
    echo "  ✓ workspace/ → $UNI_DIR/workspace (Symlink gesetzt)"
  fi
else
  echo "  ℹ FernUni-Repo noch nicht geklont – Symlink wird beim nächsten Start gesetzt"
fi

# ── 4. uv-Umgebungen (ml/dl) im Hintergrund – blockiert den Start nicht ───
echo "  → uv-Umgebungen im Hintergrund einrichten (Log: $ENV_LOG)"
nohup /usr/local/bin/uni-envs-setup.sh >> "$ENV_LOG" 2>&1 &

# ── 5. JupyterLab (8890) im Hintergrund ──────────────────────────────────
# Supervisor: startet JupyterLab neu, wenn uni-envs-setup.sh neue Kernel-Specs
# installiert hat (/root/.jupyter-restart-request) – der KernelSpecManager liest
# die Specs nur beim Start.
jupyter_supervisor() {
  for _ in $(seq 1 60); do
    [ -f "$KERNEL_DIR/ml/kernel.json" ] && [ -f "$KERNEL_DIR/dl/kernel.json" ] && break
    sleep 2
  done
  while true; do
    echo "=== $(date -Is) JupyterLab-Start (Port $JUPYTER_PORT) ===" >> "$JUPYTER_LOG"
    "$JUPYTER_BIN" --ip=0.0.0.0 --port="$JUPYTER_PORT" --no-browser --allow-root \
      --ServerApp.token="" --ServerApp.password="" \
      --ServerApp.allow_remote_access=True \
      --ServerApp.root_dir="$UNI_DIR" >> "$JUPYTER_LOG" 2>&1 &
    local pid=$!
    wait "$pid"
    if [ -f /root/.jupyter-restart-request ]; then
      rm -f /root/.jupyter-restart-request
      echo "=== $(date -Is) Neustart wegen neuer Kernel-Specs ===" >> "$JUPYTER_LOG"
      sleep 2
    else
      break
    fi
  done
}

if [ -x "$JUPYTER_BIN" ]; then
  echo "  → JupyterLab auf Port $JUPYTER_PORT (Log: $JUPYTER_LOG)"
  jupyter_supervisor &
else
  echo "  ⚠ $JUPYTER_BIN fehlt – JupyterLab wird nicht gestartet"
fi

# ── 6. startup.sh (Profil datascience) ────────────────────────────────────
echo "  → startup.sh $PROFILE starten (Log: /tmp/uni-startup.log)"
if [ -x "$REPO_DIR/coder-templates/shared/startup.sh" ]; then
  bash "$REPO_DIR/coder-templates/shared/startup.sh" "$PROFILE" > /tmp/uni-startup.log 2>&1 \
    || echo "  ⚠ startup.sh wurde mit Fehler beendet (siehe /tmp/uni-startup.log)"
else
  echo "  ⚠ startup.sh nicht gefunden: $REPO_DIR/coder-templates/shared/startup.sh"
fi

# herdr sicherstellen (analog general/entrypoint.sh)
echo "  → herdr prüfen..."
if ! herdr status 2>/dev/null | grep -q 'running'; then
  echo "  → herdr starten..."
  nohup herdr server > /tmp/herdr-server.log 2>&1 &
  sleep 3
fi
if herdr status 2>/dev/null | grep -q 'running'; then
  echo "  ✓ herdr läuft"
else
  echo "  ⚠ herdr nicht erreichbar"
fi

echo "=== $(date -Is) uni-bootstrap fertig ==="
