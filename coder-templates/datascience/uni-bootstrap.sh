#!/bin/bash
# uni-bootstrap.sh – Hintergrund-Setup des datascience-Workspaces (Docker-in-Docker)
#
# Reihenfolge (so entschieden):
#   1. /var/lib/docker vorbereiten + dockerd mit Treiber-Probe
#      (overlay2 → fuse-overlayfs → vfs; KEIN Abbruch, wenn Docker scheitert)
#   2. cgroup-Nachweis: "swap limit"-Warnung darf leer sein (Limits wirken)
#   3. ~/uni-Struktur anlegen (Host-Bind-Mount /etc/coder/uni-data, überlebt
#      das Löschen + Neuanlegen des Workspace)
#   4. workspace/-Symlink im FernUni-Klon auf
#      ~/uni/module/63123-data-engineering-fuer-data-science/workspace setzen
#      – die Data-Engineering-Notebooks liegen damit im 63123-Git-Repo und
#      sind versioniert (vorher: ~/uni/workspace, in keinem Repo)
#   5. uv-Umgebungen (ml/dl) im Hintergrund anstoßen (uni-envs-setup.sh)
#   6. JupyterLab auf Port 8890 starten (8888 gehört dem FernUni-Stack)
#   6b. On-Demand-Tor für den FernUni-Stack auf 8891 starten (startet den
#      Compose-Stack beim App-Aufruf, stoppt ihn nach 3 h Leerlauf)
#   7. startup.sh datascience (letzter Schritt)
#   8. Modul-Repos (Git) abgleichen (uni-git-setup.sh im Hintergrund) – läuft
#      bewusst NACH startup.sh, weil erst dort GITHUB_TOKEN aus OpenBao kommt
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
GIT_LOG="$LOG_DIR/git-setup.log"
JUPYTER_LOG="$LOG_DIR/jupyter.log"
SYNC_LOG="$LOG_DIR/sync.log"
JUPYTER_PORT=8890
JUPYTER_BIN="/opt/jupyter/bin/jupyter-lab"
KERNEL_DIR="/root/.local/share/jupyter/kernels"
FERNUNI_REPO="$UNI_DIR/data-engineering-infrastructure"

# Bind-Mount-Ziel des FernUni-Compose-Stacks (`./workspace` im Klon): liegt im
# 63123-Modul-Repo, damit die Data-Engineering-Notebooks git-versioniert sind
# (privates Remote: DasCarstel/fernuni-63123-data-engineering-fuer-data-science).
# Vorher zeigte der Symlink auf ~/uni/workspace – ein Ordner in KEINEM Repo;
# bei Verlust des ~/uni-Ordners wären die Notebooks unwiederbringlich weg.
# Das Zielverzeichnis wird NICHT hier angelegt, sondern kommt aus dem Git-Klon
# (uni-git-setup.sh) – sonst würde `git clone` in ein nicht-leeres Verzeichnis
# laufen. Solange der Klon fehlt, ist der Symlink kurzzeitig „dangling“; der
# Ordner entsteht mit dem Klon (workspace/ ist im Repo versioniert).
DE_MODULE_REPO="$UNI_DIR/module/63123-data-engineering-fuer-data-science"
DE_WORKSPACE_DIR="$DE_MODULE_REPO/workspace"

export UNI_DIR WS_OWNER

echo "=== $(date -Is) uni-bootstrap gestartet (owner=$WS_OWNER) ==="

# ── 0. ~/uni-Struktur (Host-Bind-Mount, überlebt Workspace-Neuanlage) ────
# Kein `workspace/` mehr: das Bind-Mount-Ziel liegt jetzt im 63123-Modul-Repo
# (siehe DE_WORKSPACE_DIR) und entsteht durch den Git-Klon.
mkdir -p "$UNI_DIR"/module "$UNI_DIR"/material "$UNI_DIR"/state \
         "$UNI_DIR"/logs "$UNI_DIR"/envs/ml "$UNI_DIR"/envs/dl
: >> "$ENV_LOG"; : >> "$SYNC_LOG"; : >> "$DOCKER_SUMMARY"; : >> "$GIT_LOG"

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

