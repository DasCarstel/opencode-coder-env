#!/bin/bash
# shared/startup.sh – Gemeinsames Setup für alle OpenCode-Workspaces
#
# Aufruf: startup.sh <profil>
#   infrastructure – HA + Server Management (ssh-mcp, authentik, HA, oCIS privat)
#   general        – Allgemeine Aufgaben (oCIS privat + Carsten-PC via ssh-mcp)
#   muellerconnect – MuellerConnect Mini-Job (nur oCIS Arbeit)
#   datascience    – FernUni Data Engineering (Docker-in-Docker, FernUni-oCIS, Moodle-Sync)
#
# Gemeinsam: OpenBao-Auth, Secrets, Git-Credentials, OpenCode V2, oCIS-MCP,
# Herdr, Env-Injektion. Profil-spezifisch: Secrets, MCP-Config, Skills,
# Herdr-Workspaces.

set -uo pipefail

PROFILE="${1:-}"
case "$PROFILE" in
  infrastructure|general|muellerconnect|datascience) ;;
  *) echo "ERROR: Profil angeben: infrastructure|general|muellerconnect|datascience"; exit 1 ;;
esac

# Profil-Trennung (Least Privilege): general nutzt die dedizierte AppRole
# 'mcp-general', datascience die 'mcp-uni' — nicht die gemeinsame 'mcp-server'
# (die alle secret/data/mcp/* lesen darf). Ohne dedizierte Role-ID bricht der
# Start ab (fail-closed), damit diese Spaces niemals versehentlich alle
# mcp-Secrets sehen.
case "$PROFILE" in
  general|datascience)
    if [ -z "${OPENBAO_ROLE_ID:-}" ] || [ "$OPENBAO_ROLE_ID" = "mcp-server" ]; then
      if [ "$PROFILE" = "general" ]; then ROLE_NAME="mcp-general"; else ROLE_NAME="mcp-uni"; fi
      echo "ERROR: Profil $PROFILE benötigt die dedizierte AppRole '$ROLE_NAME'."
      echo "       OPENBAO_ROLE_ID ist leer oder 'mcp-server'. Bitte die Role-ID aus"
      echo "       scripts/bootstrap-hosts.sh im $PROFILE-Template setzen."
      exit 1
    fi
    ;;
  *)
    OPENBAO_ROLE_ID="${OPENBAO_ROLE_ID:-mcp-server}"
    ;;
esac

# ── PATH sicherstellen ─────────────────────────────────────────────────
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.local/bin:$PATH"
grep -q '/root/.local/bin' /root/.bashrc 2>/dev/null || echo 'export PATH="/root/.local/bin:$PATH"' >> /root/.bashrc
mkdir -p /root/.local/bin /usr/local/bin

OPENBAO_ADDR="https://openbao.mueller-nas.de"
# OPENBAO_ROLE_ID wird oben profilabhängig gesetzt (general → mcp-general).
HERDR_BIN="/usr/local/bin/herdr"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Versions-Pins laden (repo-gesteuert; Anzeige: scripts/check-updates.sh) ─
if [ -f "$SCRIPT_DIR/../versions.env" ]; then
  # shellcheck source=../versions.env
  source "$SCRIPT_DIR/../versions.env"
else
  echo "⚠ coder-templates/versions.env nicht gefunden – Versionspins nicht verfügbar"
  SSH_MCP_VERSION=""
  OCIS_MCP_VERSION=""
  GRAFANA_MCP_VERSION=""
  HERDR_VERSION=""
fi
# ── Hintergrund-Setup (Kaltstart-Verkürzung) ────────────────────────────────
# Die Installationen (OpenCode/ssh-mcp/DCP, uv, ocis-mcp-server, Herdr) hängen
# nicht voneinander ab und brauchen nur Netz – deshalb laufen sie parallel
# statt nacheinander. Der Barrier ist wait_bg() vor der Env-Injektion, also vor
# dem ersten Schritt, der die OpenCode-CLI wirklich benutzt.
#
# ACHTUNG: die npm-Aufrufe bleiben in EINEM Job (npm_chain) hintereinander –
# npm -g verträgt keine Parallelität (gemeinsames /usr/lib/node_modules).
BG_PIDS=()
BG_LABELS=()
# Startzeit des Setups für die Dauer-Meldung am Ende (Messbasis).
SETUP_T0=$SECONDS
# Zeitmarken im Setup-Log: macht die Anteile der Startdauer direkt
# ablesbar (kostet nichts, schreibt nur eine Zeile).
STAMP() { printf '  [%3ss] %s\n' "$((SECONDS-SETUP_T0))" "$1"; }
bg() {   # bg <Label> <Logdatei> <Befehl…>
  local label="$1" log="$2"; shift 2
  ( "$@" ) >>"$log" 2>&1 &
  BG_PIDS+=("$!")
  BG_LABELS+=("$label")
  echo "  → ${label}: läuft im Hintergrund (Log: ${log})"
}
wait_bg() {   # alle Hintergrund-Jobs einsammeln und Ergebnis melden
  local i
  for i in "${!BG_PIDS[@]}"; do
    if wait "${BG_PIDS[$i]}"; then
      echo "  ✓ ${BG_LABELS[$i]}: fertig"
    else
      echo "  ⚠ ${BG_LABELS[$i]}: mit Fehler beendet"
    fi
  done
  BG_PIDS=(); BG_LABELS=()
}

# ssh-mcp wird nur von infrastructure und general gebraucht.
case "$PROFILE" in infrastructure|general) WANT_SSH_MCP=1 ;; *) WANT_SSH_MCP=0 ;; esac

# mcp-grafana wird nur vom infrastructure-Profil gebraucht (Grafana-Space).
case "$PROFILE" in infrastructure) WANT_GRAFANA_MCP=1 ;; *) WANT_GRAFANA_MCP=0 ;; esac

# OpenCode-CLI + ssh-mcp + DCP. OpenCode bleibt bewusst ungepinnt (npm-latest,
# Bake-Baseline siehe versions.env/Dockerfile); ssh-mcp und DCP sind gepinnt
# und werden über Stempel-Dateien in /root/.local/bin geprüft – genau die
# Stempel setzt auch das Dockerfile beim Bake, so dass hier im Normalfall
# nichts zu tun bleibt.
npm_chain() {
  local cur new got stamp
  if ! command -v npm >/dev/null 2>&1; then
    echo "  ⚠ npm nicht verfügbar – OpenCode kann nicht installiert werden"
    return 0
  fi

  cur=$(opencode --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  new=$(npm view @opencode/cli version 2>/dev/null | tail -1)
  if [ -z "$cur" ] || { [ -n "$new" ] && [ "$cur" != "$new" ]; }; then
    echo "  → OpenCode installieren/aktualisieren: ${cur:-fehlt} → ${new:-latest} ..."
    npm uninstall -g opencode 2>/dev/null || true
    npm uninstall -g @opencode-ai/opencode 2>/dev/null || true
    rm -f /usr/bin/opencode
    npm install -g @opencode/cli 2>&1 | tail -3
    got=$(opencode --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [ -n "$got" ]; then
      echo "  ✓ OpenCode: $(opencode --version 2>&1)"
      # Bewusst KEIN Service-Restart hier: der eine Restart am Ende
      # (OpenCode-Server-Binding) nimmt die neue Version mit.
    else
      echo "  ⚠ OpenCode-Installation fehlgeschlagen: $(opencode --version 2>&1)"
    fi
  elif [ -z "$new" ]; then
    echo "  ⚠ npm-Abfrage fehlgeschlagen – OpenCode ${cur:-?} bleibt bestehen"
  else
    echo "  ✓ OpenCode bereits aktuell: $(opencode --version 2>&1)"
  fi

  if [ "$WANT_SSH_MCP" = "1" ]; then
    stamp="$HOME/.local/bin/.ssh-mcp-version"
    if [ -z "${SSH_MCP_VERSION}" ]; then
      echo "  ⚠ SSH_MCP_VERSION nicht gesetzt – ssh-mcp läuft weiter in aktueller Version"
    elif [ "$(cat "$stamp" 2>/dev/null)" != "${SSH_MCP_VERSION}" ]; then
      echo "  → ssh-mcp ${SSH_MCP_VERSION} installieren..."
      if npm install -g "ssh-mcp@${SSH_MCP_VERSION}" >/tmp/npm-ssh-mcp.log 2>&1; then
        tail -2 /tmp/npm-ssh-mcp.log
        echo "${SSH_MCP_VERSION}" > "$stamp"
        echo "  ✓ ssh-mcp ${SSH_MCP_VERSION} installiert"
      else
        tail -3 /tmp/npm-ssh-mcp.log
        echo "  ⚠ ssh-mcp ${SSH_MCP_VERSION} konnte nicht installiert werden – bisherige Version bleibt"
      fi
    else
      echo "  ✓ ssh-mcp bereits auf Pin (${SSH_MCP_VERSION})"
    fi
  fi

  stamp="$HOME/.local/bin/.dcp-version"
  if [ -z "${DCP_VERSION:-}" ]; then
    echo "  ⚠ DCP_VERSION nicht gesetzt"
  elif [ "$(cat "$stamp" 2>/dev/null)" != "${DCP_VERSION}" ]; then
    echo "  → DCP ${DCP_VERSION} installieren..."
    if npm install -g "@tarquinen/opencode-dcp@${DCP_VERSION}" >/tmp/npm-dcp.log 2>&1; then
      tail -2 /tmp/npm-dcp.log
      echo "${DCP_VERSION}" > "$stamp"
      echo "  ✓ DCP ${DCP_VERSION} installiert"
    else
      tail -3 /tmp/npm-dcp.log
      echo "  ⚠ DCP konnte nicht installiert werden"
    fi
  else
    echo "  ✓ DCP bereits auf Pin (${DCP_VERSION})"
  fi
}

# uv/uvx – von uvx-basierten MCP-Servern (authentik) gebraucht.
install_uv() {
  if [ -x "$HOME/.local/bin/uvx" ]; then
    echo "  ✓ uv/uvx bereits vorhanden"
  else
    echo "  → uv/uvx installieren..."
    curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -2 || true
  fi
}

# oCIS MCP Server (OpenCloud / Obsidian), gepinntes Go-Binary.
install_ocis_mcp() {
  local bin="/root/.local/bin/ocis-mcp-server"
  local stamp="/root/.local/bin/.ocis-mcp-server-version"
  if [ -z "${OCIS_MCP_VERSION}" ]; then
    echo "  ⚠ OCIS_MCP_VERSION nicht gesetzt – ocis-mcp-server läuft weiter in aktueller Version"
  elif [ ! -x "$bin" ] || [ "$(cat "$stamp" 2>/dev/null)" != "${OCIS_MCP_VERSION}" ]; then
    echo "  → lade ocis-mcp-server v${OCIS_MCP_VERSION} herunter..."
    if curl -fsSL -o /tmp/ocis-mcp.tar.gz \
        "https://github.com/owncloud/ocis-mcp-server/releases/download/v${OCIS_MCP_VERSION}/ocis-mcp-server_${OCIS_MCP_VERSION}_linux_amd64.tar.gz" \
       && tar xzf /tmp/ocis-mcp.tar.gz -C /tmp \
       && install -m 0755 /tmp/ocis-mcp-server "$bin" \
       && rm -f /tmp/ocis-mcp.tar.gz /tmp/ocis-mcp-server; then
      echo "${OCIS_MCP_VERSION}" > "$stamp"
      echo "  ✓ ocis-mcp-server installiert (v${OCIS_MCP_VERSION})"
    else
      echo "  ⚠ ocis-mcp-server konnte nicht auf v${OCIS_MCP_VERSION} gebracht werden – alte Version (falls vorhanden) bleibt"
    fi
  else
    echo "  ✓ ocis-mcp-server bereits auf Pin (v${OCIS_MCP_VERSION})"
  fi
}

# Grafana MCP Server (grafana/mcp-grafana), gepinntes Go-Binary.
install_mcp_grafana() {
  local bin="/root/.local/bin/mcp-grafana"
  local stamp="/root/.local/bin/.mcp-grafana-version"
  local arch
  case "$(uname -m)" in
    x86_64)        arch="x86_64" ;;
    aarch64|arm64) arch="arm64" ;;
    *)
      echo "  ⚠ mcp-grafana: nicht unterstützte Architektur $(uname -m) – Installation übersprungen"
      return 0
      ;;
  esac
  if [ -z "${GRAFANA_MCP_VERSION}" ]; then
    echo "  ⚠ GRAFANA_MCP_VERSION nicht gesetzt – mcp-grafana läuft weiter in aktueller Version"
  elif [ ! -x "$bin" ] || [ "$(cat "$stamp" 2>/dev/null)" != "${GRAFANA_MCP_VERSION}" ]; then
    echo "  → lade mcp-grafana v${GRAFANA_MCP_VERSION} herunter..."
    if curl -fsSL -o /tmp/mcp-grafana.tar.gz \
        "https://github.com/grafana/mcp-grafana/releases/download/v${GRAFANA_MCP_VERSION}/mcp-grafana_Linux_${arch}.tar.gz" \
       && tar xzf /tmp/mcp-grafana.tar.gz -C /tmp \
       && install -m 0755 /tmp/mcp-grafana "$bin" \
       && rm -f /tmp/mcp-grafana.tar.gz /tmp/mcp-grafana; then
      echo "${GRAFANA_MCP_VERSION}" > "$stamp"
      echo "  ✓ mcp-grafana installiert (v${GRAFANA_MCP_VERSION})"
    else
      echo "  ⚠ mcp-grafana konnte nicht auf v${GRAFANA_MCP_VERSION} gebracht werden – alte Version (falls vorhanden) bleibt"
    fi
  else
    echo "  ✓ mcp-grafana bereits auf Pin (v${GRAFANA_MCP_VERSION})"
  fi
}

