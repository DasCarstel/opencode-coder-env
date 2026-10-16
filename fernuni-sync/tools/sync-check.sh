#!/bin/bash
# Manuelle Verifikation des oCIS-Syncs: State aufraeumen + zwei Sync-Laeufe.
# Schreibt ausschliesslich Logs unter ~/uni/logs/ - keine Vault-Inhalte,
# keine Loeschungen.
#
# Aufruf im Workspace:
#     bash ~/opencode-coder-env/fernuni-sync/tools/sync-check.sh
#
# Beleg fuer die Idempotenz: der ERSTE Lauf auf einem frischen State zieht
# Dateien (Downloads > 0), der ZWEITE muss "0 Download(s) ... Ergebnis: ok"
# melden. Genau diese zwei Zeilen sind der Nachweis, dass ein Workspace-
# Neubau den Sync-Zustand nicht neu aufbaut.
set -uo pipefail

# WORKSPACE_OWNER setzt das Template (main.tf) als Container-Env. Fail-closed:
# ohne die Variable sind die Pfade nicht eindeutig bestimmbar.
WS_OWNER="${WORKSPACE_OWNER:?WORKSPACE_OWNER ist nicht gesetzt - im Workspace setzt main.tf die Variable}"

P="/home/$WS_OWNER"
L="$P/uni/logs"
SYNC="$P/opencode-coder-env/fernuni-sync"
TOOLS="$SYNC/tools"

mkdir -p "$L"
: > "$L/state-prune.log"
: > "$L/sync-check.log"
echo "=== State-Aufraeumen $(date -Is) ===" >> "$L/state-prune.log"
python3 "$TOOLS/prune_state.py" >> "$L/state-prune.log" 2>&1
echo "prune exit=$?" >> "$L/state-prune.log"
for n in 1 2; do
  echo "=== manueller Sync-Lauf $n: $(date -Is) ===" >> "$L/sync-check.log"
  python3 "$SYNC/sync.py" --env-file "$P/uni/state/fernuni-sync.env" --config "$P/uni/state/fernuni-sync.config.json" >> "$L/sync-check.log" 2>&1
  echo "EXIT$n=$?" >> "$L/sync-check.log"
done
echo "=== beide Laeufe fertig $(date -Is) ===" >> "$L/sync-check.log"
echo READY >> "$L/sync-check.log"
