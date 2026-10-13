#!/usr/bin/env bash
# check-oc-credentials.sh — Regressionstest + Selbstheilung für die
#                            OpenCode-Credential-Kette.
#
# HINTERGRUND (Fehler vom 2026-10-13):
#   OpenCode v2 kann Credentials in opencode.db SPEICHERN (`opencode auth login`).
#   Ein gespeicherter 'OpenCode Go'-Account ÜBERSTIMMT die ENV-Variable
#   OPENCODE_API_KEY. Dann wirkt der Hestia-Kontowechsel NICHT mehr — OpenCode
#   nutzt weiter den gespeicherten Account.
#   Zusätzlich ist /root/.config/opencode/service.json EPHEMER: nach einem
#   Container-Neustart fehlt OPENCODE_API_KEY im Service-Env.
#
# QUELLE DER WAHRHEIT: das Feld "active" in .opencode/hestia-accounts.jsonc (SOT).
#   Hestia (accounts.py → switch_account) pflegt es; dieses Skript liest es nur.
#   Das gleichnamige Feld "active" im OpenBao-Secret secret/data/mcp/opencode-go
#   wird NICHT mehr gelesen — es bleibt nur historisch in OpenBao bestehen.
#
# Geprüft wird (Exit 1 bei Verstoß):
#   1. Je Container: für die Integration "OpenCode Go" existiert KEIN 'stored'
#      Credential — nur 'environment' (sonst Shadowing → Wechsel wirkungslos).
#   2. OPENCODE_API_KEY in der Container-/.bashrc entspricht dem Key des in der
#      SOT als 'active' markierten Abos (sha256-Vergleich, keine Key-Ausgabe).
#   3. Der Key steht im Service-Env (service.json) des Containers.
#   4. Mini-Modellaufruf mit dem 'active'-Key antwortet mit HTTP 200 (Account
#      nutzbar, nicht gesperrt/ohne Guthaben).
#
# Aufruf: läuft auf dem Self-hosted Runner (Docker-Host), am Ende von
# switch-account.sh und alle 30 min per .github/workflows/verify-oc-credentials.yml.
#
#   --fix   Selbstheilung: fehlenden/abweichenden Key in Service-Env und
#           /root/.bashrc (deduppt) nachziehen, Service neu starten, neu prüfen.
#           Ohne --fix wird nur geprüft (Default, für Diagnose/CI manuell).
#
# Env:
#   CODER_SSH_HOST        Default 10.0.10.17
#   CODER_SSH_KEY         Default ~/.ssh/id_ed25519_coderdeploy
#   HESTIA_TARGETS        Default infrastructure|general|muellerconnect
#   HESTIA_SOT_PATH       Default <repo>/.opencode/hestia-accounts.jsonc
#   OPENBAO_ADDR          Default https://openbao.mueller-nas.de
#   OPENBAO_TOKEN_FILE    Default /opt/docker-compose/github-runner/openbao-switch.token
#   OC_PROBE_MODEL        Default mimo-v2.6-flash
set -uo pipefail

FIX=0
for arg in "$@"; do
  case "$arg" in
    --fix) FIX=1 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "✗ Unbekanntes Argument: $arg" >&2; exit 2 ;;
  esac
done

CODER_SSH_HOST="${CODER_SSH_HOST:-10.0.10.17}"
CODER_SSH_KEY="${CODER_SSH_KEY:-$HOME/.ssh/id_ed25519_coderdeploy}"
HESTIA_TARGETS="${HESTIA_TARGETS:-infrastructure|general|muellerconnect}"
OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
OPENBAO_TOKEN_FILE="${OPENBAO_TOKEN_FILE:-/opt/docker-compose/github-runner/openbao-switch.token}"
OC_PROBE_MODEL="${OC_PROBE_MODEL:-mimo-v2.6-flash}"
SECRET_PATH="secret/data/mcp/opencode-go"
# SOT: Repo-Root ist zwei Ebenen über hestia/ci/.
SOT_PATH="${HESTIA_SOT_PATH:-$(cd "$(dirname "$0")/../.." && pwd)/.opencode/hestia-accounts.jsonc}"