# ── 2a. DinD-Vorbereitung für den FernUni-Stack ────────────────────────────
# (a) Socket: der pyspark-client-node läuft als UID 1000 und mountet den
#     INNEREN Docker-Socket (DinD aus Notebooks). Der Socket gehört nur dem
#     Daemon im Container – kein Host-Zugriff, deshalb 666 hier vertretbar.
# (b) Das Bind-Mount-Ziel des Stacks (DE_WORKSPACE_DIR im 63123-Modul-Repo)
#     braucht Schreibrechte für UID 1000 – sonst startet Jupyter, aber die
#     Notebooks sind read-only. Der Ordner stammt aus dem Git-Klon; auf einem
#     frischen Workspace gibt es ihn noch nicht, dann wird hier NICHTS
#     angelegt (sonst scheitert der spätere `git clone` an einem nicht-leeren
#     Verzeichnis). uni-git-setup.sh setzt Symlink + Rechte nach dem Klon.
chmod 666 /var/run/docker.sock 2>/dev/null || true
if [ -d "$DE_MODULE_REPO/.git" ]; then
  mkdir -p "$DE_WORKSPACE_DIR"
  chmod -R a+rwX "$DE_WORKSPACE_DIR" 2>/dev/null || true
fi

# ── 2b. FernUni-Repo sicherstellen (öffentliches GitLab, idempotent) ───────
# Der Klon liegt im persistenten Host-Ordner ~/uni und übersteht Neustarts.
# Fehlt er (frischer Workspace), wird er hier einmalig geholt.
FERNUNI_URL="https://code.dbis-pro1.fernuni-hagen.de/pub-access/data-engineering-infrastructure.git"
if [ ! -d "$FERNUNI_REPO/.git" ]; then
  echo "  → FernUni-Repo klonen (code.dbis-pro1.fernuni-hagen.de)..."
  if GIT_TERMINAL_PROMPT=0 git clone -q "$FERNUNI_URL" "$FERNUNI_REPO" 2>/dev/null; then
    echo "  ✓ FernUni-Repo geklont ($(git -C "$FERNUNI_REPO" tag | sort -V | tail -1))"
  else
    echo "  ⚠ FernUni-Repo konnte nicht geklont werden (Netz/Proxy?) – Deploy-DeDs-*.sh nicht verfügbar"
  fi
else
  echo "  ✓ FernUni-Repo vorhanden ($(git -C "$FERNUNI_REPO" tag | sort -V | tail -1))"
fi

# ── 3. workspace/-Symlink im FernUni-Klon → 63123-Modul-Repo ──────────────
# Das FernUni-Repo bringt einen eigenen workspace/-Ordner mit; der Compose-Stack
# bind-mountet ihn (`./workspace`). Er wird durch einen Symlink auf
# DE_WORKSPACE_DIR ersetzt – den workspace/-Ordner des privaten 63123-Git-Repos.
# Nur so sind Carstens Data-Engineering-Notebooks versioniert (Commit + Push)
# und auch nach einem geleerten Host-Ordner wiederherstellbar.
#
# Idempotent: passt der Symlink schon, passiert nichts. Zeigt er noch auf das
# ALTE Ziel ~/uni/workspace (ein Ordner außerhalb JEDES Repos), wird er
# umgebogen und der Altbestand entfernt (Abschnitt 3a).
if [ -d "$FERNUNI_REPO" ]; then
  if [ -L "$FERNUNI_REPO/workspace" ]; then
    ZIEL="$(readlink "$FERNUNI_REPO/workspace")"
    if [ "$ZIEL" = "$DE_WORKSPACE_DIR" ]; then
      echo "  ✓ workspace-Symlink vorhanden → $ZIEL"
    else
      ln -sfn "$DE_WORKSPACE_DIR" "$FERNUNI_REPO/workspace"
      echo "  ✓ workspace-Symlink umgebogen ($ZIEL → $DE_WORKSPACE_DIR)"
    fi
  else
    rm -rf "$FERNUNI_REPO/workspace"
    ln -sfn "$DE_WORKSPACE_DIR" "$FERNUNI_REPO/workspace"
    echo "  ✓ workspace/ → $DE_WORKSPACE_DIR (Symlink gesetzt)"
  fi
else
  echo "  ⚠ FernUni-Repo fehlt (Klon fehlgeschlagen?) – Deploy-DeDs-*.sh erst nach erfolgreichem Klon nutzbar"
fi