# Herdr: Quelle GitHub-Release-Asset, Fallback Host-Bind-Mount /opt/opencode-bin.
herdrver() { "$1" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1; }
install_herdr() {
  local cur hostbin="/opt/opencode-bin/herdr"
  cur=$(herdrver "$HERDR_BIN")
  if [ -z "${HERDR_VERSION}" ]; then
    echo "  ⚠ HERDR_VERSION nicht gesetzt – Herdr läuft weiter in Version ${cur:-?}"
  elif [ "$cur" = "${HERDR_VERSION}" ]; then
    echo "  ✓ Herdr bereits auf Pin (${HERDR_VERSION})"
  elif [ -x "$hostbin" ] && [ "$(herdrver "$hostbin")" = "${HERDR_VERSION}" ]; then
    cp "$hostbin" "$HERDR_BIN" \
      && chmod +x "$HERDR_BIN" \
      && echo "  ✓ Herdr ${HERDR_VERSION} aus /opt/opencode-bin installiert" \
      || echo "  ⚠ Herdr aus /opt/opencode-bin konnte nicht installiert werden"
  else
    # Release-Assets liegen auf GitHub als herdr-linux-<arch>. Die frühere URL
    # herdr.dev/api/releases/... liefert nur eine HTML-Seite (~51 KB) – der
    # Download schlug damit still fehl und fiel auf /opt/opencode-bin (alte
    # Version) zurück.
    local asset="herdr-linux-x86_64"
    case "$(uname -m)" in aarch64|arm64) asset="herdr-linux-aarch64" ;; esac
    echo "  → Herdr ${HERDR_VERSION} wird heruntergeladen (GitHub)..."
    if curl -fsSL --max-time 120 -o "$HERDR_BIN.tmp" \
        "https://github.com/herdrdev/herdr/releases/download/v${HERDR_VERSION}/${asset}" \
       && chmod +x "$HERDR_BIN.tmp" \
       && [ "$(herdrver "$HERDR_BIN.tmp")" = "${HERDR_VERSION}" ]; then
      mv "$HERDR_BIN.tmp" "$HERDR_BIN"
      echo "  ✓ Herdr installiert (${HERDR_VERSION})"
    else
      rm -f "$HERDR_BIN.tmp"
      if [ -x "$hostbin" ]; then
        cp "$hostbin" "$HERDR_BIN" && chmod +x "$HERDR_BIN" \
          && echo "  ⚠ Download fehlgeschlagen – bleibe bei Version $(herdrver "$HERDR_BIN") aus /opt/opencode-bin" \
          || echo "  ⚠ Download fehlgeschlagen und Host-Fallback nicht installierbar"
      else
        echo "  ⚠ Herdr ${HERDR_VERSION} weder per Download noch Host-Fallback verfügbar"
      fi
    fi
  fi
}

# Tailscale (nur infrastructure, Cleve-Zugang). Läuft bewusst als NICHT
# getrackter Job: nichts im Setup hängt davon ab, und ein hängender
# Auth-Vorgang darf den Start nicht aufhalten.
TS_SOCK="/var/run/tailscale/tailscaled.sock"
setup_tailscale() {
  if [ -z "${TS_AUTHKEY:-}" ]; then
    echo "  ⚠ TS_AUTHKEY nicht gesetzt (secret/data/mcp/tailscale)"
    return 0
  fi
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "  → Tailscale wird installiert..."
    curl -fsSL https://tailscale.com/install.sh | sh 2>&1 | tail -2 || true
  fi
  command -v tailscale >/dev/null 2>&1 || { echo "  ⚠ Tailscale nicht verfügbar"; return 0; }
  mkdir -p /var/lib/tailscale /var/run/tailscale
  if ! tailscale --socket="$TS_SOCK" status >/dev/null 2>&1; then
    echo "  → tailscaled wird gestartet..."
    nohup tailscaled --state=/var/lib/tailscale/tailscaled.state --socket="$TS_SOCK" \
      > /tmp/tailscaled.log 2>&1 &
    # auf den Socket warten statt blind sleep 3
    local i
    for i in $(seq 1 20); do
      [ -S "$TS_SOCK" ] && break
      sleep 0.5
    done
  fi
  # Timeout bewusst 10 s statt 30 s: Tailscale ist nur für die Kleve-Hosts
  # nötig und darf den Start nicht ausbremsen.
  tailscale --socket="$TS_SOCK" up \
    --authkey="$TS_AUTHKEY" \
    --hostname="coder-${PROFILE}" \
    --accept-routes \
    --accept-dns=false \
    --ssh=false \
    --timeout=10s 2>&1 | tail -3 || echo "  ⚠ tailscale up fehlgeschlagen"
  if tailscale --socket="$TS_SOCK" status >/dev/null 2>&1; then
    echo "  ✓ Tailscale aktiv: $(tailscale --socket="$TS_SOCK" ip -4 2>/dev/null | head -1)"
  else
    echo "  ⚠ Tailscale-Status nicht abrufbar"
  fi
}

# ── /etc/opencode.env vorbereiten ───────────────────────────────────────────
: > /etc/opencode.env
chmod 600 /etc/opencode.env

if [ -z "${OPENBAO_APPROLE_SECRET_ID:-}" ]; then
  echo "ERROR: OPENBAO_APPROLE_SECRET_ID ist nicht gesetzt."
  exit 1
fi

# ── OpenBao: AppRole-Login (mit Retry) ──────────────────────────────────────
# Transiente Fehler (Netzwerk-Token-Blinker, Rollout-Fenster des Logins)
# dürfen den Start nicht mehr abbrechen: 6 Versuche à max. 10 s Curl-Timeout,
# danach Setup ohne Secrets weiterlaufen lassen (SKIP_SECRETS=1).
echo "=== OpenBao: Authentifizierung ($PROFILE) ==="
BAO_TOKEN=""
for _bao_attempt in 1 2 3 4 5 6; do
  RESPONSE=$(curl -sS --max-time 10 -X POST "${OPENBAO_ADDR}/v1/auth/approle/login" \
    -H "Content-Type: application/json" \
    -d "{\"role_id\":\"${OPENBAO_ROLE_ID}\",\"secret_id\":\"${OPENBAO_APPROLE_SECRET_ID}\"}" 2>&1) || RESPONSE=""
  BAO_TOKEN=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['auth']['client_token'])
except Exception:
    print('')
")
  if [ -n "$BAO_TOKEN" ]; then break; fi
  echo "  ⚠ OpenBao-Login fehlgeschlagen (Versuch ${_bao_attempt}/6) – nächster Versuch in 5 s"
  [ "$_bao_attempt" -ge 6 ] || sleep 5
done
unset RESPONSE
if [ -n "$BAO_TOKEN" ]; then
  export BAO_TOKEN
  printf 'BAO_TOKEN="%s"\n' "$BAO_TOKEN" >> /etc/opencode.env
  echo "  ✓ OpenBao-Token erhalten"
else
  echo "⚠ OpenBao-Login nach 6 Versuchen fehlgeschlagen – Setup läuft ohne Secrets weiter"
  SKIP_SECRETS=1
fi

# ── OpenBao: Secrets laden ──────────────────────────────────────────────────
echo ""
STAMP "OpenBao-Login fertig"
echo "=== OpenBao: Secrets laden ==="
read_secret() {
  local path="$1" key="$2" varname="$3"
  local value
  if [ "${SKIP_SECRETS:-0}" = "1" ]; then
    echo "  ⚠ ${varname}: übersprungen (kein OpenBao-Login)"
    return 0
  fi
  value=$(curl -sS --max-time 10 -H "X-Vault-Token: ${BAO_TOKEN}" "${OPENBAO_ADDR}/v1/${path}" \
    | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['data']['data'].get('${key}', ''))
except Exception:
    print('')
")
  if [ -z "$value" ]; then
    echo "  ⚠ ${varname}: nicht gefunden"
  else
    export "${varname}=${value}"
    # NIE den Wert loggen – nur den Variablennamen (Logs landen in /tmp/*.log).
    echo "  ✓ ${varname} geladen"
  fi
}

