#!/usr/bin/env bash
# sync.sh — Tier-Overrides in die laufenden Coder-Workspaces synchronisieren
# (läuft auf dem Self-hosted Runner auf dem Docker-Host) — OHNE Neustart.
#
# Kein Coder-API-Token nötig: der Runner hat per SSH-Deploy-Key Zugang zum
# Coder-Host. Statt alle Container neu zu starten (wie rollout.sh), ersetzt
# sync.sh pro Container nur die Overrides-Datei per `git show`. Das
# Router-Plugin liest die Datei live neu (Cache-Fingerprint über mtime/size):
# der nächste Prompt fährt bereits das neue Modell — ein laufender Agent wird
# nicht gestört, kein `reset --hard`, kein Entrypoint-Restart.
#
# Env (identisch zu rollout.sh):
#   CODER_SSH_HOST      Default 10.0.10.17
#   CODER_SSH_KEY       Default ~/.ssh/id_ed25519_coderdeploy
#   HESTIA_TARGETS      Config-Name-Regex, Default infrastructure|general|muellerconnect
set -euo pipefail

CODER_SSH_HOST="${CODER_SSH_HOST:-10.0.10.17}"
CODER_SSH_KEY="${CODER_SSH_KEY:-$HOME/.ssh/id_ed25519_coderdeploy}"
HESTIA_TARGETS="${HESTIA_TARGETS:-infrastructure|general|muellerconnect}"

OVERRIDES='.opencode/opencode-model-router.overrides.jsonc'
REPO='/home/carstenmueller2002/opencode-coder-env'

SSH=(ssh -i "$CODER_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "root@${CODER_SSH_HOST}")

echo "→ Suche laufende OpenCode-Container (Muster: coder-*(${HESTIA_TARGETS}))"
NAMES=$("${SSH[@]}" "docker ps --format '{{.Names}}' | grep -E '^coder-.*(${HESTIA_TARGETS})\$' || true")
if [ -z "$NAMES" ]; then
  echo "⚠ Keine passenden laufenden Container gefunden — nichts zu tun."
  exit 0
fi
echo "→ Ziele:"
echo "$NAMES" | sed 's/^/   - /'

FAILURES=0
for c in $NAMES; do
  echo "→ sync $c"
  # Pro Container NUR die eine Overrides-Datei ersetzen (git show > file).
  # Fehlt der Container-Eintrag oder das Repo, meldet der einzelne Lauf klar
  # und der Loop läuft mit den restlichen Zielen weiter.
  if OUT=$("${SSH[@]}" "docker exec '$c' sh -c 'cd $REPO && git fetch --depth 1 origin master && git show FETCH_HEAD:$OVERRIDES > $OVERRIDES.tmp && mv $OVERRIDES.tmp $OVERRIDES'" 2>&1); then
    echo "   ✓ Overrides ersetzt (Plugin lädt beim nächsten Prompt neu)"
    [ -n "$OUT" ] && echo "$OUT" | sed 's/^/   /'
  else
    echo "   ✗ FEHLER bei $c:"
    echo "$OUT" | sed 's/^/   /'
    FAILURES=$((FAILURES + 1))
  fi
done

if [ "$FAILURES" -gt 0 ]; then
  echo "SYNC FEHLERHAFT — $FAILURES von $(echo "$NAMES" | wc -l) Zielen nicht aktualisiert."
  exit 1
fi

echo "SYNC OK — Overrides in allen Containern ersetzt (kein Neustart, kein reset --hard)."
