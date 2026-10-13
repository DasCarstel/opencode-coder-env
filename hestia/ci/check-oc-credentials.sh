#!/usr/bin/env bash
# check-oc-credentials.sh — Regressionstest für die OpenCode-Credential-Kette.
#
# HINTERGRUND (Fehler vom 2026-10-13):
#   OpenCode v2 kann Credentials in opencode.db SPEICHERN (`opencode auth login`).
#   Ein gespeicherter 'OpenCode Go'-Account ÜBERSTIMMT die ENV-Variable
#   OPENCODE_API_KEY. Dann wirkt der Hestia-Kontowechsel (OpenBao 'active' →
#   OPENCODE_API_KEY) NICHT mehr — OpenCode nutzt weiter den gespeicherten Account.
#   Zusätzlich führt `oc-go use` nur die Env-Variable nach, was nichts ändert.
#
# Dieser Check stellt sicher, dass die Kette intakt ist. Er schlägt fehl (=Exit 1),
# sobald eine der Bedingungen verletzt ist:
#   1. Je Container: für die Integration "OpenCode Go" existiert KEIN 'stored'
#      Credential — nur 'environment' (sonst Shadowing → Wechsel wirkungslos).
#   2. OPENCODE_API_KEY in der Container-/.bashrc entspricht dem Key des in OpenBao
#      als 'active' markierten Abos (sha256-Vergleich, keine Key-Ausgabe).
#   3. Mini-Modellaufruf mit dem 'active'-Key antwortet mit HTTP 200 (Account nutzbar,
#      nicht gesperrt/ohne Guthaben).
#
# Aufruf: läuft auf dem Self-hosted Runner (Docker-Host) und zusätzlich am Ende von
# switch-account.sh. Täglich per .github/workflows/verify-oc-credentials.yml.
#
# Env:
#   CODER_SSH_HOST        Default 10.0.10.17
#   CODER_SSH_KEY         Default ~/.ssh/id_ed25519_coderdeploy
#   HESTIA_TARGETS        Default infrastructure|general|muellerconnect
#   OPENBAO_ADDR          Default https://openbao.mueller-nas.de
#   OPENBAO_TOKEN_FILE    Default /opt/docker-compose/github-runner/openbao-switch.token
#   OC_PROBE_MODEL        Default mimo-v2.6-flash
set -uo pipefail

CODER_SSH_HOST="${CODER_SSH_HOST:-10.0.10.17}"
CODER_SSH_KEY="${CODER_SSH_KEY:-$HOME/.ssh/id_ed25519_coderdeploy}"
HESTIA_TARGETS="${HESTIA_TARGETS:-infrastructure|general|muellerconnect}"
OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
OPENBAO_TOKEN_FILE="${OPENBAO_TOKEN_FILE:-/opt/docker-compose/github-runner/openbao-switch.token}"
OC_PROBE_MODEL="${OC_PROBE_MODEL:-mimo-v2.6-flash}"
SECRET_PATH="secret/data/mcp/opencode-go"