case "$PROFILE" in
  infrastructure)
    read_secret "secret/data/mcp/homeassistant"  "api_key"   "HA_LLA_TOKEN"
    read_secret "secret/data/mcp/homeassistant-kleve" "api_key" "HA_KLEVE_LLA_TOKEN"
    read_secret "secret/data/mcp/authentik"      "api_key"   "AUTHENTIK_API_KEY"
    read_secret "secret/data/mcp/homarr"         "api_key"   "HOMARR_API_KEY"
    read_secret "secret/data/mcp/grafana"        "api_key"   "GRAFANA_API_KEY"
    read_secret "secret/data/mcp/opencloud"      "api_key"   "OPENCLOUD_API_KEY"
    read_secret "secret/data/mcp/opencloud"      "username"  "OPENCLOUD_USERNAME"
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_private_user"  "OCIS_PRIVATE_USER"
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_private_token" "OCIS_PRIVATE_TOKEN"
    read_secret "secret/data/mcp/tailscale"      "auth_key"  "TS_AUTHKEY"
    ;;
  general)
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_private_user"  "OCIS_PRIVATE_USER"
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_private_token" "OCIS_PRIVATE_TOKEN"
    # Super-Productivity-MCP (Remote-HTTP, Bearer-Token)
    read_secret "secret/data/mcp/super-productivity" "token" "SP_MCP_TOKEN"
    ;;
  muellerconnect)
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_muellerconnect_user"  "OCIS_MUELLERCONNECT_USER"
    read_secret "secret/data/mcp/opencloud-ocis" "obsidian_muellerconnect_token" "OCIS_MUELLERCONNECT_TOKEN"
    ;;
  datascience)
    # FernUni-Account: eigener oCIS-Space des Studenten (oCIS-MCP + fernuni-sync).
    read_secret "secret/data/uni/fernuni-ocis" "user"     "FERNUNI_OCIS_USER"
    read_secret "secret/data/uni/fernuni-ocis" "token"    "FERNUNI_OCIS_TOKEN"
    read_secret "secret/data/uni/fernuni-ocis" "password" "FERNUNI_OCIS_PW"
    read_secret "secret/data/uni/fernuni-ocis" "space_id" "FERNUNI_SPACE_ID"
    read_secret "secret/data/uni/fernuni-ocis" "url"      "FERNUNI_OCIS_BASE"
    if [ -z "${FERNUNI_OCIS_BASE:-}" ]; then
      export FERNUNI_OCIS_BASE="https://opencloud.mueller-nas.de"
      echo "  ⚠ FERNUNI_OCIS_BASE nicht im Secret – Fallback gesetzt"
    fi
    # Moodle (fernuni-sync)
    read_secret "secret/data/uni/moodle" "username" "MD_USER"
    read_secret "secret/data/uni/moodle" "password" "MD_PW"
    read_secret "secret/data/uni/moodle" "base_url" "MD_BASE"
    # GitHub: private Modul-Repos (coder-templates/datascience/uni-git-setup.sh).
    # Die AppRole 'mcp-server-uni' erlaubt genau secret/data/mcp/github – andere
    # mcp-Secrets bleiben diesem Profil verborgen. Der Token wird nur zur
    # Laufzeit benutzt; die Remote-URLs der Modul-Repos tragen keinen Token.
    read_secret "secret/data/mcp/github" "api_key" "GITHUB_TOKEN"
    ;;
esac
# OpenCode Go: mehrere Accounts (alle Profile)
read_secret "secret/data/mcp/opencode-go" "default1" "OC_GO_DEFAULT1"
read_secret "secret/data/mcp/opencode-go" "default2" "OC_GO_DEFAULT2"
read_secret "secret/data/mcp/opencode-go" "default3" "OC_GO_DEFAULT3"
read_secret "secret/data/mcp/opencode-go" "default4" "OC_GO_DEFAULT4"
# KEIN read_secret mehr auf "active": der aktive Account wird allein von Hestia
# über die SOT .opencode/hestia-accounts.jsonc bestimmt (siehe unten).
# OpenCode-Server-Password (Fleet-Zugriff: Hestia + Hermes space-matrix)
read_secret "secret/data/mcp/hermes-opencode-service" "password" "OPENCODE_SERVER_PASSWORD"

# ── GitHub-Credentials (git clone/push privater Repos) ──────────────────────
echo ""
STAMP "Secrets geladen"
echo "=== Git-Credentials (GitHub) ==="
if [ -f "$REPO_DIR/scripts/setup-git-credentials.sh" ]; then
  bash "$REPO_DIR/scripts/setup-git-credentials.sh" || echo "  ⚠ setup-git-credentials fehlgeschlagen"
else
  echo "  ⚠ scripts/setup-git-credentials.sh nicht gefunden"
fi

# ── Parallel-Setup starten (unabhängig voneinander, brauchen nur Netz) ──────
echo ""
STAMP "Parallel-Setup: Start"
echo "=== Parallel-Setup (Hintergrund) ==="
bg "OpenCode/ssh-mcp/DCP" /tmp/bg-npm.log npm_chain
bg "uv/uvx"               /tmp/bg-uv.log install_uv
bg "ocis-mcp-server"      /tmp/bg-ocis-mcp.log install_ocis_mcp
[ "$WANT_GRAFANA_MCP" = "1" ] && bg "mcp-grafana" /tmp/bg-mcp-grafana.log install_mcp_grafana
bg "Herdr"                /tmp/bg-herdr.log install_herdr

# Tailscale (nur infrastructure): bewusst NICHT getrackt – nichts im Setup
# hängt davon ab, ein hängender Auth-Vorgang darf den Start nicht aufhalten.
if [ "$PROFILE" = "infrastructure" ]; then
  ( setup_tailscale ) > /tmp/bg-tailscale.log 2>&1 &
  echo "  → Tailscale: läuft im Hintergrund (Log: /tmp/bg-tailscale.log)"
fi

# ── SSH via OpenBao-CA + ssh-mcp (nur infrastructure) ──────────────────────
if [ "$PROFILE" = "infrastructure" ]; then
  echo ""
  echo "=== SSH-Zugang (OpenBao-CA) ==="
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  KEY=/root/.ssh/id_ed25519_coder
  rm -f "$KEY" "$KEY.pub" "$KEY-cert.pub"
  ssh-keygen -t ed25519 -f "$KEY" -N "" -C "coder-workspace" -q

  SIGNED=$(curl -sS --max-time 10 -X POST "${OPENBAO_ADDR}/v1/ssh/sign/host-access" \
    -H "X-Vault-Token: ${BAO_TOKEN}" -H "Content-Type: application/json" \
    -d "{\"public_key\":\"$(cat ${KEY}.pub)\",\"valid_principals\":\"root\",\"ttl\":\"24h\"}" \
    | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['data']['signed_key'])
except Exception:
    print('')
")
  if [ -n "$SIGNED" ]; then
    printf '%s\n' "$SIGNED" > "${KEY}-cert.pub"
    chmod 600 "$KEY" "${KEY}-cert.pub"
    echo "  ✓ SSH-Zertifikat ausgestellt"
  else
    echo "  ⚠ SSH-Zertifikat konnte nicht ausgestellt werden"
  fi

  cat > /root/.ssh/config << 'SSHCFG'
Host docker
    HostName 10.0.10.10
    User root
    IdentityFile /root/.ssh/id_ed25519_coder
    CertificateFile /root/.ssh/id_ed25519_coder-cert.pub
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null

Host proxmox
    HostName 10.0.10.20
    User root
    IdentityFile /root/.ssh/id_ed25519_coder
    CertificateFile /root/.ssh/id_ed25519_coder-cert.pub
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null

Host truenas
    HostName 10.0.10.30
    User root
    IdentityFile /root/.ssh/id_ed25519_coder
    CertificateFile /root/.ssh/id_ed25519_coder-cert.pub
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
SSHCFG
  chmod 600 /root/.ssh/config
  echo "  ✓ SSH-Config erstellt"

  # ── ssh-mcp: stabiler Key aus OpenBao (in authorized_keys der Zielhosts) ────
  echo ""
  echo "=== ssh-mcp Key (OpenBao) ==="
  SSH_MCP_KEY=/root/.ssh/id_ed25519_mcp
  MCP_KEY=$(curl -sS --max-time 10 -H "X-Vault-Token: ${BAO_TOKEN}" \
    "${OPENBAO_ADDR}/v1/secret/data/mcp/ssh-mcp" \
    | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['data']['data'].get('private_key', ''))
except Exception:
    print('')
")
  if [ -n "$MCP_KEY" ]; then
    printf '%s\n' "$MCP_KEY" > "$SSH_MCP_KEY"
    chmod 600 "$SSH_MCP_KEY"
    echo "  ✓ ssh-mcp Key installiert ($SSH_MCP_KEY)"
  else
    echo "  ⚠ ssh-mcp Key nicht gefunden (secret/data/mcp/ssh-mcp)"
  fi

  # ── ssh-mcp: eigener Key für den Carsten-PC (Windows) ──────────────────────
  SSH_MCP_PC_KEY=/root/.ssh/id_ed25519_mcp_pc
  PC_KEY=$(curl -sS --max-time 10 -H "X-Vault-Token: ${BAO_TOKEN}" \
    "${OPENBAO_ADDR}/v1/secret/data/mcp/ssh-mcp-pc" \
    | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['data']['data'].get('private_key', ''))
except Exception:
    print('')
")
  if [ -n "$PC_KEY" ]; then
    printf '%s\n' "$PC_KEY" > "$SSH_MCP_PC_KEY"
    chmod 600 "$SSH_MCP_PC_KEY"
    echo "  ✓ Carsten-PC Key installiert ($SSH_MCP_PC_KEY)"
  else
    echo "  ⚠ Carsten-PC Key nicht gefunden (secret/data/mcp/ssh-mcp-pc)"
  fi

  # ── ssh-mcp Konfiguration ───────────────────────────────────────────────────
  echo ""
  echo "=== ssh-mcp Config ==="
  mkdir -p /root/.config/ssh-mcp /root/.ssh-mcp-transfers && chmod 700 /root/.ssh-mcp-transfers
  cat > /root/.config/ssh-mcp/config.toml << 'MCPCFG'
[defaults]
defaultProfile = "docker-host"
approvalMode = "auto"
transferRoot = "/root/.ssh-mcp-transfers"

# Krefeld-Hosts (prod) + Kleve-Hosts (kleve): fuer server-management- und
# homeassistant-Space - alle Befehlsklassen pro Gruppe erlaubt.
# Die eingebaute Forbidden-Liste (rm -rf /, mkfs, ...) greift weiterhin.
[policy.roleBindings.admin]
prod = ["read-only", "safe", "destructive", "privileged"]
kleve = ["read-only", "safe", "destructive", "privileged"]
pc = ["read-only", "safe", "destructive", "privileged"]

[[profiles]]
name = "docker-host"
host = "10.0.10.10"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "proxmox"
host = "10.0.10.20"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "truenas"
host = "10.0.10.30"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "pbs"
host = "10.0.10.21"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "coder"
host = "10.0.10.17"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "unifi"
host = "10.0.0.1"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "media"
host = "10.0.10.25"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "test"
host = "10.0.10.40"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

