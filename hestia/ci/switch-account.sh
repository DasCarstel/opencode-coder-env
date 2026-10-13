#!/usr/bin/env bash
# switch-account.sh — OpenCode-Go-Konto auf allen Coder-Workspaces umschalten
# (läuft auf dem Self-hosted Runner auf dem Docker-Host, angestoßen von
# hestia/backend/accounts.py via workflow_dispatch).
#
# 1. Key des Zielkontos aus OpenBao secret/data/mcp/opencode-go lesen
# 2. Key pro Container übertragen und OPENCODE_API_KEY neu setzen
#    (.bashrc: deduppen — genau eine Zeile —, Service-Env setzen, Neustart)
#
# WICHTIG: Das Zielkonto kommt als Workflow-Input ACCOUNT. Hestia hat das Feld
# "active" vorher schon in .opencode/hestia-accounts.jsonc (SOT) geschrieben —
# dieses Skript schreibt NICHTS mehr nach OpenBao. Das dort historisch
# vorhandene Feld "active" bleibt bewusst unangetastet und wird nicht gelesen;
# die SOT ist die einzige Quelle der Wahrheit.
#
# Sicherheit: der Key steht nie im Log, nie in einer Kommandozeile und nie im
# Workflow-Log — er wandert über stdin auf den Coder-Host und wird dort
# sofort nach dem Kopieren wieder gelöscht.
#
# Env:
#   ACCOUNT              Konto-ID (Pflicht, z. B. default2)
#   REASON               Begründung, nur fürs Log
#   OPENBAO_ADDR         Default https://openbao.mueller-nas.de
#   OPENBAO_TOKEN_FILE   Default /opt/docker-compose/github-runner/openbao-switch.token
#   CODER_SSH_HOST       Default 10.0.10.17
#   CODER_SSH_KEY        Default ~/.ssh/id_ed25519_coderdeploy
#   HESTIA_TARGETS       Config-Name-Regex, Default infrastructure|general|muellerconnect
set -euo pipefail

ACCOUNT="${ACCOUNT:-}"
REASON="${REASON:-hestia}"
OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
OPENBAO_TOKEN_FILE="${OPENBAO_TOKEN_FILE:-/opt/docker-compose/github-runner/openbao-switch.token}"
CODER_SSH_HOST="${CODER_SSH_HOST:-10.0.10.17}"
CODER_SSH_KEY="${CODER_SSH_KEY:-$HOME/.ssh/id_ed25519_coderdeploy}"
HESTIA_TARGETS="${HESTIA_TARGETS:-infrastructure|general|muellerconnect}"
SECRET_PATH="secret/data/mcp/opencode-go"
KEY_PATH_REMOTE="/tmp/ocgo_key"