# ── 3a. Altbestand ~/uni/workspace entfernen ──────────────────────────────
# Das war das frühere Symlink-Ziel des Compose-Stacks – ein Ordner in KEINEM
# Git-Repo. Er wird nicht mehr verwendet; steht dort außer probe.txt nichts,
# wird er gelöscht, damit niemand versehentlich Notebooks am falschen Ort
# ablegt. Liegt echter Inhalt darin, wird NICHT gelöscht, sondern gewarnt.
if [ -d "$UNI_DIR/workspace" ] && [ ! -L "$UNI_DIR/workspace" ]; then
  REST="$(find "$UNI_DIR/workspace" -mindepth 1 ! -name probe.txt -print -quit 2>/dev/null)"
  if [ -z "$REST" ]; then
    rm -rf "$UNI_DIR/workspace"
    echo "  ✓ Altbestand ~/uni/workspace entfernt (enthielt nur probe.txt; neues Ziel: $DE_WORKSPACE_DIR)"
  else
    echo "  ⚠ ~/uni/workspace enthält noch Dateien ($REST) – NICHT entfernt. Bitte nach $DE_WORKSPACE_DIR verschieben, dann Ordner löschen."
  fi
fi

# ── 3b. Wrapper `deds` für die FernUni-Deploy-Skripte ──────────────────────
# Die FernUni-Skripte (Deploy-DeDs-Candle.sh & Co.) setzen `user: ${UID}:${GID}`
# aus `id -u`. Im Workspace läuft alles als root – Jupyter im client-node bricht
# dann mit "Running as root is not recommended" ab (Exit 1). Der Wrapper
# erzwingt die vorgesehene UID/GID 1000 (User pyspark-client im Image).
cat > /usr/local/bin/deds <<'DEDS'
#!/usr/bin/env bash
# deds — FernUni-Stack im datascience-Workspace starten (Candle/Firework/Rocket).
# Aufruf: deds [candle|firework|rocket|down]   (Default: candle)
set -euo pipefail
REPO=/home/carstenmueller2002/uni/data-engineering-infrastructure
MODE="${1:-candle}"
cd "$REPO" 2>/dev/null || { echo "FernUni-Repo fehlt: $REPO"; exit 1; }
case "$MODE" in
  candle)   exec env UID=1000 GID=1000 docker compose -f docker-compose.yml up client-node ;;
  firework) exec env UID=1000 GID=1000 docker compose -f docker-compose.yml -f docker-compose-small.yml up ;;
  rocket)   exec env UID=1000 GID=1000 docker compose -f docker-compose.yml up ;;
  down)     exec docker compose -f docker-compose.yml -f docker-compose-small.yml down ;;
  *) echo "Aufruf: deds [candle|firework|rocket|down]"; exit 2 ;;
esac
DEDS
chmod 0755 /usr/local/bin/deds
echo "  ✓ Wrapper deds installiert (/usr/local/bin/deds → UID 1000)"

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

# ── 5b. On-Demand-Tor für den FernUni-Stack (8891 → 8888) ─────────────────
# Die Coder-App `fernuni-jupyter` zeigt NICHT mehr direkt auf 8888, sondern auf
# dieses Tor. Es startet den Compose-Stack beim ersten Zugriff, liefert
# währenddessen eine 503-Seite mit Auto-Refresh aus und stoppt ihn nach 3 h
# ohne Zugriff – aber nur, wenn es ihn selbst gestartet hat (ein per
# `deds candle` im Terminal gestarteter Stack wird nur geproxyt, nie gestoppt).
# Der Stack selbst bleibt unverändert auf Port 8888.
#
# Idempotent: Läuft das Tor schon, wird nichts doppelt gestartet. Der Check
# geht über `ss` auf den LISTEN-Socket – eine TCP-Probe (curl) würde im Tor
# selbst einen „Zugriff“ samt Startversuch auslösen und den Leerlauf-Zähler
# zurücksetzen. `pgrep` wäre unpräzise, weil eine Testinstanz auf einem
# anderen Port denselben Skriptnamen trägt.
#
# `setsid` + `nohup` wie bei den anderen Hintergrunddiensten: das Tor muss den
# Bootstrap überleben (der läuft bei jedem Workspace-Start erneut) und darf
# keinen SIGHUP abbekommen. Es schreibt selbst in ~/uni/logs/fernuni-gate.log;
# stdout/stderr gehen in dieselbe Datei, damit ein Python-Traceback nicht
# verloren geht (Log-Zeilen schreibt das Tor nur, wenn stdout ein TTY ist –
# so steht keine Zeile doppelt).
#
# Workspace-Neustart: Der Container (inkl. innerem dockerd) wird neu gebaut,
# damit ist auch ein zuvor vom Tor gestarteter Stack weg – das Tor kennt dann
# wieder „selbst gestartet = nein“ und startet beim nächsten App-Aufruf frisch.
GATE_PY="$REPO_DIR/coder-templates/datascience/fernuni-gate.py"
GATE_LOG="$LOG_DIR/fernuni-gate.log"
GATE_LISTEN="127.0.0.1:8891"
# Port-Check ohne 'ss': ss ist in den Workspace-Images nicht installiert, der
# frühere Check griff dadurch nie und stufte ein laufendes Tor stets als
# "nicht laufend" ein (parallel gestartete zweite Instanz). Fallback ist ein
# reiner TCP-Connect über bash (/dev/tcp) – funktioniert hier, weil das Tor
# auf Loopback lauscht und ein refused sofort zurückkommt.
port_open() {  # port_open <host> <port>
  if command -v ss >/dev/null 2>&1; then
    ss -tln 2>/dev/null | grep -qE "(^|[^0-9])${1}:${2}([^0-9]|\$)"
  else
    (exec 3<>"/dev/tcp/${1}/${2}") 2>/dev/null
  fi
}