# Carsten-PC (Windows, User-VLAN 10.0.0.0/24) — eigener Key (ssh-mcp-pc)
[[profiles]]
name = "carsten-pc"
host = "10.0.0.20"
port = 22
user = "carst"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp_pc"
role = "admin"
group = "pc"
approvalPolicy = "auto"

# Cleve Hosts (via Tailscale-Tunnel / Subnet-Route)
[[profiles]]
name = "cleve-proxmox"
host = "192.168.178.50"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-truenas"
host = "192.168.178.51"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-docker"
host = "192.168.178.52"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"
MCPCFG
  cat > /root/.config/ssh-mcp/config-full.toml << 'MCPCFG'
[defaults]
defaultProfile = "docker-host"
approvalMode = "auto"
transferRoot = "/root/.ssh-mcp-transfers"

# Krefeld-Hosts (prod) + Kleve-Hosts (kleve): fuer server-management- und
# homeassistant-Space - alle Befehlsklassen pro Gruppe erlaubt.
# Die eingebaute Forbidden-Liste (rm -rf /, mkfs, ...) greift weiterhin.
[policy.roleBindings.admin]
prod = ["read-only", "safe", "destructive", "privileged"]
kleve = ["read-only", "safe", "destructive", "privileged"]
pc = ["read-only", "safe", "destructive", "privileged"]

[[profiles]]
name = "docker-host"
host = "10.0.10.10"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "proxmox"
host = "10.0.10.20"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "truenas"
host = "10.0.10.30"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "pbs"
host = "10.0.10.21"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "coder"
host = "10.0.10.17"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "unifi"
host = "10.0.0.1"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "media"
host = "10.0.10.25"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

[[profiles]]
name = "test"
host = "10.0.10.40"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "prod"
approvalPolicy = "auto"

# Carsten-PC (Windows, User-VLAN 10.0.0.0/24) — eigener Key (ssh-mcp-pc)
[[profiles]]
name = "carsten-pc"
host = "10.0.0.20"
port = 22
user = "carst"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp_pc"
role = "admin"
group = "pc"
approvalPolicy = "auto"

# Cleve Hosts (via Tailscale-Tunnel / Subnet-Route)
[[profiles]]
name = "cleve-proxmox"
host = "192.168.178.50"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-truenas"
host = "192.168.178.51"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-docker"
host = "192.168.178.52"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"
MCPCFG
  cat > /root/.config/ssh-mcp/config-cleve.toml << 'MCPCFG'
[defaults]
defaultProfile = "cleve-docker"
approvalMode = "auto"
transferRoot = "/root/.ssh-mcp-transfers"

# Nur Kleve-Hosts (via Tailscale-Tunnel / Subnet-Route) - fuer den cleve-Space.
# Kein prod-Binding: Krefeld-Hosts sind hier bewusst nicht definiert
# (harte Trennlinie Site-Krefeld / Site-Kleve).
[policy.roleBindings.admin]
kleve = ["read-only", "safe", "destructive", "privileged"]

[[profiles]]
name = "cleve-proxmox"
host = "192.168.178.50"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-truenas"
host = "192.168.178.51"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"

[[profiles]]
name = "cleve-docker"
host = "192.168.178.52"
port = 22
user = "root"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp"
role = "admin"
group = "kleve"
approvalPolicy = "auto"
MCPCFG
  chmod 700 /root/.config/ssh-mcp
  chmod 600 /root/.config/ssh-mcp/config.toml /root/.config/ssh-mcp/config-full.toml /root/.config/ssh-mcp/config-cleve.toml
  echo "  ✓ ssh-mcp Config erstellt"
fi

# ── ssh-mcp (nur general): Zugang zum Carsten-PC (Windows) ──────────────────
if [ "$PROFILE" = "general" ]; then
  echo ""
  echo "=== ssh-mcp (Carsten-PC) ==="
  mkdir -p /root/.ssh && chmod 700 /root/.ssh

  # Eigener, exklusiver PC-Key (nicht der gemeinsame ssh-mcp-Key der Hosts).
  SSH_MCP_PC_KEY=/root/.ssh/id_ed25519_mcp_pc
  PC_KEY=$(curl -sS --max-time 10 -H "X-Vault-Token: ${BAO_TOKEN}" \
    "${OPENBAO_ADDR}/v1/secret/data/mcp/ssh-mcp-pc" \
    | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin)['data']['data'].get('private_key', ''))
except Exception:
    print('')
")
  if [ -n "$PC_KEY" ]; then
    printf '%s\n' "$PC_KEY" > "$SSH_MCP_PC_KEY"
    chmod 600 "$SSH_MCP_PC_KEY"
    echo "  ✓ Carsten-PC Key installiert ($SSH_MCP_PC_KEY)"
  else
    echo "  ⚠ Carsten-PC Key nicht gefunden (secret/data/mcp/ssh-mcp-pc)"
  fi

  # config-general.toml: ausschließlich der Carsten-PC (harte Grenze,
  # analog zu config-cleve.toml — Krefeld-/Kleve-Profile existieren hier nicht).
  mkdir -p /root/.config/ssh-mcp /root/.ssh-mcp-transfers && chmod 700 /root/.ssh-mcp-transfers
  cat > /root/.config/ssh-mcp/config-general.toml << 'MCPCFG'
[defaults]
defaultProfile = "carsten-pc"
approvalMode = "auto"
transferRoot = "/root/.ssh-mcp-transfers"

# Nur der Carsten-PC (Windows) – für den general-Space.
# Kein prod-/kleve-Binding: Krefeld- und Kleve-Hosts sind hier bewusst nicht
# definiert (harte Grenze, analog zu config-cleve.toml).
[policy.roleBindings.admin]
pc = ["read-only", "safe", "destructive", "privileged"]

[[profiles]]
name = "carsten-pc"
host = "10.0.0.20"
port = 22
user = "carst"
auth = "key"
keyRef = "/root/.ssh/id_ed25519_mcp_pc"
role = "admin"
group = "pc"
approvalPolicy = "auto"
MCPCFG
  chmod 700 /root/.config/ssh-mcp
  chmod 600 /root/.config/ssh-mcp/config-general.toml
  echo "  ✓ ssh-mcp Config (config-general.toml) erstellt"
fi

# ── OpenCode-Basisverzeichnis ──────────────────────────────────────────────
mkdir -p ~/.config/opencode
# Model-Router-Konfiguration liegt in .opencode/opencode-model-router.overrides.jsonc
# am Repo-Root (das Plugin durchsucht von cwd aufwärts bis Repo-Root) —
# kein Symlink/Cache-Eingriff mehr nötig. tiers.json entfernt (Historie in git).
echo 'alias update-opencode-config="cd ~/opencode-coder-env && git pull --ff-only"' >> ~/.bashrc

# ── MCP-Konfiguration (Config installieren, profil-spezifisch) ──────────────
echo ""
STAMP "MCP-Config: Start"
echo "=== MCP-Konfiguration ==="
case "$PROFILE" in
  infrastructure) GLOBAL_CFG_SRC="$REPO_DIR/coder-templates/infrastructure/config/global-opencode.json" ;;
  general)        GLOBAL_CFG_SRC="$REPO_DIR/workspaces/general/workspace-global.json" ;;
  muellerconnect)        GLOBAL_CFG_SRC="$REPO_DIR/workspaces/minijob/workspace-global.json" ;;
  datascience)           GLOBAL_CFG_SRC="$REPO_DIR/workspaces/uni/workspace-global.json" ;;
esac
if [ -f "$GLOBAL_CFG_SRC" ]; then
  install -m 600 "$GLOBAL_CFG_SRC" /root/.config/opencode/opencode.json
  echo "  ✓ Config installiert ($PROFILE)"
else
  echo "  ⚠ Config nicht gefunden: $GLOBAL_CFG_SRC"
fi

# DCP-Plugin im globalen Config auf den Pin aus versions.env festschreiben.
# OpenCode loest einen blanken Plugin-Namen selbst als @latest auf und laedt aus
# seinem eigenen Cache — nur ein versionierter Spec bindet die Laufzeitversion.
# So bleibt DCP_VERSION in versions.env die einzige Wahrheit (kein Drift zwischen
# den vier Config-Dateien).
if [ -n "${DCP_VERSION:-}" ] && [ -f /root/.config/opencode/opencode.json ]; then
  if python3 - "$DCP_VERSION" <<'PY'
import json, re, sys
p = "/root/.config/opencode/opencode.json"
ver = sys.argv[1]
try:
    d = json.load(open(p))
except Exception:
    sys.exit(1)
