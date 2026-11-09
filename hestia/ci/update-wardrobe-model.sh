#!/usr/bin/env bash
# update-wardrobe-model.sh — Text-Modell des AI-Wardrobe umschalten.
#
# Angestoßen von hestia/backend/server.py (POST /api/wardrobe/settings) per
# workflow_dispatch (wardrobe-model.yml, läuft auf dem Runner auf dem
# Docker-Host). Das Skript selbst enthält KEINE Secrets — es ändert nur env
# in .env-Dateien (upsert, idempotent) und erstellt die betreffenden Container
# neu. Nur Log-Ausgaben ueber env-Namen und Modell-IDs — keine Key- oder
# DSN-Inhalte.
#
# Was es macht:
#   1. WARDROBE-Container (/opt/docker-compose/wardrobe): AI_TEXT_MODEL +
#      AI_VISION_MODEL auf das gewählte Modell upserten und nur bei echter
#      Änderung `backend` + `worker` neu erstellen (idempotent — ohne Änderung
#      kein Restart).
#   2. wardrobe-shim (/opt/docker-compose/ai-shim): bei Go-Modellen
#      (glm-5.3-flash, gpt-6-luna) GO_MODELS auf beide Go-Modelle setzen
#      (env-upsert) + Shim-Container neu erstellen. Bei Gemini-Modellen wird
#      der Shim bewusst NICHT angefasst: das gewählte Modell ist dann nicht in
#      GO_MODELS und der Shim routet es automatisch direkt auf die
#      Gemini-API (billed) — der Fallback-Pfad bleibt bestehen.
#
# Env:
#   WARDROBE_TEXT_MODEL   Ziel-Text-Modell (Pflicht)
#   REASON                Begründung, nur fürs Log (workflow input)
#   WARDROBE_DIR          Default /opt/docker-compose/wardrobe
#   SHIM_ENV              Default /opt/docker-compose/ai-shim/.env
set -euo pipefail

WARDROBE_TEXT_MODEL="${WARDROBE_TEXT_MODEL:-}"
REASON="${REASON:-hestia}"
WARDROBE_DIR="${WARDROBE_DIR:-/opt/docker-compose/wardrobe}"
SHIM_ENV="${SHIM_ENV:-/opt/docker-compose/ai-shim/.env}"

GO_MODELS_GO="glm-5.3-flash,gpt-6-luna"

# --- 1) Eingabe validieren (Muster switch-account.sh: striktes Regex) ----------
if [[ ! "$WARDROBE_TEXT_MODEL" =~ ^(glm-5\.3-flash|gpt-6-luna|gemini-flash-latest|gemini-4-argon)$ ]]; then
  echo "✗ WARDROBE_TEXT_MODEL ungültig: '$WARDROBE_TEXT_MODEL' "
  echo "  (erlaubt: glm-5.3-flash, gpt-6-luna, gemini-flash-latest, gemini-4-argon)" >&2
  exit 1
fi
echo "→ Wardrobe-Text-Modell: $WARDROBE_TEXT_MODEL (Grund: $REASON)"

# Upsert einer Variable in einer .env: alte Zeile ersetzen, sonst anhängen.
# Wert kommt aus dem validierten Workflow-Input — sed-mäßig ungefährlich.
upsert_env_var() {
  local file="$1" key="$2" value="$3"
  if [[ ! -f "$file" ]]; then
    echo "  ⚠ $file fehlt — kann $key nicht setzen" >&2
    return 1
  fi
  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

# Aktuelle .env-Werte: leer = Variable noch nicht gesetzt.
env_value() {
  local file="$1" key="$2"
  [[ -f "$file" ]] && sed -n "s/^${key}=//p" "$file" | head -n 1 || true
}

# --- 2) Wardrobe-Container: AI_TEXT_MODEL + AI_VISION_MODEL --------------------
WARDROBE_ENV="$WARDROBE_DIR/.env"
if [[ ! -f "$WARDROBE_ENV" ]]; then
  echo "✗ Wardrobe-.env nicht gefunden: $WARDROBE_ENV" >&2
  exit 1
fi
OLD_TEXT="$(env_value "$WARDROBE_ENV" AI_TEXT_MODEL)"
OLD_VISION="$(env_value "$WARDROBE_ENV" AI_VISION_MODEL)"
echo "→ Wardrobe ($WARDROBE_ENV): AI_TEXT_MODEL='${OLD_TEXT}' AI_VISION_MODEL='${OLD_VISION}'"

CHANGED=1
if [[ "$OLD_TEXT" == "$WARDROBE_TEXT_MODEL" && -n "$OLD_TEXT" \
      && "$OLD_VISION" == "$WARDROBE_TEXT_MODEL" && -n "$OLD_VISION" ]]; then
  CHANGED=0
fi

if [[ "$CHANGED" -eq 1 ]]; then
  upsert_env_var "$WARDROBE_ENV" AI_TEXT_MODEL "$WARDROBE_TEXT_MODEL"
  upsert_env_var "$WARDROBE_ENV" AI_VISION_MODEL "$WARDROBE_TEXT_MODEL"
  chmod 600 "$WARDROBE_ENV"
  echo "→ Container-Neustart (backend worker): nur da Modell sich änderte"
  if ( cd "$WARDROBE_DIR" \
       && docker compose -f docker-compose.yml -f docker-compose.override.yml up -d backend worker ); then
    echo "   ✓ Wardrobe backend/worker mit AI_TEXT_MODEL=$WARDROBE_TEXT_MODEL neu erstellt"
  else
    echo "   ⚠ Compose-Restart fehlgeschlagen — .env steht bereit, manuell prüfen" >&2
    exit 1
  fi
else
  echo "   ✓ Modell bereits aktiv — kein Container-Restart"
fi

# --- 3) wardrobe-shim: GO_MODELS für Go-Modelle nachziehen, sonst lassen -------
case "$WARDROBE_TEXT_MODEL" in
  glm-5.3-flash|gpt-6-luna)
    if [[ -f "$SHIM_ENV" ]]; then
      echo "→ Wardrobe-Shim: GO_MODELS=$GO_MODELS_GO ($SHIM_ENV)"
      upsert_env_var "$SHIM_ENV" GO_MODELS "$GO_MODELS_GO"
      chmod 600 "$SHIM_ENV"
      if docker compose -f /opt/docker-compose/ai-shim/docker-compose.yml up -d --force-recreate ai-shim >/dev/null 2>&1; then
        echo "   ✓ Shim neu erstellt (Go-Modell läuft über das Go-Abo)"
      else
        echo "   ⚠ Shim-Restart fehlgeschlagen — GO_MODELS steht in $SHIM_ENV" >&2
        exit 1
      fi
    else
      echo "⚠ Shim-.env nicht gefunden ($SHIM_ENV) — Modell läuft dann automatisch per Gemini-Fallback" >&2
    fi
    ;;
  *)
    # Gemini-Modelle: Shim anlassen — das gewählte Modell ist nicht in
    # GO_MODELS, der Shim routet es direkt auf die Gemini-API (billed).
    echo "→ Gemini-Modell gewählt — Shim unverändert (Modell nicht in GO_MODELS → direkt Gemini)"
    ;;
esac

echo "✓ Wardrobe-Text-Modell aktiv: $WARDROBE_TEXT_MODEL"