if [ ! -f "$GATE_PY" ]; then
  echo "  ⚠ fernuni-gate.py fehlt ($GATE_PY) – die FernUni-App startet den Stack nicht mehr von selbst"
elif port_open 127.0.0.1 8891; then
  echo "  ✓ FernUni-Tor läuft bereits auf $GATE_LISTEN"
else
  echo "  → FernUni-Tor starten ($GATE_LISTEN → 127.0.0.1:8888, Log: $GATE_LOG)"
  ( umask 022; setsid nohup python3 "$GATE_PY" \
      --listen "$GATE_LISTEN" --target 127.0.0.1:8888 --repo "$FERNUNI_REPO" \
      >> "$GATE_LOG" 2>&1 & )
  sleep 1
  if ss -tln 2>/dev/null | grep -qE '(^|[^0-9])127\.0\.0\.1:8891([^0-9]|$)'; then
    echo "  ✓ FernUni-Tor läuft (Stack startet bei App-Aufruf, Stopp nach 3 h Leerlauf)"
  else
    echo "  ⚠ FernUni-Tor konnte nicht starten (siehe $GATE_LOG) – Stack weiterhin nur per 'deds candle'"
  fi
fi

# ── 6. startup.sh (Profil datascience) ────────────────────────────────────
echo "  → startup.sh $PROFILE starten (Log: /tmp/uni-startup.log)"
# -f statt -x: startup.sh wird hier ohnehin per 'bash' aufgerufen, das
# Ausführungsbit ist also nicht nötig. Der frühere -x-Test machte den Aufruf
# davon abhängig und ließ die gesamte Setup-Kette still ausfallen, nachdem
# das Bit in Git verloren ging (737826e: 100755 -> 100644).
if [ -f "$REPO_DIR/coder-templates/shared/startup.sh" ]; then
  bash "$REPO_DIR/coder-templates/shared/startup.sh" "$PROFILE" > /tmp/uni-startup.log 2>&1 \
    || echo "  ⚠ startup.sh wurde mit Fehler beendet (siehe /tmp/uni-startup.log)"
else
  echo "  ⚠ startup.sh nicht gefunden: $REPO_DIR/coder-templates/shared/startup.sh"
fi

# ── 6b. Modul-Repos (Git) im Hintergrund ──────────────────────────────────
# Pro Studienmodul ein eigenes privates GitHub-Repo; ~/uni/module ist persistent.
# Ein NEUER Workspace (leeres ~/uni) holt die Repos hier aus dem Remote zurück.
# Läuft NACH startup.sh, weil GITHUB_TOKEN erst dort aus OpenBao geladen wird
# (/etc/opencode.env). Bewusst im Hintergrund: Klonen/Pushen darf den
# Workspace-Start nicht verzögern.
echo "  → Modul-Repos im Hintergrund abgleichen (Log: $GIT_LOG)"
GIT_SETUP=""
for kandidat in "$REPO_DIR/coder-templates/datascience/uni-git-setup.sh" \
                /usr/local/bin/uni-git-setup.sh; do
  [ -f "$kandidat" ] && GIT_SETUP="$kandidat" && break
done
if [ -n "$GIT_SETUP" ]; then
  nohup bash "$GIT_SETUP" >> "$GIT_LOG" 2>&1 &
else
  echo "  ⚠ uni-git-setup.sh nicht gefunden – Modul-Repos werden nicht abgeglichen"
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