SSH=(ssh -i "$CODER_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "root@${CODER_SSH_HOST}")
FAIL=0
fail() { echo "✗ $*" >&2; FAIL=1; }

# --- 1) OpenBao: aktives Abo + dessen Key -------------------------------------
if [[ ! -r "$OPENBAO_TOKEN_FILE" ]]; then
  echo "✗ OpenBao-Token nicht lesbar: $OPENBAO_TOKEN_FILE" >&2
  exit 1
fi
VAULT_TOKEN="$(cat "$OPENBAO_TOKEN_FILE")"
WORK="$(mktemp -d -t oc-cred-check.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

curl -sS -H "X-Vault-Token: $VAULT_TOKEN" "$OPENBAO_ADDR/v1/$SECRET_PATH" > "$WORK/og.json"
ACTIVE="$(python3 -c 'import json,sys;d=(json.load(open(sys.argv[1])).get("data",{}).get("data") or {});print(d.get("active") or "")' "$WORK/og.json")"
ACTIVE_KEY="$(python3 -c 'import json,sys;d=(json.load(open(sys.argv[1])).get("data",{}).get("data") or {});print(d.get(sys.argv[2]) or "")' "$WORK/og.json" "$ACTIVE")"
if [[ -z "$ACTIVE" || -z "$ACTIVE_KEY" ]]; then
  echo "✗ OpenBao: 'active' oder Key leer (active='$ACTIVE')" >&2
  exit 1
fi
echo "→ OpenBao aktiv: $ACTIVE (Key ${#ACTIVE_KEY} Zeichen)"

# --- 2) Mini-Probe mit dem aktiven Key (Account nutzbar?) ---------------------
PROBE_CODE="$(curl -s -o "$WORK/probe.json" -w '%{http_code}' -m 20 \
  -X POST -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ACTIVE_KEY" \
  -H 'x-opencode-session: hestia-check' \
  -H 'User-Agent: Mozilla/5.0' \
  -d "{\"model\":\"$OC_PROBE_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1}" \
  "https://opencode.ai/zen/go/v1/chat/completions")"
if [[ "$PROBE_CODE" == "200" ]]; then
  echo "✓ Probe: aktives Abo $ACTIVE antwortet (HTTP 200)"
else
  fail "Probe für $ACTIVE fehlgeschlagen (HTTP $PROBE_CODE): $(head -c 160 "$WORK/probe.json")"
fi

# --- 3) Je Container: kein gespeicherter Go-Credential + .bashrc-Key == active --
ACTIVE_HASH="$(printf '%s' "$ACTIVE_KEY" | sha256sum | cut -c1-16)"
NAMES="$("${SSH[@]}" "docker ps --format '{{.Names}}' | grep -E '^coder-.*(${HESTIA_TARGETS})\$' || true")"
if [[ -z "$NAMES" ]]; then
  fail "keine passenden Container gefunden (Muster: coder-* ($HESTIA_TARGETS))"
fi
for c in $NAMES; do
  # 3a) Shadowing-Check: In der Integration 'OpenCode Go' darf KEIN 'stored' stehen
  if "${SSH[@]}" "docker exec '$c' sh -lc 'opencode auth list 2>/dev/null'" \
       | grep -E "OpenCode Go" | grep -q "stored"; then
    fail "$c: gespeicherter 'OpenCode Go'-Account vorhanden (überschreibt OPENCODE_API_KEY!) → 'opencode auth logout opencode-go <cred>'"
  else
    echo "✓ $c: keine gespeicherte Go-Credential (nur environment)"
  fi
  # 3b) .bashrc-Key == OpenBao-active-Key (Hash-Vergleich, keine Klartext-Ausgabe)
  RKEY="$("${SSH[@]}" "docker exec '$c' sh -lc 'grep -m1 \"^export OPENCODE_API_KEY=\" /root/.bashrc 2>/dev/null | cut -d= -f2- | tr -d \"\\\"\"'" || true)"
  if [[ -z "$RKEY" ]]; then
    echo "⚠ $c: kein OPENCODE_API_KEY in /root/.bashrc (nur Shell-Marker, kein Fehler)"
  elif [[ "$(printf '%s' "$RKEY" | sha256sum | cut -c1-16)" != "$ACTIVE_HASH" ]]; then
    fail "$c: OPENCODE_API_KEY != OpenBao-active ($ACTIVE) — Wechsel nicht angekommen. 'oc-go use $ACTIVE' + Service-Neustart"
  else
    echo "✓ $c: OPENCODE_API_KEY == aktiv ($ACTIVE)"
  fi
done

if [[ "$FAIL" != "0" ]]; then
  echo "✗ Credential-Check FEHLGESCHLAGEN — siehe Meldungen oben." >&2
  exit 1
fi
echo "✓ Credential-Check ok: OpenCode nutzt das aktive Abo ($ACTIVE) in allen Containern."