pl = d.get("plugins")
if isinstance(pl, list):
    d["plugins"] = [
        re.sub(r"^@tarquinen/opencode-dcp(?:@[^@]+)?$",
               f"@tarquinen/opencode-dcp@{ver}", x)
        if isinstance(x, str) else x
        for x in pl
    ]
    with open(p, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
PY
  then
    echo "  ✓ DCP-Plugin auf @${DCP_VERSION} gepinnt"
  else
    echo "  ⚠ DCP-Plugin-Pin konnte nicht gesetzt werden"
  fi
fi

# ── ntfy: Benachrichtigungen aus OpenCode (alle Profile) ────────────────────
# Das Plugin liegt in ~/.config/opencode/plugins/ und wird von OpenCode beim
# Start des Dienstes automatisch geladen (wie das herdr-Plugin daneben).
# Ohne Token bleibt es still — es blockiert also nichts, wenn das Secret fehlt.
echo ""
STAMP "MCP-Config: fertig"
echo "=== ntfy: OpenCode-Benachrichtigungen ==="
NTFY_PLUGIN_SRC="$REPO_DIR/coder-templates/shared/plugins/ntfy-notify.js"
mkdir -p ~/.config/opencode/plugins
if [ -f "$NTFY_PLUGIN_SRC" ]; then
  install -m 600 "$NTFY_PLUGIN_SRC" ~/.config/opencode/plugins/ntfy-notify.js \
    && echo "  ✓ ntfy-Plugin installiert (plugins/ntfy-notify.js)" \
    || echo "  ⚠ ntfy-Plugin konnte nicht installiert werden"
else
  echo "  ⚠ ntfy-Plugin nicht gefunden: $NTFY_PLUGIN_SRC"
fi
read_secret "secret/data/mcp/notify" "opencode_token" "NTFY_TOKEN"

read_secret "secret/data/mcp/notify" "url" "NTFY_URL"
if [ -n "${NTFY_TOKEN:-}" ]; then
  printf 'NTFY_TOKEN="%s"\n' "$NTFY_TOKEN" >> /etc/opencode.env
  printf 'NTFY_URL="%s"\n' "${NTFY_URL:-https://ntfy.mueller-nas.de}" >> /etc/opencode.env
  echo "  ✓ ntfy-Token erhalten"
else
  echo "  ⚠ ntfy-Token fehlt (OpenBao secret/data/mcp/notify, Feld 'opencode_token')"
fi

# Schreibt einen Schlüssel nur, wenn er in /etc/opencode.env noch fehlt –
# ein bereits vorhandener (manuell eingetragener) Wert gewinnt immer.
set_env_default() {
  local key="$1" value="$2"
  grep -q "^${key}=" /etc/opencode.env 2>/dev/null \
    || printf '%s="%s"\n' "$key" "$value" >> /etc/opencode.env
}

# Statische ntfy-Optionen (alle Profile): Icons für die Meldungen, Stummschaltung
# der Hestia-Ausfall- und Speed-Proben, Deep-Link-Stil (öffnet direkt die Session)
# und das Diagnose-Log. Bisher waren diese vier Schlüssel nur im Container
# coder-carstenmueller2002-infrastructure manuell gesetzt – alle übrigen Profile
# liefen ohne sie. ntfy ist ausschließlich Meldeweg (siehe docs/notifications.md)
# – Antwort-Schalter gibt es deshalb hier keine mehr.
NTFY_ICON_BASE_DEFAULT="https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons"
set_env_default NTFY_ICON_BASE     "$NTFY_ICON_BASE_DEFAULT"
set_env_default NTFY_IGNORE_TITLES "Ausfall-Probe,Speed-Probe"
set_env_default NTFY_LINK_STYLE    "session"
set_env_default NTFY_REPLY_LOG     "/tmp/opencode/ntfy-diag.log"
# Debounce für „Fertig"-Meldungen: eine Meldung wird erst nach N ms ohne neue
# Aktivität verschickt — das unterdrückt Spam bei schnell aufeinanderfolgenden
# Turns, kostet aber genau diese Zeit zwischen Turn-Ende und Meldung. 60 s und
# 15 s wurden beide als zu spät empfunden (die Meldung kam, als der Chat längst
# gelesen war), deshalb steht der Wert derzeit auf 0 = sofort. Wer es ruhiger
# will, setzt hier z. B. 15000 ein — Doppelmeldungen bei sehr schnellen
# Doppel-Turns verhindert weiterhin der 15-s-Cooldown je Session im Plugin.
set_env_default NTFY_DEBOUNCE_MS  "0"
# Fehler-Drossel (siehe docs/notifications.md): denselben Fehlertext höchstens
# einmal je Fenster melden (24 h). Ein wiederkehrendes Provider-Limit
# ("Go usage limit exceeded") erzeugte bisher pro Turn eine Meldung. Der Zustand
# liegt im OpenCode-Datenverzeichnis — das ist im Container ein Host-Pfad
# (/etc/opencode-data/<nutzer>, siehe main.tf) und übersteht damit die
# Neuerstellung des Containers (~/.config/opencode wäre beim nächsten Start leer).
set_env_default NTFY_ERROR_THROTTLE_MS "86400000"
set_env_default NTFY_STATE_FILE        "$HOME/.local/share/opencode/ntfy-notify-state.json"
echo "  ✓ ntfy-Standardoptionen in /etc/opencode.env sichergestellt"

# Topic je Workspace (opt-in über den Namen, siehe Tabelle):
#   infrastructure → oc-infrastructure | general → oc-general
#   muellerconnect → oc-minijob        | datascience → oc-datascience
# NTFY_TOPIC_PREFIX trennt die Topics mehrerer Workspaces, NTFY_TOPIC_NAME ist
# die Kategorie in der App. Unbekanntes Profil: nichts setzen, damit das Topic
# unverändert bleibt ("ai"). Beide Schlüssel nur, wenn sie fehlen –
# ein bereits vorhandener (manuell eingetragener) Wert gewinnt immer.
#
# WICHTIG – Trennung der Zuständigkeiten: NTFY_TOPIC_NAME bestimmt NUR das
# Topic (die Kategorie in der App). Icon und Titel der Meldung kommen weiterhin
# aus dem Session-Ordner – NTFY_WORKSPACE hier NICHT setzen, das würde die
# Ordner-Icons überschreiben.
NTFY_TOPIC_NAME_VALUE=""
case "$PROFILE" in
  infrastructure) NTFY_TOPIC_NAME_VALUE="infrastructure" ;;
  general)        NTFY_TOPIC_NAME_VALUE="general" ;;
  muellerconnect) NTFY_TOPIC_NAME_VALUE="minijob" ;;
  datascience)    NTFY_TOPIC_NAME_VALUE="datascience" ;;
esac
if [ -n "$NTFY_TOPIC_NAME_VALUE" ]; then
  set_env_default NTFY_TOPIC_PREFIX "oc"
  set_env_default NTFY_TOPIC_NAME   "$NTFY_TOPIC_NAME_VALUE"
  echo "  ✓ ntfy-Topic: oc-$NTFY_TOPIC_NAME_VALUE (Profil $PROFILE)"
else
  echo "  • ntfy-Topic: kein Eintrag für Profil '$PROFILE' – Topic bleibt unverändert"
fi

# ── OpenCode-Theme: dauerhaft hell (cli.json, idempotent) ───────────────────
mkdir -p ~/.config/opencode
python3 - <<'PY' >/dev/null 2>&1
import os, json
p = os.path.expanduser("~/.config/opencode/cli.json")
try:
    d = json.load(open(p))
    if not isinstance(d, dict):
        d = {}
except Exception:
    d = {}
d.setdefault("$schema", "https://opencode.ai/v2/cli.json")
d["theme"] = {"name": "opencode", "mode": "light"}
with open(p, "w") as f:
    json.dump(d, f, indent=2)
os.chmod(p, 0o600)
PY

# ── Barriere: ab hier wird die OpenCode-CLI wirklich benutzt ────────────────
echo ""
STAMP "Barriere: warte auf Jobs"
echo "=== Parallel-Setup: warte auf Hintergrund-Jobs ==="
wait_bg
STAMP "Barriere: fertig"

# ── Herdr-Server vorziehen ──────────────────────────────────────────────────
# Er braucht nur das Herdr-Binary aus dem Parallel-Setup (Barriere oben), NICHT
# den OpenCode-Service. Vorgezogen läuft er während der Agents-/Env- und
# Service-Phase warm; der Herdr-Block unten wartet weiterhin auf
# "status: running" (dort greift auch der Fallback-Start, falls er hier nicht
# hochkommt) und lädt die Theme-Konfiguration per reload-config nach.
if [ -x "$HERDR_BIN" ] && ! "$HERDR_BIN" status 2>/dev/null | grep -q "status: running"; then
  nohup "$HERDR_BIN" server > /tmp/herdr-server.log 2>&1 &
  echo "  → Herdr-Server startet im Hintergrund (Log: /tmp/herdr-server.log)"
fi

# ── Env-Injektion: HA / authentik (nur infrastructure) ──────────────────────
if [ "$PROFILE" = "infrastructure" ]; then
  if [ -n "${HA_LLA_TOKEN:-}" ]; then
    opencode service set env HA_LLA_TOKEN "$HA_LLA_TOKEN" >/dev/null 2>&1 \
      && echo "  ✓ HA_LLA_TOKEN im OpenCode-Service gesetzt" \
      || echo "  ⚠ HA_LLA_TOKEN konnte nicht gesetzt werden"
  fi
  if [ -n "${HA_KLEVE_LLA_TOKEN:-}" ]; then
    opencode service set env HA_KLEVE_LLA_TOKEN "$HA_KLEVE_LLA_TOKEN" >/dev/null 2>&1 \
      && echo "  ✓ HA_KLEVE_LLA_TOKEN im OpenCode-Service gesetzt" \
      || echo "  ⚠ HA_KLEVE_LLA_TOKEN konnte nicht gesetzt werden"
  fi
  if [ -n "${AUTHENTIK_API_KEY:-}" ]; then
    opencode service set env AUTHENTIK_API_KEY "$AUTHENTIK_API_KEY" >/dev/null 2>&1 \
      && echo "  ✓ AUTHENTIK_API_KEY im OpenCode-Service gesetzt" \
      || echo "  ⚠ AUTHENTIK_API_KEY konnte nicht gesetzt werden"
  fi
  if [ -n "${HOMARR_API_KEY:-}" ]; then
    opencode service set env HOMARR_API_KEY "$HOMARR_API_KEY" >/dev/null 2>&1 \
      && echo "  ✓ HOMARR_API_KEY im OpenCode-Service gesetzt" \
      || echo "  ⚠ HOMARR_API_KEY konnte nicht gesetzt werden"
  fi
  if [ -n "${GRAFANA_API_KEY:-}" ]; then
    opencode service set env GRAFANA_API_KEY "$GRAFANA_API_KEY" >/dev/null 2>&1 \
      && echo "  ✓ GRAFANA_API_KEY im OpenCode-Service gesetzt" \
      || echo "  ⚠ GRAFANA_API_KEY konnte nicht gesetzt werden"
  fi
fi

# ntfy-Token (alle Profile): das Plugin liest NTFY_TOKEN/NTFY_URL aus der
# Service-Umgebung — so überlebt die Konfiguration jeden Container-Neustart.
# Dasselbe gilt für das Topic (NTFY_TOPIC_PREFIX/NTFY_TOPIC_NAME); der Name
# stammt aus der einen case-Auflösung weiter oben (keine zweite Wahrheitsquelle).
if [ -n "${NTFY_TOKEN:-}" ]; then
  opencode service set env NTFY_TOKEN "$NTFY_TOKEN" >/dev/null 2>&1 \
    && echo "  ✓ NTFY_TOKEN im OpenCode-Service gesetzt" \
    || echo "  ⚠ NTFY_TOKEN konnte nicht gesetzt werden"
  opencode service set env NTFY_URL "${NTFY_URL:-https://ntfy.mueller-nas.de}" >/dev/null 2>&1 || true
  if [ -n "${NTFY_TOPIC_NAME_VALUE:-}" ]; then
    opencode service set env NTFY_TOPIC_PREFIX "oc" >/dev/null 2>&1 || true
    opencode service set env NTFY_TOPIC_NAME "$NTFY_TOPIC_NAME_VALUE" >/dev/null 2>&1 || true
  fi
fi

# ── Model-Router: Overrides GLOBAL ablegen (vor dem Service-Start!) ─────────
# Sessions/Services mit cwd außerhalb des Repos (z. B. /root) finden die
# Tier-Konfiguration nur, wenn sie auch global liegt.
OVERRIDES_SRC="$REPO_DIR/.opencode/opencode-model-router.overrides.jsonc"
if [ -f "$OVERRIDES_SRC" ]; then
  install -m 600 "$OVERRIDES_SRC" /root/.config/opencode/opencode-model-router.overrides.jsonc \
    && echo "  ✓ Overrides global verfügbar (Service-Kontexte)" \
    || echo "  ⚠ Overrides konnten nicht global abgelegt werden"
fi

# ── DCP: Config GLOBAL ablegen ─────────────────────────────────────────────
DCP_CFG_SRC="$REPO_DIR/.opencode/dcp.jsonc"
if [ -f "$DCP_CFG_SRC" ]; then
  install -m 600 "$DCP_CFG_SRC" /root/.config/opencode/dcp.jsonc \
    && echo "  ✓ DCP-Config global verfügbar (Service-Kontexte)" \
    || echo "  ⚠ DCP-Config konnte nicht global abgelegt werden"
