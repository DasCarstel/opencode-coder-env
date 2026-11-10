#!/usr/bin/env bash
# Stellt die nicht versionierten Hermes-Volume-Dateien aus dem Repo wieder her.
# Aufrufen AUF DEM CODER-HOST (10.0.10.17), wo /etc/docker/hermes/data liegt.
# Das Repo liegt dort unter /opt/docker-compose/hermes (Deploy-rsync).
#
# Hintergrund: docs/hermes.md -> "Profile": Volume-Dateien überleben einen
# Volume-Verlust nicht. Diese beiden Dateien sind die Ausnahmen, die wir
# versionieren, weil sie den Facharbeiter-Pfad tragen:
#   - bin/oc-dispatch.py          (Dispatch + Session-Adoption, Fix 2026-11-10)
#   - profiles/kleve/skills/space-matrix/SKILL.md  (Skill des Facharbeiter-Pfads)
#
# Gebrauch:  bash /opt/docker-compose/hermes/restore-volume-overrides.sh

set -euo pipefail

REPO="/opt/docker-compose/hermes"
DATA="/etc/docker/hermes/data"
HERMES_UID=10000

declare -A MAP=(
  ["$REPO/bin/oc-dispatch.py"]="$DATA/bin/oc-dispatch.py:755"
  ["$REPO/profiles/kleve/skills/space-matrix/SKILL.md"]="$DATA/profiles/kleve/skills/space-matrix/SKILL.md:644"
)

for src in "${!MAP[@]}"; do
  dst="${MAP[$src]%:*}"; mode="${MAP[$src]##*:}"
  [[ -f "$src" ]] || { echo "✓ übersprungen (fehlt im Repo): $src"; continue; }
  mkdir -p "$(dirname "$dst")"
  [[ -f "$dst" ]] && cp -a "$dst" "$dst.bak-restore-$(date +%s)"
  install -o "$HERMES_UID" -g "$HERMES_UID" -m "$mode" "$src" "$dst"
  echo "✓ $dst"
done

# Sanity: das Dispatch-Skript muss kompilieren, sonst kein Facharbeiter-Pfad.
if docker exec hermes python3 -m py_compile /opt/data/bin/oc-dispatch.py 2>/dev/null; then
  echo "✓ py_compile OK"
else
  echo "⚠ py_compile FEHLGESCHLAGEN — Restore prüfen!"
  exit 1
fi
echo "Fertig. Ein Hermes-Neustart ist nicht nötig; der Dispatch läuft pro Turn."
