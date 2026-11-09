#!/usr/bin/env bash
# prune-sessions.sh — löscht alte Hermes-Sessions eines Profils.
#
# Läuft auf dem Coder-Host (systemd-Timer hermes-session-prune.timer, täglich)
# und nutzt das eingebaute CLI-Kommando `hermes sessions prune` — keine eigenen
# SQL-Eingriffe, damit FTS-Index, Blobs und Schema-Konsistenz erhalten bleiben.
#
# WICHTIG: Hermes verweigert `prune`, solange der Gateway-Prozess die Session-DB
# offen hält (Schutz vor kaputtem state.db-wal). Deshalb wird der Container
# kurz gestoppt, der Prune in einem Einmal-Container auf demselben Volume
# ausgeführt und der Container danach wieder gestartet. Ausfallzeit: wenige
# Sekunden, bewusst nachts (04:30).
#
# Einstellbar per Umgebung (systemd: /etc/default/hermes-session-prune):
#   HERMES_PRUNE_AGE      Aufbewahrung, Default 30d  (z. B. 14d, 60d)
#   HERMES_PRUNE_PROFILE  Hermes-Profil, Default kleve
#   HERMES_CONTAINER      Container-Name, Default hermes
#
# Bewusst nur EIN Profil pro Lauf: das Default-Profil (Krefeld) behält seinen
# Verlauf. Ein Aufruf ohne --profile würde dort löschen.
set -euo pipefail

CONTAINER="${HERMES_CONTAINER:-hermes}"
PROFILE="${HERMES_PRUNE_PROFILE:-kleve}"
AGE="${HERMES_PRUNE_AGE:-30d}"

log() { echo "[$(date -Is)] prune-sessions: $*"; }

if [ -f /etc/default/hermes-session-prune ]; then
  # shellcheck disable=SC1091
  . /etc/default/hermes-session-prune
  CONTAINER="${HERMES_CONTAINER:-$CONTAINER}"
  PROFILE="${HERMES_PRUNE_PROFILE:-$PROFILE}"
  AGE="${HERMES_PRUNE_AGE:-$AGE}"
fi

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -qx true; then
  log "Container '$CONTAINER' läuft nicht — Lauf übersprungen"
  exit 0
fi

IMAGE="$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")"
DATA_DIR="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/opt/data"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
if [ -z "$IMAGE" ] || [ -z "$DATA_DIR" ]; then
  log "Image oder /opt/data-Mount von '$CONTAINER' nicht ermittelbar — Abbruch"
  exit 1
fi
OWNER="$(stat -c '%u:%g' "$DATA_DIR")"

STOPPED=0
restore() {
  if [ "$STOPPED" = 1 ]; then
    log "starte Container wieder"
    docker start "$CONTAINER" >/dev/null || true
  fi
}
trap restore EXIT

log "stoppe '$CONTAINER' (Hermes hält die Session-DB offen)"
docker stop "$CONTAINER" >/dev/null
STOPPED=1

run_hermes() {
  docker run --rm --init -u "$OWNER" -e HOME=/opt/data \
    -v "$DATA_DIR:/opt/data" --entrypoint /opt/hermes/bin/hermes "$IMAGE" "$@"
}

log "prune: Profil=$PROFILE · älter als $AGE"
# --yes: kein interaktiver Prompt. Ohne --include-archived/--include-pinned
# bleiben archivierte und angepinnte Sessions bewusst erhalten.
run_hermes --profile "$PROFILE" sessions prune --older-than "$AGE" --yes

# Platz zurückholen (FTS-Merge + VACUUM, keine Datenänderung).
run_hermes --profile "$PROFILE" sessions optimize || true

log "fertig"