fi

# ── OpenCode Go (mehrere Keys aus OpenBao) ──────────────────────────────────
# Der aktive Account kommt aus der SOT .opencode/hestia-accounts.jsonc (von
# Hestia gepflegt) — nicht aus dem OpenBao-Feld "active" (das wird nicht mehr
# gelesen/geschrieben). Ohne SOT (kein Repo-Checkout) greift der Default.
OC_GO_ACTIVE=""
SOT_OC_GO="${OVERRIDES_SRC%/opencode-model-router.overrides.jsonc}/hestia-accounts.jsonc"
if [ -r "$SOT_OC_GO" ]; then
  OC_GO_ACTIVE="$(python3 -c '
import json, re, sys
raw = open(sys.argv[1], encoding="utf-8").read()
try:
    import hjsonc
    cfg = hjsonc.parse(raw) or {}
except Exception:
    raw = re.sub(r"^[ \t]*//.*$|[ \t]+//.*$", "", raw, flags=re.M)
    cfg = json.loads(re.sub(r",(\s*[}\]])", r"\1", raw))
print(str(cfg.get("active") or ""))
' "$SOT_OC_GO" 2>/dev/null)"
fi
[ -z "$OC_GO_ACTIVE" ] && OC_GO_ACTIVE="${OC_GO_ACTIVE:-default1}"
ACTIVE_VAR="OC_GO_${OC_GO_ACTIVE^^}"
OPENCODE_API_KEY="${!ACTIVE_VAR:-}"

if [ -n "$OPENCODE_API_KEY" ]; then
  export OPENCODE_API_KEY
  # .bashrc IMMER erst deduppen, dann genau EINE Zeile anhängen (kein blindes >>).
  sed -i '/^export OPENCODE_API_KEY=/d' /root/.bashrc 2>/dev/null || true
  printf 'export OPENCODE_API_KEY="%s"\n' "$OPENCODE_API_KEY" >> /root/.bashrc
  # /root/.config/opencode/service.json ist EPHEMER → nach jedem Container-
  # Neustart hier den Key des aktiven Kontos neu ins Service-Env setzen.
  # Der eigentliche Service-Start passiert später; Fehler sind tolerierbar.
  opencode service set env OPENCODE_API_KEY "$OPENCODE_API_KEY" >/dev/null 2>&1 || true
  echo "  ✓ OpenCode Go aktiv: $OC_GO_ACTIVE (SOT)"
else
  echo "  ⚠ Kein OpenCode Go Key gefunden"
fi

# ── Primary-Agents (build/plan) aus @medium ableiten ───────────────────────
# build = @medium (Standardvariante), plan = @medium mit höchster Variante.
# Nutzt den laufenden OpenCode-Server für die Varianten-Liste.
echo ""
STAMP "Agents: Start"
echo "=== Primary-Agents (build/plan) ==="
if [ -f "$OVERRIDES_SRC" ]; then
  # OpenCode-Server sicherstellen — die Varianten-Liste kommt von /api/model
  opencode service start >/dev/null 2>&1 || true
  REPO_DIR="$REPO_DIR" python3 "$REPO_DIR/coder-templates/shared/derive-agents.py" \
    || echo "  ⚠ Primary-Agents konnten nicht abgeleitet werden"
fi

# Helper zum Umschalten zwischen den OpenCode-Go-Keys
cat > /usr/local/bin/oc-go << 'OCGO'
#!/bin/bash
# OpenCode Go Key verwalten:
#   oc-go list              → lokal gesetzter Key/Account (verwaltet von Hestia)
#   oc-go use <name>        → Key lokal wechseln (.bashrc + Service-Env, kein OpenBao-Schreibzugriff)
# Hinweis: es gibt bewusst KEIN 'oc-go default' mehr. Welcher Account aktiv ist,
# entscheidet allein Hestia über .opencode/hestia-accounts.jsonc.
source /etc/opencode.env 2>/dev/null
case "${1:-list}" in
  list)
    echo "Verfügbare OpenCode-Go-Keys:"
    for n in default1 default2 default3 default4; do
      v="OC_GO_${n^^}"; [ -n "${!v:-}" ] && echo "  - $n"
    done
    # Aktiv ist der LOKAL gesetzte Key (nicht mehr OpenBao). Quelle der Wahrheit
    # für die Auswahl bleibt Hestia; hier nur anzeigen, was tatsächlich gesetzt ist.
    ACT_KEY=""
    if [ -r /root/.bashrc ]; then
      ACT_KEY="$(grep -m1 '^export OPENCODE_API_KEY=' /root/.bashrc 2>/dev/null | cut -d= -f2- | tr -d "\"'")"
    fi
    [ -z "$ACT_KEY" ] && ACT_KEY="${OPENCODE_API_KEY:-}"
    ACT="unbekannt"
    if [ -n "$ACT_KEY" ]; then
      for n in default1 default2 default3 default4; do
        v="OC_GO_${n^^}"
        [ -n "${!v:-}" ] && [ "$v" = "$ACT_KEY" ] && ACT="$n"
      done
    fi
    echo "Aktiv (lokal gesetzt): ${ACT}"
    echo "  Key: ${ACT_KEY:+gesetzt (${#ACT_KEY} Zeichen)}${ACT_KEY:-NICHT gesetzt}"
    echo "  Hinweis: Konto-Auswahl wird ausschließlich von Hestia (Panel) verwaltet —"
    echo "           SOT: .opencode/hestia-accounts.jsonc (Feld \"active\")."
    ;;
  use)
    NAME="${2:-}"
    [ -z "$NAME" ] && { echo "Nutzung: oc-go use <default1|default2|default3|default4>"; exit 1; }
    VAR="OC_GO_${NAME^^}"; KEY="${!VAR:-}"
    [ -z "$KEY" ] && { echo "Unbekannter Key: $NAME"; exit 1; }
    # .bashrc erst deduppen, dann genau EINE Zeile anhängen (kein blindes >>).
    sed -i '/^export OPENCODE_API_KEY=/d' /root/.bashrc 2>/dev/null || true
    printf 'export OPENCODE_API_KEY="%s"\n' "$KEY" >> /root/.bashrc
    export OPENCODE_API_KEY="$KEY"
    opencode service set env OPENCODE_API_KEY "$KEY" >/dev/null 2>&1 || true
    echo "✓ Lokal aktiv: $NAME (nur dieser Container — dauerhaft umschalten: Hestia-Panel)"
    ;;
  default)
    echo "✗ 'oc-go default' ist entfernt."
    echo "  Der aktive OpenCode-Go-Account wird ausschließlich von Hestia (Panel)"
    echo "  über die SOT .opencode/hestia-accounts.jsonc (Feld \"active\") bestimmt."
    echo "  Für einen lokalen Test: oc-go use <default1|default2|default3|default4>"
    exit 1
    ;;
  *) echo "Nutzung: oc-go [list | use <name>]";;
esac
OCGO
chmod +x /usr/local/bin/oc-go
echo "  ✓ Helper 'oc-go' installiert"

# ── OpenCode-Server (Service): 0.0.0.0 + OpenBao-paritätisches Passwort ─────
# Der v2-Background-Service regeneriert bei fehlender Konfig Passwort und
# bindet loopback — für Hestia/Hermes-Fleet-Zugriff (Port 49374) brauchen wir
# 0.0.0.0 + das Passwort aus secret/data/mcp/hermes-opencode-service (Goldquelle).
echo ""
STAMP "Agents + Env-Injektion: fertig"
echo "=== OpenCode-Server-Binding (Service) ==="
if [ -n "${OPENCODE_SERVER_PASSWORD:-}" ]; then
  opencode service set env OPENCODE_SERVER_PASSWORD "${OPENCODE_SERVER_PASSWORD}" >/dev/null 2>&1 || true
fi
mkdir -p ~/.config/opencode
python3 - <<'PY' >/dev/null 2>&1
import os, json
p = os.path.expanduser("~/.config/opencode/service.json")
try:
    d = json.load(open(p))
except Exception:
    d = {}
d["hostname"] = "0.0.0.0"
d.setdefault("port", 49374)
pw = os.environ.get("OPENCODE_SERVER_PASSWORD")
if pw:
    d["password"] = pw
json.dump(d, open(p, "w"), indent=2)
PY
# ── SSO-Proxy (nur Loopback): transparente Basic-Injektion für die Web-UI ────
# Die Web-UI verlangt für /api/* HTTP-Basic; der Proxy auf 127.0.0.1:49375
# injiziert es und reicht alles an 49374 weiter. Da er ausschließlich am
# Loopback lauscht, ist er nur über den Coder-Agent erreichbar (Port-App
# hinter Authentik) — 49374 bleibt unverändert geschützt. Opt-out:
# OPENCODE_SSO_PROXY=0.
if [ "${OPENCODE_SSO_PROXY:-1}" != "0" ] && [ "${OPENCODE_SSO_PROXY:-1}" != "false" ]; then
  SSO_PROXY_SRC="$SCRIPT_DIR/opencode-sso-proxy.js"
  if [ -f "$SSO_PROXY_SRC" ]; then
    pkill -f "opencode-sso-proxy.js" 2>/dev/null || true
    setsid nohup node "$SSO_PROXY_SRC" > /tmp/opencode-sso-proxy.log 2>&1 < /dev/null &
    # Health-Check: 3 Versuche, zwischen den Versuchen 2 s. Fehlt der Listener,
    # obwohl OPENCODE_SERVER_PASSWORD gesetzt ist, einmalig neu starten.
    _sso_ready=0
    for _sso_attempt in 1 2 3; do
      _sso_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:49375/api/info 2>/dev/null)
      if [ -n "$_sso_code" ] && [ "$_sso_code" != "000" ]; then _sso_ready=1; break; fi
      [ "$_sso_attempt" -ge 3 ] || sleep 2
    done
    if [ "$_sso_ready" != "1" ] && [ -n "${OPENCODE_SERVER_PASSWORD:-}" ]; then
      echo "  ⚠ SSO-Proxy-Listener fehlt – Neustart (Health-Check 3× fehlgeschlagen)"
      pkill -f "opencode-sso-proxy.js" 2>/dev/null || true
      setsid nohup node "$SSO_PROXY_SRC" > /tmp/opencode-sso-proxy.log 2>&1 < /dev/null &
      for _sso_attempt in 1 2 3; do
        _sso_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:49375/api/info 2>/dev/null)
        if [ -n "$_sso_code" ] && [ "$_sso_code" != "000" ]; then _sso_ready=1; break; fi
        [ "$_sso_attempt" -ge 3 ] || sleep 2
      done
    fi
    if [ "$_sso_ready" = "1" ]; then
      # Deep-Links (ntfy „Session öffnen") nutzen künftig die Proxy-Port-App.
      # Bewusst direkt in service.json statt `opencode service set env`: Letzteres
      # startet den Dienst sofort neu und kann laufende Sitzungen mitten im
      # Startskript abbrechen. Der Neustart weiter unten greift den Wert auf;
      # alle übrigen Env-Schlüssel bleiben erhalten.
      python3 - <<'PY' >/dev/null 2>&1 || true