SSH=(ssh -i "$CODER_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "root@${CODER_SSH_HOST}")

# --- 1) Eingabe validieren -----------------------------------------------------
if [[ ! "$ACCOUNT" =~ ^[A-Za-z0-9_-]{1,40}$ ]]; then
  echo "✗ ACCOUNT ungültig (erwartet: ^[A-Za-z0-9_-]{1,40}$)" >&2
  exit 1
fi
if [[ ! -r "$OPENBAO_TOKEN_FILE" ]]; then
  echo "✗ OpenBao-Token nicht lesbar: $OPENBAO_TOKEN_FILE" >&2
  exit 1
fi
VAULT_TOKEN="$(cat "$OPENBAO_TOKEN_FILE")"
echo "→ Konto: $ACCOUNT (Grund: $REASON)"

cleanup() { rm -f "${KEY_PY:-}"; }
trap cleanup EXIT

# --- 2) Key lesen --------------------------------------------------------------
# jq ist auf dem Runner nicht garantiert → python3 (läuft dort ohnehin, s. bootstrap.py).
# Quotiertes Heredoc = garantiert literaler Python-Code; das Feld kommt als argv[1].
KEY_PY="$(mktemp -t ocgo_keyread.XXXXXX.py)"
cat >"$KEY_PY" <<'PY'
import sys, json
try:
    data = (json.load(sys.stdin).get("data") or {}).get("data") or {}
except Exception:
    data = {}
sys.stdout.write(str(data.get(sys.argv[1]) or ""))
PY
KEY="$(curl -sS -H "X-Vault-Token: $VAULT_TOKEN" "$OPENBAO_ADDR/v1/$SECRET_PATH" \
  | python3 "$KEY_PY" "$ACCOUNT")"
if [[ -z "$KEY" ]]; then
  echo "✗ Kein Key für Konto '$ACCOUNT' unter $OPENBAO_ADDR/v1/$SECRET_PATH" >&2
  exit 1
fi
echo "→ Key gelesen (${#KEY} Zeichen, wird nicht ausgegeben)"

# --- 3) Container ermitteln und umstellen --------------------------------------
# Kein OpenBao-Schreibzugriff: die SOT (.opencode/hestia-accounts.jsonc) hat das
# Feld "active" bereits atomar auf ACCOUNT gesetzt. Dieses Skript verteilt nur
# den Key in die Container.
echo "→ Suche laufende OpenCode-Container (Muster: coder-* + HESTIA_TARGETS)"
# Remote-Kommando als literales Heredoc: keine verschachtelten Quotes im Aufruf.
REMOTE_PS="docker ps --format '{{.Names}}' | grep -E '^coder-.*(${HESTIA_TARGETS})\$'"
NAMES="$("${SSH[@]}" "$REMOTE_PS" || true)"
if [[ -z "$NAMES" ]]; then
  echo "⚠ Keine passenden laufenden Container gefunden — SOT steht auf $ACCOUNT."
  exit 0
fi
echo "$NAMES" | sed 's/^/   - /'

COUNT=0
for c in $NAMES; do
  echo "→ $c"
  # Key per stdin auf den Host: nie als Argument, nie im SSH-Log.
  printf '%s' "$KEY" | "${SSH[@]}" "umask 077; cat > '$KEY_PATH_REMOTE'"
  "${SSH[@]}" "docker cp '$KEY_PATH_REMOTE' '$c:$KEY_PATH_REMOTE'"
  # .bashrc IMMER erst deduppen (sed -i) und danach genau EINE Zeile anhängen —
  # kein blindes '>>', sonst sammeln sich über die Wechsel Duplikate an.
  # service.json (/root/.config/opencode/service.json) ist ephemer → nach jedem
  # Container-Neustart wird der Key hier neu gesetzt.
  "${SSH[@]}" "docker exec '$c' sh -lc 'K=\$(cat $KEY_PATH_REMOTE); opencode service set env OPENCODE_API_KEY \"\$K\"; sed -i \"/^export OPENCODE_API_KEY=/d\" /root/.bashrc; printf \"export OPENCODE_API_KEY=%s\n\" \"\$K\" >> /root/.bashrc; opencode service restart >/dev/null 2>&1 || true; rm -f $KEY_PATH_REMOTE'"
  "${SSH[@]}" "rm -f '$KEY_PATH_REMOTE'"
  COUNT=$((COUNT + 1))
done

echo "✓ Konto $ACCOUNT aktiv (SOT + $COUNT Container)"
# --- 4) Verifikation: greift der Wechsel in OpenCode wirklich? ---------------
# Ein in opencode.db GESPEICHERTER 'OpenCode Go'-Account überstimmt die
# ENV-Variable OPENCODE_API_KEY — dann wirkt dieser Wechsel nicht (Fehler vom
# 2026-10-13). check-oc-credentials.sh schlägt in dem Fall fehl; mit --fix wird
# zusätzlich der Key in Service-Env und .bashrc nachgezogen.
if bash "$(dirname "$0")/check-oc-credentials.sh" --fix; then
  echo "✓ Wechsel verifiziert: OpenCode nutzt $ACCOUNT"
else
  echo "✗ Verifikation fehlgeschlagen — gespeicherter Go-Account oder Key nicht angekommen?" >&2
  exit 1
fi
