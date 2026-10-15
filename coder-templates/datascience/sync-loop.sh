#!/bin/bash
# sync-loop.sh – systemd-freier Timer für fernuni-sync (Profil datascience).
#
# Aufruf: sync-loop.sh <sync.py> <config.json> <env-file> <log>
#
# Läuft einmal sofort und danach alle 6 Stunden (SYNC_INTERVALL überschreibbar).
# Der Zustand liegt in der State-Datei aus der config.json — im persistenten
# Volume ~/uni/state — deshalb ist ein Workspace-Neustart idempotent: der erste
# Lauf nach dem Neustart meldet "0 Downloads".
#
# Der Loop schreibt nur nach <log>; Secrets stehen in <env-file> (600) und
# werden von sync.py per --env-file gelesen (keine Shell-Expansion von '$').

set -uo pipefail

SYNC_PY="${1:?Aufruf: sync-loop.sh <sync.py> <config.json> <env-file> <log>}"; shift
SYNC_CFG="${1:?config.json fehlt}"; shift
SYNC_ENV="${1:?env-file fehlt}"; shift
SYNC_LOG="${1:?log-Pfad fehlt}"; shift

INTERVALL="${SYNC_INTERVALL:-21600}"   # 6 Stunden
PY="${PYTHON:-python3}"

while true; do
  {
    echo ""
    echo "=== $(date -Is) Sync-Lauf starten ==="
  } >> "$SYNC_LOG"

  "$PY" "$SYNC_PY" --env-file "$SYNC_ENV" --config "$SYNC_CFG" >> "$SYNC_LOG" 2>&1
  RC=$?

  echo "=== $(date -Is) Sync-Lauf beendet (Exit $RC) – nächster Lauf in ${INTERVALL}s ===" >> "$SYNC_LOG"
  sleep "$INTERVALL"
done