import json, os
p = os.path.expanduser("~/.config/opencode/service.json")
d = json.load(open(p))
d.setdefault("env", {})["NTFY_SESSION_PORT"] = "49375"
json.dump(d, open(p, "w"), indent=2)
PY
      set_env_default NTFY_SESSION_PORT 49375
      echo "  ✓ SSO-Proxy: 127.0.0.1:49375 (Deep-Links nutzen Port 49375)"
    else
      if [ -z "${OPENCODE_SERVER_PASSWORD:-}" ]; then
        echo "  ⚠ SSO-Proxy ohne OPENCODE_SERVER_PASSWORD (kein OpenBao-Login) – Basic-Injektion nicht aktiv, bewusst kein Fehler (Log: /tmp/opencode-sso-proxy.log)"
      else
        echo "  ⚠ SSO-Proxy auf 49375 nicht bereit — Log: /tmp/opencode-sso-proxy.log (NTFY_SESSION_PORT bleibt unverändert)"
      fi
    fi
  fi
fi
# ntfy-Anzeigeoptionen auch in das Service-Env schreiben: set_env_default pflegt
# ausschließlich /etc/opencode.env – diese Datei liest der OpenCode-Server NICHT.
# Dadurch liefen Stummschaltung (NTFY_IGNORE_TITLES) und die übrigen Defaults in
# der Praxis leer, und Hestias Ausfall-Proben kamen als „✅ Fertig …" durch.
# Direkt in service.json (wie NTFY_SESSION_PORT oben) – der Neustart unten greift
# es auf. Ein in /etc/opencode.env eingetragener Wert gewinnt weiterhin.
python3 - <<'PY' >/dev/null 2>&1 || true
import json, os, re
p = os.path.expanduser("~/.config/opencode/service.json")
file_env = {}
try:
    with open("/etc/opencode.env") as fh:
        for line in fh:
            m = re.match(r'^([A-Z0-9_]+)="?(.*?)"?\s*$', line)
            if m:
                file_env[m.group(1)] = m.group(2)
except Exception:
    pass
defaults = {
    "NTFY_IGNORE_TITLES": "Ausfall-Probe,Speed-Probe",
    "NTFY_ICON_BASE": "https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons",
    "NTFY_LINK_STYLE": "session",
    "NTFY_REPLY_LOG": "/tmp/opencode/ntfy-diag.log",
    "NTFY_DEBOUNCE_MS": "0",
    "NTFY_ERROR_THROTTLE_MS": "86400000",
    "NTFY_STATE_FILE": os.path.expanduser("~/.local/share/opencode/ntfy-notify-state.json"),
}
d = json.load(open(p))
e = d.setdefault("env", {})
for k, v in defaults.items():
    e[k] = file_env.get(k) or v
json.dump(d, open(p, "w"), indent=2)
PY
opencode service restart >/dev/null 2>&1 || true
# `ss` ist in den Images nicht installiert – der frühere Check griff nie und
# meldete immer "Binding unklar". Stattdessen die Erreichbarkeit direkt
# prüfen (kurz nachziehen, der Service braucht einen Moment zum Binden).
for _ in $(seq 1 10); do
  curl -fsS --max-time 2 "http://127.0.0.1:49374/" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS --max-time 2 "http://127.0.0.1:49374/" >/dev/null 2>&1 \
  && echo "  ✓ OpenCode-Server: 0.0.0.0:49374" \
  || echo "  ⚠ OpenCode-Server auf 49374 nicht erreichbar"
# SSO-Proxy-Gegenprobe: der curl schickt bewusst KEINE Zugangsdaten mit —
# ein 200 beweist die Basic-Injektion. Der Proxy selbst ist durch
# Coder/Authentik geschützt, weil er nur am Loopback lauscht.
_sso_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:49375/api/info 2>/dev/null)
if [ "$_sso_code" = "200" ]; then
  echo "  ✓ SSO-Proxy ohne Zugangsdaten erreichbar (Authentik-Schutz)"
else
  echo "  ⚠ SSO-Proxy-Check: HTTP ${_sso_code:---} auf 49375 (Log: /tmp/opencode-sso-proxy.log)"
  [ -n "${OPENCODE_SERVER_PASSWORD:-}" ] || echo "    ↳ erwartbar: ohne OPENCODE_SERVER_PASSWORD deshalb SKIP_SECRETS=1, kein Fehler"
fi

# Profil-spezifische Token-Env + Hard-Guardrail (jeder Workspace sieht nur
# seinen eigenen Space; das jeweils andere Token wird aktiv entfernt).
case "$PROFILE" in
  infrastructure|general)
    if [ -n "${OCIS_PRIVATE_USER:-}" ] && [ -n "${OCIS_PRIVATE_TOKEN:-}" ]; then
      opencode service set env OCIS_PRIVATE_USER "$OCIS_PRIVATE_USER" >/dev/null 2>&1 || true
      opencode service set env OCIS_PRIVATE_TOKEN "$OCIS_PRIVATE_TOKEN" >/dev/null 2>&1 || true
      echo "  ✓ oCIS Private-Token im OpenCode-Service gesetzt"
    fi
    if [ -n "${SP_MCP_TOKEN:-}" ]; then
      opencode service set env SP_MCP_TOKEN "$SP_MCP_TOKEN" >/dev/null 2>&1 || true
      echo "  ✓ Super-Productivity-MCP-Token im OpenCode-Service gesetzt"
    fi
    opencode service unset env OCIS_MUELLERCONNECT_USER  >/dev/null 2>&1 || true
    opencode service unset env OCIS_MUELLERCONNECT_TOKEN >/dev/null 2>&1 || true
    ;;
  muellerconnect)
    if [ -n "${OCIS_MUELLERCONNECT_USER:-}" ] && [ -n "${OCIS_MUELLERCONNECT_TOKEN:-}" ]; then
      opencode service set env OCIS_MUELLERCONNECT_USER "$OCIS_MUELLERCONNECT_USER" >/dev/null 2>&1 || true
      opencode service set env OCIS_MUELLERCONNECT_TOKEN "$OCIS_MUELLERCONNECT_TOKEN" >/dev/null 2>&1 || true
      echo "  ✓ oCIS MuellerConnect-Token im OpenCode-Service gesetzt"
    fi
    opencode service unset env OCIS_PRIVATE_USER  >/dev/null 2>&1 || true
    opencode service unset env OCIS_PRIVATE_TOKEN >/dev/null 2>&1 || true
    ;;
  datascience)
    # Der datascience-Workspace sieht AUSSCHLIESSLICH den FernUni-Account.
    if [ -n "${FERNUNI_OCIS_USER:-}" ] && [ -n "${FERNUNI_OCIS_TOKEN:-}" ]; then
      for v in FERNUNI_OCIS_USER FERNUNI_OCIS_TOKEN FERNUNI_OCIS_PW FERNUNI_SPACE_ID \
               FERNUNI_OCIS_BASE MD_USER MD_PW MD_BASE; do
        [ -n "${!v:-}" ] && opencode service set env "$v" "${!v}" >/dev/null 2>&1
      done
      echo "  ✓ FernUni-oCIS + Moodle-Env im OpenCode-Service gesetzt"
    else
      echo "  ⚠ FernUni-oCIS-Zugangsdaten fehlen (secret/data/uni/fernuni-ocis)"
    fi
    opencode service unset env OCIS_PRIVATE_USER  >/dev/null 2>&1 || true
    opencode service unset env OCIS_PRIVATE_TOKEN >/dev/null 2>&1 || true
    opencode service unset env OCIS_MUELLERCONNECT_USER  >/dev/null 2>&1 || true
    opencode service unset env OCIS_MUELLERCONNECT_TOKEN >/dev/null 2>&1 || true
    unset OCIS_PRIVATE_USER OCIS_PRIVATE_TOKEN OCIS_MUELLERCONNECT_USER OCIS_MUELLERCONNECT_TOKEN
    ;;
esac

# ── Herdr-Server + Workspaces ───────────────────────────────────────────────
echo ""
STAMP "OpenCode-Service: fertig"
echo "=== Herdr-Server & Workspaces ==="
export PATH="/root/.local/bin:$PATH"

# ── Herdr-Theme: dauerhaft hell (config.toml [theme], idempotent) ───────────
mkdir -p /root/.config/herdr
python3 - <<'PY' >/dev/null 2>&1
import os
p = "/root/.config/herdr/config.toml"
try:
    with open(p, encoding="utf-8") as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    lines = []
latte = [("panel_bg", "#eff1f5"), ("sidebar_bg", "#e6e9ef"), ("active_row_bg", "#dce0e8"), ("selection_bg", "#ccd0da"), ("accent", "#8839ef"), ("blue", "#1e66f5"), ("green", "#40a02b"), ("red", "#d20f39"), ("yellow", "#df8e1d")]
sections = [
    ("[theme]", [("name", 'name = "rose-pine-dawn"'), ("auto_switch", "auto_switch = false")]),
    ("[theme.custom]", [(k, f'{k} = "{v}"') for k, v in latte]),
]
for header, want in sections:
    start = next((i for i, l in enumerate(lines) if l.strip() == header), None)
    if start is None:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(header)
        lines += [v for _, v in want]
        continue
    end = next((j for j in range(start + 1, len(lines)) if lines[j].lstrip().startswith("[")), len(lines))
    for key, val in want:
        idx = next((j for j in range(start + 1, end) if "=" in lines[j] and lines[j].split("=", 1)[0].strip() == key), None)
        if idx is not None:
            lines[idx] = val
        else:
            lines.insert(end, val)
            end += 1
