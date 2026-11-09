#!/usr/bin/env bash
# prune-sessions.sh — löscht alte Hermes-Sessions eines Profils.
#
# Läuft auf dem Coder-Host (systemd-Timer hermes-session-prune.timer, täglich)
# und arbeitet über `docker exec` im Hermes-Container. Nutzt das eingebaute
# CLI-Kommando `hermes sessions prune` — keine eigenen SQL-Eingriffe, damit
# FTS-Index, Blobs und Schema-Konsistenz erhalten bleiben.
#
# Einstellbar per Umgebung (systemd: Environment= in der Unit oder
# /etc/default/hermes-session-prune):
#   HERMES_PRUNE_AGE      Aufbewahrung, Default 30d  (z. B. 14d, 60d)
#   HERMES_PRUNE_PROFILE  Hermes-Profil, Default kleve
#   HERMES_CONTAINER      Container-Name, Default hermes
#
# Bewusst nur EIN Profil pro Lauf: das Default-Profil (Krefeld) behält seinen
# Verlauf. Ein zusätzlicher Aufruf ohne --profile würde den dort löschen.
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

log "Profil=$PROFILE · älter als $AGE"

# --yes: kein interaktiver Prompt (Timer). Ohne --include-archived/--include-pinned
# bleiben archivierte und angepinnte Sessions bewusst erhalten.
docker exec "$CONTAINER" sh -lc \
  "HOME=/opt/data /opt/hermes/bin/hermes --profile $PROFILE sessions prune --older-than $AGE --yes"

# Platz zurückholen (FTS-Merge + VACUUM, keine Datenänderung).
docker exec "$CONTAINER" sh -lc \
  "HOME=/opt/data /opt/hermes/bin/hermes --profile $PROFILE sessions optimize" || true

log "fertig"