SSH=(ssh -i "$CODER_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "root@${CODER_SSH_HOST}")
FAIL=0
FIXED=0
fail() { echo "✗ $*" >&2; FAIL=1; }

# --- 0) SOT: aktives Abo bestimmen (nicht aus OpenBao!) -----------------------
if [[ ! -r "$SOT_PATH" ]]; then
  echo "✗ SOT nicht lesbar: $SOT_PATH" >&2
  exit 1
fi
ACTIVE="$(python3 -c '
import json, re, sys
raw = open(sys.argv[1], encoding="utf-8").read()
try:
    import hjsonc
    cfg = hjsonc.parse(raw) or {}
except Exception:
    # Fallback ohne hjsonc: nur Kommentare entfernen ("//" mit Whitespace davor —
    # ein "//" in einer URL wie https://... ist durch den Doppelpunkt geschützt).
    raw = re.sub(r"^[ \t]*//.*$|[ \t]+//.*$", "", raw, flags=re.M)
    cfg = json.loads(re.sub(r",(\s*[}\]])", r"\1", raw))
print(str(cfg.get("active") or ""))
' "$SOT_PATH")"
if [[ -z "$ACTIVE" ]]; then
  echo "✗ SOT ($SOT_PATH) enthält kein 'active'" >&2
  exit 1
fi
echo "→ SOT $SOT_PATH: active=$ACTIVE"

# --- 1) OpenBao: Key des aktiven Kontos lesen (nur lesend) -------------------
if [[ ! -r "$OPENBAO_TOKEN_FILE" ]]; then
  echo "✗ OpenBao-Token nicht lesbar: $OPENBAO_TOKEN_FILE" >&2
  exit 1
fi
VAULT_TOKEN="$(cat "$OPENBAO_TOKEN_FILE")"
WORK="$(mktemp -d -t oc-cred-check.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

curl -sS -H "X-Vault-Token: $VAULT_TOKEN" "$OPENBAO_ADDR/v1/$SECRET_PATH" > "$WORK/og.json"
ACTIVE_KEY="$(python3 -c 'import json,sys;d=(json.load(open(sys.argv[1])).get("data",{}).get("data") or {});print(d.get(sys.argv[2]) or "")' "$WORK/og.json" "$ACTIVE")"
if [[ -z "$ACTIVE_KEY" ]]; then
  echo "✗ OpenBao: kein Key für SOT-Account '$ACTIVE' ($SECRET_PATH)" >&2
  exit 1
fi
echo "→ OpenBao: Key für $ACTIVE gelesen (${#ACTIVE_KEY} Zeichen, wird nicht ausgegeben)"

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

# --- 3) Je Container: kein gespeicherter Go-Credential, Key in .bashrc + service.json
ACTIVE_HASH="$(printf '%s' "$ACTIVE_KEY" | sha256sum | cut -c1-16)"
KEY_PATH_REMOTE="/tmp/ocgo_key_fix"
hash_of() { printf '%s' "$1" | sha256sum | cut -c1-16; }
NAMES="$("${SSH[@]}" "docker ps --format '{{.Names}}' | grep -E '^coder-.*(${HESTIA_TARGETS})\$' || true")"
if [[ -z "$NAMES" ]]; then
  fail "keine passenden Container gefunden (Muster: coder-* ($HESTIA_TARGETS))"
fi

# Self-healing: Key des SOT-Accounts in service.json + .bashrc (deduppt) setzen.
# service.json ist ephemer → genau das holen wir hier nach einem Neustart nach.
fix_container() {
  local c="$1"
  echo "→ heal $c: Key des SOT-Accounts ($ACTIVE) in Service-Env + .bashrc setzen"
  printf '%s' "$ACTIVE_KEY" | "${SSH[@]}" "umask 077; cat > '$KEY_PATH_REMOTE'"
  "${SSH[@]}" "docker cp '$KEY_PATH_REMOTE' '$c:$KEY_PATH_REMOTE'"
  # sed -i löscht ALLE alten Zeilen zuerst, danach kommt genau eine neue dazu
  # (kein blindes '>>' — sonst sammeln sich Duplikate im .bashrc an).
  "${SSH[@]}" "docker exec '$c' sh -lc 'K=\$(cat $KEY_PATH_REMOTE); \
      opencode service set env OPENCODE_API_KEY \"\$K\" >/dev/null 2>&1; \
      sed -i \"/^export OPENCODE_API_KEY=/d\" /root/.bashrc; \
      printf \"export OPENCODE_API_KEY=%s\n\" \"\$K\" >> /root/.bashrc; \
      opencode service restart >/dev/null 2>&1 || true; \
      rm -f $KEY_PATH_REMOTE'"
  "${SSH[@]}" "rm -f '$KEY_PATH_REMOTE'"
  FIXED=$((FIXED + 1))
}

for c in $NAMES; do
  # 3a) Shadowing-Check: In der Integration 'OpenCode Go' darf KEIN 'stored' stehen
  if "${SSH[@]}" "docker exec '$c' sh -lc 'opencode auth list 2>/dev/null'" \
       | grep -E "OpenCode Go" | grep -q "stored"; then
    fail "$c: gespeicherter 'OpenCode Go'-Account vorhanden (überschreibt OPENCODE_API_KEY!) → 'opencode auth logout opencode-go <cred>'"
  else
    echo "✓ $c: keine gespeicherte Go-Credential (nur environment)"
  fi

  # 3b) Key in Service-Env (service.json) und /root/.bashrc == SOT-Key?
  #     Beide Werte werden nur als Hash verglichen, nie im Klartext ausgegeben.
  BASHRC_KEY="$("${SSH[@]}" "docker exec '$c' sh -lc 'grep -m1 \"^export OPENCODE_API_KEY=\" /root/.bashrc 2>/dev/null | cut -d= -f2- | tr -d \"\\\"\\047\"'" || true)"
  SVC_KEY="$("${SSH[@]}" "docker exec '$c' sh -lc 'tr \",\" \"\n\" < /root/.config/opencode/service.json 2>/dev/null | grep \"OPENCODE_API_KEY\" | head -n1 | cut -d\\\" -f4'" || true)"
  NEED_FIX=0
  if [[ -z "$SVC_KEY" ]]; then
    echo "⚠ $c: kein OPENCODE_API_KEY im Service-Env (service.json)"
    NEED_FIX=1
  elif [[ "$(hash_of "$SVC_KEY")" != "$ACTIVE_HASH" ]]; then
    echo "⚠ $c: Service-Env-Key != SOT-aktiv ($ACTIVE)"
    NEED_FIX=1
  fi
  if [[ -z "$BASHRC_KEY" ]]; then
    echo "⚠ $c: kein OPENCODE_API_KEY in /root/.bashrc"
    NEED_FIX=1
  elif [[ "$(hash_of "$BASHRC_KEY")" != "$ACTIVE_HASH" ]]; then
    echo "⚠ $c: .bashrc-Key != SOT-aktiv ($ACTIVE) — Wechsel nicht angekommen"
    NEED_FIX=1
  fi

  if [[ "$NEED_FIX" == "1" ]]; then
    if [[ "$FIX" == "1" ]]; then
      fix_container "$c"
    else
      fail "$c: OPENCODE_API_KEY weicht vom SOT-aktiven Konto ($ACTIVE) ab (Service-Env/.bashrc)"
      echo "   (nur geprüft — mit --fix würde hier der Key nachgezogen)"
    fi
  else
    # Duplikate im .bashrc: der erste Treffer kann richtig sein, trotzdem ist die
    # Datei kaputt (siehe die sed-Deduplizierung im Fix-Pfad).
    DUPS="$("${SSH[@]}" "docker exec '$c' sh -lc 'grep -c \"^export OPENCODE_API_KEY=\" /root/.bashrc 2>/dev/null || echo 0'" || echo 0)"
    if [[ "${DUPS:-0}" -gt 1 ]]; then
      if [[ "$FIX" == "1" ]]; then
        echo "→ heal $c: $DUPS OPENCODE_API_KEY-Zeilen in /root/.bashrc deduppen"
        fix_container "$c"
      else
        fail "$c: $DUPS OPENCODE_API_KEY-Zeilen in /root/.bashrc (Duplikate — '--fix' dedupliziert)"
      fi
    else
      echo "✓ $c: OPENCODE_API_KEY == SOT-aktiv ($ACTIVE) in Service-Env und .bashrc"
    fi
  fi
done

if [[ "$FIX" == "1" && "$FIXED" != "0" ]]; then
  echo "→ $FIXED Container geheilt — jetzt erneut prüfen"
  RECHECK_FAIL=0
  for c in $NAMES; do
    BASHRC_KEY="$("${SSH[@]}" "docker exec '$c' sh -lc 'grep -m1 \"^export OPENCODE_API_KEY=\" /root/.bashrc 2>/dev/null | cut -d= -f2- | tr -d \"\\\"\\047\"'" || true)"
    SVC_KEY="$("${SSH[@]}" "docker exec '$c' sh -lc 'tr \",\" \"\n\" < /root/.config/opencode/service.json 2>/dev/null | grep \"OPENCODE_API_KEY\" | head -n1 | cut -d\\\" -f4'" || true)"
    if [[ -n "$BASHRC_KEY" && -n "$SVC_KEY" \
          && "$(hash_of "$BASHRC_KEY")" == "$ACTIVE_HASH" \
          && "$(hash_of "$SVC_KEY")" == "$ACTIVE_HASH" ]]; then
      DUPS="$("${SSH[@]}" "docker exec '$c' sh -lc 'grep -c \"^export OPENCODE_API_KEY=\" /root/.bashrc 2>/dev/null || echo 0'" || echo 0)"
      if [[ "${DUPS:-0}" -gt 1 ]]; then
        echo "✗ $c: nach dem Fix noch $DUPS OPENCODE_API_KEY-Zeilen in /root/.bashrc" >&2
        RECHECK_FAIL=1
      else
        echo "✓ $c: nach dem Fix auf SOT-aktiv ($ACTIVE), .bashrc dedupliziert"
      fi
    else
      echo "✗ $c: auch nach dem Fix falsch" >&2
      RECHECK_FAIL=1
    fi
  done
  if [[ "$RECHECK_FAIL" == "1" ]]; then FAIL=1; fi
fi

if [[ "$FAIL" != "0" ]]; then
  echo "✗ Credential-Check FEHLGESCHLAGEN — siehe Meldungen oben." >&2
  exit 1
fi
if [[ "$FIX" == "1" ]]; then
  echo "✓ Credential-Check ok (--fix): OpenCode nutzt das SOT-aktive Abo ($ACTIVE) in allen Containern."
else
  echo "✓ Credential-Check ok: OpenCode nutzt das SOT-aktive Abo ($ACTIVE) in allen Containern."
fi
