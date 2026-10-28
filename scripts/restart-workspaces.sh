#!/usr/bin/env bash
# restart-workspaces.sh — Coder-Workspaces nach Repo-Änderungen neu starten
# (läuft auf dem Self-hosted Runner auf dem Docker-Host).
#
# Ein plain `docker restart` genügt — KEIN `coder templates push`, KEIN
# Image-Rebuild:
#   - Der Entrypoint jedes Containers zieht beim Start
#     `git fetch --depth 1 origin master && reset --hard` und startet danach
#     `coder-templates/shared/startup.sh` neu — damit sind Änderungen an
#     Spaces, Templates und Skills aus dem Repo sofort aktiv.
#   - Ein `coder templates push` würde über `CACHE_DATE=timestamp()` alle vier
#     Images neu bauen — für Repo-seitige Änderungen reiner Overhead.
#
# `datascience` ist bewusst ausgeschlossen (DinD, heavy) und muss manuell
# neu gestartet werden.
#
# Env:
#   CODER_SSH_HOST      Default 10.0.10.17
#   CODER_SSH_KEY       Default ~/.ssh/id_ed25519_coderdeploy
#   TARGETS             Profil-Regex, Default infrastructure|general|muellerconnect
#   DELAY               Sekunden vor dem Restart (Default 0 — lässt einen
#                       laufenden Agenten-Durchlauf abschließen)
#   WAIT                Sekunden vor dem Status-Check (Default 20)
set -euo pipefail

CODER_SSH_HOST="${CODER_SSH_HOST:-10.0.10.17}"
CODER_SSH_KEY="${CODER_SSH_KEY:-$HOME/.ssh/id_ed25519_coderdeploy}"
TARGETS="${TARGETS:-infrastructure|general|muellerconnect}"
DELAY="${DELAY:-0}"
WAIT="${WAIT:-20}"

SSH=(ssh -i "$CODER_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "root@${CODER_SSH_HOST}")

echo "→ Suche laufende OpenCode-Container (Muster: coder-*(${TARGETS}))"
NAMES=$("${SSH[@]}" "docker ps --format '{{.Names}}' | grep -E '^coder-.*(${TARGETS})\$' || true")
if [ -z "$NAMES" ]; then
  echo "⚠ Keine passenden laufenden Container gefunden — nichts zu tun."
  exit 0
fi
echo "→ Ziele:"
echo "$NAMES" | sed 's/^/   - /'

if [ "$DELAY" -gt 0 ]; then
  echo "→ Warte ${DELAY}s vor dem Restart (laufende Sessions dürfen abschließen)"
  sleep "$DELAY"
fi

for c in $NAMES; do
  echo "→ restart $c"
  "${SSH[@]}" "docker restart '$c' >/dev/null && echo '   ✓ neu gestartet'"
done

echo "→ Kurz warten (${WAIT}s), dann Container-Status prüfen"
sleep "$WAIT"

STATUS=$("${SSH[@]}" "docker ps --format '{{.Names}} {{.Status}}' | grep -E '^coder-.*(${TARGETS}) ' || true")
FAIL=0
for c in $NAMES; do
  LINE="$(printf '%s\n' "$STATUS" | grep "^${c} " || true)"
  if [ -z "$LINE" ]; then
    echo "✗ $c fehlt in 'docker ps' (läuft nicht mehr?)" >&2
    FAIL=1
  elif [[ "$LINE" != *" Up "* ]]; then
    echo "✗ $c ist nicht 'Up' (Status: ${LINE#* })" >&2
    FAIL=1
  fi
done
if [ "$FAIL" -ne 0 ]; then
  echo "RESTART FEHLGESCHLAGEN — mindestens ein Container ist nicht 'Up' (siehe oben)." >&2
  exit 1
fi

echo "→ Fertig: $(echo "$NAMES" | wc -l | tr -d ' ') Container neu gestartet und 'Up':"
echo "$NAMES" | sed 's/^/   - /'
