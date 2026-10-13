#!/usr/bin/env bash
# rollout.sh — Tier-Overrides auf alle Coder-Workspaces ausrollen (läuft auf dem
# Self-hosted Runner auf dem Docker-Host).
#
# Kein Coder-API-Token nötig: der Runner hat per SSH-Deploy-Key Zugang zum
# Coder-Host und startet die OpenCode-Container neu. Deren entrypoint.sh zieht
# beim Start `git fetch --depth 1 origin master && reset --hard` — danach sind
# die neuen Router-Overrides aktiv.
#
# Env:
#   CODER_SSH_HOST      Default 10.0.10.17
#   CODER_SSH_KEY       Default ~/.ssh/id_ed25519_coderdeploy
#   HESTIA_TARGETS      Config-Name-Regex, Default infrastructure|general|muellerconnect
set -euo pipefail

CODER_SSH_HOST="${CODER_SSH_HOST:-10.0.10.17}"
CODER_SSH_KEY="${CODER_SSH_KEY:-$HOME/.ssh/id_ed25519_coderdeploy}"
HESTIA_TARGETS="${HESTIA_TARGETS:-infrastructure|general|muellerconnect}"

SSH=(ssh -i "$CODER_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "root@${CODER_SSH_HOST}")

echo "→ Suche laufende OpenCode-Container (Muster: coder-*(${HESTIA_TARGETS}))"
NAMES=$("${SSH[@]}" "docker ps --format '{{.Names}}' | grep -E '^coder-.*(${HESTIA_TARGETS})\$' || true")
if [ -z "$NAMES" ]; then
  echo "⚠ Keine passenden laufenden Container gefunden — nichts zu tun."
  exit 0
fi
echo "→ Ziele:"
echo "$NAMES" | sed 's/^/   - /'

for c in $NAMES; do
  echo "→ restart $c"
  "${SSH[@]}" "docker restart '$c' >/dev/null && echo '   ✓ neu gestartet'"
done

echo "→ Kurz warten, dann Container-Status prüfen"
sleep 15
"${SSH[@]}" "docker ps --format '{{.Names}} {{.Status}}' | grep -E '^coder-.*(${HESTIA_TARGETS})\$' | sed 's/^/   /'"

echo "ROLLOUT OK — Entrypoints haben das Repo gezogen (Overrides aktiv nach Setup-Lauf)."