with open(p, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
os.chmod(p, 0o600)
PY
if [ -x "$HERDR_BIN" ] && "$HERDR_BIN" status 2>/dev/null | grep -q "status: running"; then
  "$HERDR_BIN" server reload-config >/dev/null 2>&1 || true
fi

if [ -x "$HERDR_BIN" ]; then
  if ! "$HERDR_BIN" status 2>/dev/null | grep -q "status: running"; then
    echo "  → Herdr-Server wird gestartet..."
    nohup "$HERDR_BIN" server > /tmp/herdr-server.log 2>&1 &
    # auf den Server warten statt blind sleep 3
    for _ in $(seq 1 20); do
      "$HERDR_BIN" status 2>/dev/null | grep -q "status: running" && break
      sleep 0.5
    done
  fi
  if "$HERDR_BIN" status 2>/dev/null | grep -q "status: running"; then
    echo "  ✓ Herdr-Server läuft"
  else
    echo "  ⚠ Herdr-Server-Start fehlgeschlagen"
  fi

  # Profil-spezifische Herdr-Workspaces (label:relpath:agentname)
  # relpath = "." → Workspace-Basisverzeichnis selbst
  case "$PROFILE" in
    infrastructure)
      WS_BASE="$REPO_DIR/workspaces/infrastructure"
      WS_LIST=("Home Assistant:homeassistant:opencode-ha" "Server Management:server-management:opencode-sm" "Svelte:svelte:opencode-svelte" "Cleve:cleve:opencode-cleve" "Grafana:grafana:opencode-grafana")
      ;;
    general)
      WS_BASE="$REPO_DIR/workspaces/general"
      WS_LIST=("General:.:opencode-general")
      ;;
    muellerconnect)
      WS_BASE="$REPO_DIR/workspaces/minijob"
      WS_LIST=("MuellerConnect:.:opencode-mc")
      ;;
    datascience)
      WS_BASE="$REPO_DIR/workspaces/uni"
      WS_LIST=("Data Science:.:opencode-datascience")
      ;;
  esac

  # Einen Workspace anlegen (idempotent) und den OpenCode-Agent darin starten.
  # Läuft als Hintergrund-Job (bg): die Workspaces sind voneinander unabhängig,
  # daher starten sie parallel statt nacheinander – bei infrastructure (5
  # Workspaces) verkürzt das den Kaltstart um ~15 s. Der Endzustand ist
  # identisch (alle Workspaces laufen, kein Lazy-Start), es wartet nur nichts
  # mehr auf das vorherige. Logs: /tmp/bg-ws-<label>.log
  start_ws() {
    local entry="$1"
    local LABEL REST RELPATH NAME WS_DIR PANE
    LABEL="${entry%%:*}"; REST="${entry#*:}"; RELPATH="${REST%%:*}"; NAME="${REST##*:}"
    if [ "$RELPATH" = "." ]; then WS_DIR="$WS_BASE"; else WS_DIR="$WS_BASE/$RELPATH"; fi

    # Workspace anlegen (idempotent)
    "$HERDR_BIN" workspace list 2>/dev/null | grep -q "$LABEL" \
      || "$HERDR_BIN" workspace create --cwd "$WS_DIR" --label "$LABEL" >/dev/null 2>&1

    # Pane ermitteln und OpenCode starten
    PANE=$("$HERDR_BIN" workspace list 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for w in d['result']['workspaces']:
        if w['label'] == '$LABEL':
            print(w['active_tab_id'].split(':')[0] + ':p1')
            break
except Exception:
    pass
")
    if [ -n "$PANE" ]; then
      if ! "$HERDR_BIN" agent list 2>/dev/null | grep -q "\"$NAME\""; then
        # Abgekoppelt starten (setsid): `agent start` wartet per Default bis zu
        # 30 s darauf, dass der Agent "interactive_ready" meldet (gemessen
        # ~3,9 s). Das ist reine Wartezeit auf einen Prozess, der ohnehin läuft:
        # der Agent wird weiterhin beim Boot gestartet (kein Lazy-Start), nur
        # das Setup wartet nicht darauf. Herdr registriert ihn, sobald er ihn
        # erkannt hat (2026-10-18 mit Probe-Workspace verifiziert: Start
        # überlebt das Skript-Ende und erscheint als interactive_ready).
        # Achtung: mit zu kleinem --timeout bricht der Start ab und der Agent
        # wird GAR NICHT registriert — deshalb kein Timeout-Tuning.
        # Antwort/Fehler pro Workspace: /tmp/bg-ws-start-<label>.log
        setsid nohup "$HERDR_BIN" agent start "$NAME" --kind opencode --pane "$PANE" \
          > "/tmp/bg-ws-start-${LABEL// /-}.log" 2>&1 < /dev/null &
        echo "  → OpenCode-Start angestoßen: $LABEL ($PANE)"
      else
        echo "  ✓ OpenCode läuft bereits: $LABEL"
      fi
    fi
  }

  for entry in "${WS_LIST[@]}"; do
    WS_TAG="${entry%%:*}"
    bg "Workspace ${WS_TAG}" "/tmp/bg-ws-${WS_TAG// /-}.log" start_ws "$entry"
  done
  wait_bg
else
  echo "  ⚠ Herdr nicht installiert – Server und Workspaces übersprungen"
fi

echo ""
echo "=== $PROFILE Workspace bereit ==="
echo "  Öffne die App in Coder."
echo ""

# ── FernUni-Sync (nur datascience) ──────────────────────────────────────────
# fernuni-sync (Python-Standardbibliothek) spiegelt die Moodle-Kurse als
# Obsidian-Notizen + Material in den FernUni-oCIS-Space. systemd-freier Timer:
# einmal beim Workspace-Start, danach alle 6 h (Log: ~/uni/logs/sync.log).
# Zustand (state.json) + config.json + env-Datei liegen im PERSISTENTEN Volume
# ~/uni/state → ein Workspace-Neustart lädt nichts erneut ("0 Downloads").
if [ "$PROFILE" = "datascience" ]; then
  echo ""
  echo "=== FernUni-Sync (Moodle → oCIS) ==="
  UNI_DIR="${UNI_DIR:-/home/carstenmueller2002/uni}"
  SYNC_PY="$REPO_DIR/fernuni-sync/sync.py"
  SYNC_LOOP="$REPO_DIR/coder-templates/datascience/sync-loop.sh"
  SYNC_STATE_DIR="$UNI_DIR/state"
  SYNC_STATE="$SYNC_STATE_DIR/state.json"
  SYNC_CONFIG="$SYNC_STATE_DIR/fernuni-sync.config.json"
  SYNC_ENV="$SYNC_STATE_DIR/fernuni-sync.env"
  SYNC_LOG="$UNI_DIR/logs/sync.log"
  # Lokale Spiegelung: derselbe Ordner, den der FernUni-Compose-Stack als
  # Jupyter-Wurzel einbindet (./workspace -> ~/uni/module/63123-.../workspace).
  # Der Sync spiegelt dorthin <slug>/<Abschnitt>/<Datei> aus dem Vault.
  SYNC_LOCAL_ROOT="${FERNUNI_LOCAL_ROOT:-$UNI_DIR/workspace/Material}"
  # ~/uni/workspace ist der stabile Einstiegspunkt dorthin (uni-bootstrap.sh
  # setzt denselben Symlink; hier nur der Nachlauf für einen frischen Workspace).
  DE_WS_DIR="$UNI_DIR/module/63123-data-engineering-fuer-data-science/workspace"
  if [ -d "$DE_WS_DIR" ] && [ ! -e "$UNI_DIR/workspace" ]; then
    ln -sfn "$DE_WS_DIR" "$UNI_DIR/workspace"
  fi
  mkdir -p "$SYNC_STATE_DIR" "$UNI_DIR/logs" "$SYNC_LOCAL_ROOT"

  if [ -f "$SYNC_PY" ] && [ -f "$SYNC_LOOP" ]; then
    # Env-Datei (600) für --env-file. Bewusst UNQUOTED und KEIN `. datei`:
    # nur sync.py liest sie, so bleibt ein '$' in Passwort/Space-ID intakt.
    umask 077
    {
      echo "# von startup.sh erzeugt – Werte aus OpenBao (secret/data/uni/*). Nicht per Shell sourcen."
      for v in MD_USER MD_PW MD_BASE FERNUNI_OCIS_USER FERNUNI_OCIS_TOKEN \
               FERNUNI_OCIS_PW FERNUNI_SPACE_ID FERNUNI_OCIS_BASE; do
        [ -n "${!v:-}" ] && printf '%s=%s\n' "$v" "${!v}"
      done
    } > "$SYNC_ENV"
    chmod 600 "$SYNC_ENV"
    umask 022

    # config.json mit state_file im persistenten Volume (Default wäre ~/.local/…)
    # und local_root = Jupyter-Wurzel des Stacks (leer/0 = Spiegelung aus).
    python3 - "$REPO_DIR/fernuni-sync/config.json" "$SYNC_CONFIG" "$SYNC_STATE" \
             "$SYNC_LOCAL_ROOT" << 'PY'
import json, sys
src, dst, state, lokal = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(src, encoding="utf-8") as fh:
    cfg = json.load(fh)
cfg["state_file"] = state
cfg["local_root"] = lokal          # Material-Baum zusaetzlich lokal spiegeln
with open(dst, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2, ensure_ascii=False)
PY

    if pgrep -f "sync-loop.sh" >/dev/null 2>&1; then
      echo "  ✓ Sync-Loop läuft bereits"
    else
      nohup bash "$SYNC_LOOP" "$SYNC_PY" "$SYNC_CONFIG" "$SYNC_ENV" "$SYNC_LOG" \
        >> "$SYNC_LOG" 2>&1 &
      echo "  ✓ Sync-Loop gestartet (sofort + alle 6 h, Log: $SYNC_LOG)"
    fi
  else
    echo "  ⚠ fernuni-sync fehlt (Repo-Klon ohne fernuni-sync/?) – Sync übersprungen"
  fi
fi

# ~ == HA-Kleve TCP-Relay (fuer Container ohne Tailscale, z. B. hermes) == ~
HA_FORWARD_SCRIPT="/home/carstenmueller2002/opencode-coder-env/coder-templates/shared/ha-forward.py"
if [ -f "$HA_FORWARD_SCRIPT" ]; then
  if ! curl -sS --max-time 3 -o /dev/null http://127.0.0.1:18123/api/ 2>/dev/null; then
    echo "  > HA-Kleve TCP-Relay starten (127.0.0.1:18123 -> 192.168.178.52:8123)..."
    pkill -f "ha-forward.py" 2>/dev/null || true
    nohup python3 "$HA_FORWARD_SCRIPT" 18123 192.168.178.52 8123 > /tmp/ha-forward.log 2>&1 &
    sleep 1
  else
    echo "  > HA-Kleve TCP-Relay laeuft bereits"
  fi
fi

echo ""

# Dauer des gesamten Setups (Messbasis: SETUP_T0 aus dem Parallel-Setup-Block)
if [ "$PROFILE" = "infrastructure" ]; then
  if tailscale --socket="$TS_SOCK" status >/dev/null 2>&1; then
    echo "  ✓ Tailscale aktiv: $(tailscale --socket="$TS_SOCK" ip -4 2>/dev/null | head -1)"
  else
    echo "  ⚠ Tailscale-Status nicht abrufbar"
  fi
fi
# Hinweis auf gesicherte Arbeit: entrypoint.sh legt vor dem Repo-Reset einen
# Auto-Stash an, wenn uncommittete Änderungen vorhanden sind — hier sichtbar
# machen, sonst bleibt wiederherstellbare Arbeit unbemerkt liegen.
if [ -d "$REPO_DIR/.git" ]; then
  STASHED=$(git -C "$REPO_DIR" stash list 2>/dev/null | head -3 || true)
  if [ -n "$STASHED" ]; then
    echo ""
    echo "  ⚠ Uncommittete Änderungen im Stash gesichert (git stash list / git stash pop):"
    echo "$STASHED" | sed 's/^/     /'
  fi
fi
STAMP "Herdr + Workspaces: fertig"
echo "=== Setup abgeschlossen in $((SECONDS-SETUP_T0))s ($PROFILE) ==="
