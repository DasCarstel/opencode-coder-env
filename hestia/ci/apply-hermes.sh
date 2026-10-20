#!/usr/bin/env bash
# apply-hermes.sh — Hermes-Agent (Nous Research) aus der SOT steuern.
#
# Läuft auf dem Self-hosted Runner auf dem Docker-Host, angestoßen von
# hestia/backend/hermes.py (Panel → POST /api/hermes/apply) oder von
# accounts.py nach einem Konto-Wechsel (pool.followActive = true).
# Quelle der Wahrheit ist .opencode/hestia-hermes.jsonc aus dem Checkout.
#
# 1) config.yaml vorher sichern
# 2) model.provider + model.default über die Hermes-CLI setzen
# 3) fallback_providers als YAML-Liste schreiben (die CLI erzeugt sonst ein Dict,
#    das Hermes ignoriert) — bestehenden Block entfernen, neuen anhängen
# 4) API_SERVER_ENABLED/PORT/HOST aus der SOT nach /opt/data/.env schreiben
#    (immer, unabhaengig vom Pool; API_SERVER_KEY bleibt unangetastet)
# 4b) erweiterte Sektionen (agent/providers/platform_toolsets/platforms/skills)
#     per Deep-Merge in die config.yaml — alles andere bleibt unangetastet
# 4d) Fastpath-Skripte (hestia/scripts/*_fast.py → /opt/data/bin) und Skills
#     (notion-database, obsidian-ocis → /opt/data/skills) materialisieren —
#     tar-over-ssh-stdin, ohne fremde Dateien zu löschen, ohne Neustart
# 4e) API-Tool-Oberfläche: Default-MCP-Server aus dem API-Turn entfernen
#     (idempotenter Quell-Patch, Backup + Syntaxprüfung; Neustart bei Änderung)
# 5) optional den Abo-Pool (OPENCODE_GO_API_KEY*) aus OpenBao neu setzen
# 5a) OPENCODE_SERVER_PASSWORD aus OpenBao (secret/data/mcp/hermes-opencode-service)
#     in /opt/data/.env mergen — bewusst NUR Hermes; startup.sh und die
#     OpenCode-Config bleiben unangetastet
# 6) dasselbe für jedes weitere SOT-Profil ("profiles") — der Multiplexer
#    bedient mehrere Profile mit je eigenen Dateien unter /opt/data/profiles/
# 7) hermes config check, optionaler Neustart + Health-Wait, Zusammenfassung
#
# Sicherheit: die API-Keys stehen nie im Log, nie in einer Kommandozeile und nie
# im Workflow-Log — sie wandern ausschließlich über stdin.
#
# Env:
#   SOT_PATH              Default .opencode/hestia-hermes.jsonc
#   HESTIA_ACCOUNTS_PATH  Default <Verzeichnis von SOT_PATH>/hestia-accounts.jsonc
#                         Quelle des aktiven opencode-go-Abos (Feld "active")
#   HERMES_CONTAINER      Default hermes
#   OPENBAO_ADDR          Default https://openbao.mueller-nas.de
#   OPENBAO_TOKEN_FILE    Default /opt/docker-compose/github-runner/openbao-switch.token
#   OPENBAO_ROLE_ID       optional — AppRole-Login für den Service-Passwort-Sync (5a)
#   OPENBAO_APPROLE_SECRET_ID  optional — geht nur per stdin an curl, nie in argv
#   HERMES_SYNC_SERVICE_PASSWORD  Default true; false = .env-Passwort unangetastet
#   CODER_SSH_HOST        Default 10.0.10.17
#   CODER_SSH_KEY         Default ~/.ssh/id_ed25519_coderdeploy
#   SINGLE                true = nur das aktive Abo in den Pool (Default false)
set -euo pipefail

SOT_PATH="${SOT_PATH:-.opencode/hestia-hermes.jsonc}"
HERMES_CONTAINER="${HERMES_CONTAINER:-hermes}"
OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
OPENBAO_TOKEN_FILE="${OPENBAO_TOKEN_FILE:-/opt/docker-compose/github-runner/openbao-switch.token}"
CODER_SSH_HOST="${CODER_SSH_HOST:-10.0.10.17}"
CODER_SSH_KEY="${CODER_SSH_KEY:-$HOME/.ssh/id_ed25519_coderdeploy}"
# SINGLE=true → Pool besteht NUR aus dem Key des aktiven Abos (OPENCODE_GO_API_KEY).
# Wird von accounts.py gesetzt, wenn das aktive Abo bald abläuft.
SINGLE="${SINGLE:-false}"
SECRET_PATH="secret/data/mcp/opencode-go"
# Hermes-only: OPENCODE_SERVER_PASSWORD (API/Voice-Auth gegen den OpenCode-Service)
# aus diesem Pfad in die Container-.env mergen. Deaktivierbar mit
# HERMES_SYNC_SERVICE_PASSWORD=false — dann bleibt der bestehende Wert stehen.
SERVICE_SECRET_PATH="secret/data/mcp/hermes-opencode-service"
HERMES_SYNC_SERVICE_PASSWORD="${HERMES_SYNC_SERVICE_PASSWORD:-true}"
# SOT der Konten — dort steht der Name des aktiven opencode-go-Abos (default1..3).
HESTIA_ACCOUNTS_PATH="${HESTIA_ACCOUNTS_PATH:-${SOT_PATH%/*}/hestia-accounts.jsonc}"
CONFIG_IN_CONTAINER="/opt/data/config.yaml"
ENV_IN_CONTAINER="/opt/data/.env"
HERMES_BIN="/opt/hermes/bin/hermes"
# Der Login-Shell-PATH (sh -lc) zeigt auf /usr/local/bin/python3 ohne
# ruamel.yaml; die Hermes-venv hat ruamel. Für den Config-Merge explizit
# diesen Interpreter verwenden.
HERMES_PYTHON="${HERMES_PYTHON:-/opt/hermes/.venv/bin/python3}"
# /api/health antwortet auch ohne Auth mit ok/version — reicht als Restart-Indicator.
HEALTH_PATH="/api/health"

SSH=(ssh -i "$CODER_SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "root@${CODER_SSH_HOST}")

WORK="$(mktemp -d -t hermes-apply.XXXXXX)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# --- 1) SOT lesen -------------------------------------------------------------
if [[ ! -r "$SOT_PATH" ]]; then
  echo "✗ SOT nicht lesbar: $SOT_PATH" >&2
  exit 1
fi
# Ein Helper für die ganze SOT:jq ist auf dem Runner nicht garantiert.
# IFS=Tabulator, weil die JSON-Werte Leerzeichen enthalten.
IFS=$'\t' read -r PRIMARY_PROVIDER PRIMARY_MODEL FALLBACKS_JSON POOL_FOLLOW RESTART ENABLED PROFILES_JSON \
  API_ENABLED API_PORT API_HOST AGENT_JSON PROVIDERS_JSON TOOLSETS_JSON PLATFORMS_JSON \
  SKILLS_JSON < <(
  python3 - "$SOT_PATH" <<'PY'
import json, sys
try:
    from hjsonc import parse
except ImportError:  # hjsonc ist im Runner nicht installiert → Kommentare strippen
    def parse(text):
        return json.loads("\n".join(l.split("//", 1)[0] for l in text.splitlines()))
try:
    cfg = parse(open(sys.argv[1], encoding="utf-8").read()) or {}
except Exception as e:
    sys.stderr.write(f"SOT nicht lesbar: {e}\n")
    sys.exit(1)
p = cfg.get("primary") or {}
pool_top = cfg.get("pool") or {}
# API-Server (fehlt er die Sektion, greifen die Defaults — abwaertskompatibel).
api = cfg.get("api_server") if isinstance(cfg.get("api_server"), dict) else {}
try:
    api_port = int(api.get("port", 8642))
except (TypeError, ValueError):
    api_port = 8642
if not 1 <= api_port <= 65535:
    api_port = 8642
api_host = str(api.get("host") or "0.0.0.0").strip() or "0.0.0.0"


def norm_fb(raw):
    out = []
    for f in (raw or []):
        if not isinstance(f, dict):
            continue
        if f.get("provider") and f.get("model"):
            out.append({"provider": str(f["provider"]), "model": str(f["model"])})
    return out


fb = norm_fb(cfg.get("fallback"))

# Weitere Profile: jedes erbt vom Top-Level, was es nicht selbst setzt.
profiles = []
for name, prof in (cfg.get("profiles") or {}).items():
    if not isinstance(prof, dict):
        continue
    if prof.get("enabled") is False:
        continue
    pp = prof.get("primary") if isinstance(prof.get("primary"), dict) else {}
    pool_p = prof.get("pool") if isinstance(prof.get("pool"), dict) else {}
    prof_fb = norm_fb(prof["fallback"]) if "fallback" in prof else fb
    profiles.append({
        "name": str(name),
        "provider": str(pp.get("provider") or p.get("provider") or ""),
        "model": str(pp.get("model") or p.get("model") or ""),
        "fallback": json.dumps(prof_fb, separators=(",", ":")),
        "pool_follow": "true" if (
            pool_p.get("followActive") if "followActive" in pool_p
            else pool_top.get("followActive")
        ) else "false",
        "pool_only_active": "true" if (
            pool_p.get("onlyActiveWhenExpiring") if "onlyActiveWhenExpiring" in pool_p
            else pool_top.get("onlyActiveWhenExpiring")
        ) else "false",
    })

print("\t".join([
    str(p.get("provider") or ""),
    str(p.get("model") or ""),
    json.dumps(fb, separators=(",", ":")),
    "true" if pool_top.get("followActive") else "false",
    "false" if cfg.get("restart") is False else "true",
    "true" if cfg.get("enabled", True) else "false",
    json.dumps(profiles, separators=(",", ":")),
    "true" if api.get("enabled", True) else "false",
    str(api_port),
    api_host,
    json.dumps(cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {},
               separators=(",", ":")),
    json.dumps(cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {},
               separators=(",", ":")),
    json.dumps(cfg.get("platform_toolsets")
               if isinstance(cfg.get("platform_toolsets"), dict) else {},
               separators=(",", ":")),
    json.dumps(cfg.get("platforms") if isinstance(cfg.get("platforms"), dict) else {},
               separators=(",", ":")),
    json.dumps(cfg.get("skills") if isinstance(cfg.get("skills"), dict) else {},
               separators=(",", ":")),
]))
PY
)

if [[ "$ENABLED" != "true" ]]; then
  echo "→ SOT ist deaktiviert (enabled=false) — nichts zu tun."
  exit 0
fi
if [[ -z "$PRIMARY_PROVIDER" || -z "$PRIMARY_MODEL" ]]; then
  echo "✗ SOT ohne primary.provider/primary.model" >&2
  exit 1
fi
echo "→ Primary: $PRIMARY_PROVIDER/$PRIMARY_MODEL"
echo "→ Fallbacks: $FALLBACKS_JSON"
echo "→ Pool folgt aktivem Konto: $POOL_FOLLOW · nur aktives Abo: $SINGLE · Restart: $RESTART"
if [[ "$PROFILES_JSON" != "[]" ]]; then
  echo "→ Weitere Profile: $PROFILES_JSON"
fi

# Kurzer Health-Check: Container muss laufen, sonst ist alles sinnlos.
if ! "${SSH[@]}" "docker inspect -f '{{.State.Running}}' '$HERMES_CONTAINER' 2>/dev/null" | grep -q true; then
  echo "✗ Container '$HERMES_CONTAINER' läuft nicht (auf $CODER_SSH_HOST)" >&2
  exit 1
fi

# Ist-Zustand festhalten, BEVOR irgendetwas geschrieben wird: nur wenn sich
# config.yaml bzw. .env danach tatsächlich unterscheiden, wird Hermes neu
# gestartet. Der 30-Minuten-Abgleich aus Hestia setzte den Container sonst bei
# jedem Lauf neu auf — inklusive Shutdown-Meldung nach ntfy und Abbruch der
# laufenden Aufgabe, obwohl sich nichts geändert hatte (16 Meldungen in einer
# Nacht). Gehasht werden nur die Dateien, die Hermes liest (inkl. Profile).
hermes_state_hash() {
  "${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'find /opt/data -maxdepth 3 \( -name \"config.yaml\" -o -name \".env\" \) -type f 2>/dev/null | sort | xargs cat 2>/dev/null | sha256sum'" 2>/dev/null | awk '{print $1}'
}
STATE_BEFORE="$(hermes_state_hash)"
echo "→ Ist-Zustand (config.yaml + .env) vor dem Schreiben: ${STATE_BEFORE:-<nicht lesbar>}"

# --- 2) Backup ---------------------------------------------------------------
echo "→ Backup der aktuellen config.yaml"
"${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'cp -a $CONFIG_IN_CONTAINER $CONFIG_IN_CONTAINER.bak-hestia-\$(date +%s)'"
"${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'ls -1 $CONFIG_IN_CONTAINER.bak-hestia-* 2>/dev/null | tail -1'" | sed 's/^/   Backup: /'

# --- 3) Primary über die Hermes-CLI setzen -----------------------------------
# Die CLI schreibt die YAML-Struktur korrekt — das ist der bevorzugte Weg.
"${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'HOME=/opt/data $HERMES_BIN config set model.provider $PRIMARY_PROVIDER'"
"${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'HOME=/opt/data $HERMES_BIN config set model.default $PRIMARY_MODEL'"

# --- 4) Fallbacks als YAML-Liste schreiben -----------------------------------
# "hermes config set fallback_providers.N.x" legt ein Dict an und Hermes ignoriert
# es → den Block hier zeilenweise entfernen und die neue Liste anhängen.
cat >"$WORK/fallbacks.py" <<'PY'
"""Ersetzt den fallback_providers-Block in der YAML-Config (auf stdin) durch
eine echte YAML-Liste. Block = von der Zeile 'fallback_providers:' bis zur
nächsten Top-Level-Key-Zeile (Spalte 0 + ':')."""
import json, re, sys

FB_KEY = "fallback_providers"


def yaml_scalar(v):
    v = str(v)
    if re.search(r"[:#\-{}\[\],&*?|<>=!%@`\"'\s]", v):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def main():
    text = sys.stdin.read()
    entries = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else []
    lines = text.splitlines()

    out, i, removed = [], 0, False
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^" + FB_KEY + r"\s*:", line)
        if m:
            removed = True
            i += 1
            # Folgezeilen einrücken (Listenelemente, Kommentare) überspringen.
            # Ältere Writer schrieben die Liste auch ohne Einrückung; auch
            # solche Top-Level-"- provider"-Zeilen gehören zum Block.
            while i < len(lines) and (
                lines[i].strip() == ""
                or lines[i][:1] in (" ", "\t")
                or lines[i].startswith("- ")
            ):
                i += 1
            continue
        out.append(line)
        i += 1

    # Aufräumen: Eine YAML-Root-Map darf keine verwaiste Top-Level-Liste
    # enthalten. Solche Reste konnten entstehen, wenn ein alter Writer den
    # fallback_providers-Kopf entfernte, die unindizierten Listeneinträge aber
    # stehen ließ. Nur Listen unter einem echten Top-Level-Key bleiben erhalten.
    cleaned, i = [], 0
    while i < len(out):
        line = out[i]
        if line.startswith("- provider:"):
            j = len(cleaned) - 1
            while j >= 0 and (cleaned[j].strip() == "" or cleaned[j].lstrip().startswith("#")):
                j -= 1
            parent = cleaned[j] if j >= 0 else ""
            if not re.match(r"^[A-Za-z0-9_.-]+\s*:\s*$", parent):
                i += 1
                while i < len(out) and (
                    out[i].strip() == "" or out[i][:1] in (" ", "\t")
                ):
                    i += 1
                continue
        cleaned.append(line)
        i += 1
    out = cleaned

    while out and out[-1].strip() == "":
        out.pop()

    if entries:
        out.append("")
        out.append(FB_KEY + ":")
        for e in entries:
            out.append(f"  - provider: {yaml_scalar(e['provider'])}")
            out.append(f"    model: {yaml_scalar(e['model'])}")

    sys.stdout.write("\n".join(out) + "\n")


main()
PY

# Schreibt eine beliebige config.yaml im Container: Fallback-Liste ersetzen.
# $1 = Pfad im Container, $2 = Fallback-JSON, $3 = Label für die Logs.
write_fallbacks() {
  local cfg="$1" fb_json="$2" label="$3"
  local base
  base="$(basename "$cfg")"
  "${SSH[@]}" "docker exec '$HERMES_CONTAINER' cat $cfg" \
    | python3 "$WORK/fallbacks.py" "$fb_json" > "$WORK/$base.new"
  "${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > $cfg'" < "$WORK/$base.new"
  "${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'chown hermes:hermes $cfg; chmod 600 $cfg'"
  echo "  → [$label] fallback_providers ersetzt (alter Block entfernt, neue Liste angehängt)"
}

# Prüft eine geschriebene YAML-Datei mit dem Hermes-venv-Parser. Ein defektes
# Profil darf nicht als erfolgreicher Apply durchgehen.
validate_yaml() {
  local cfg="$1" label="$2"
  if ! "${SSH[@]}" "docker exec '$HERMES_CONTAINER' '$HERMES_PYTHON' -c 'from ruamel.yaml import YAML; import sys; d=YAML(typ=\"safe\").load(open(sys.argv[1])); sys.exit(0 if isinstance(d, dict) else 1)' '$cfg'" >/dev/null 2>&1; then
    echo "✗ [$label] YAML ungültig oder Root keine Map: $cfg" >&2
    return 1
  fi
}

write_fallbacks "$CONFIG_IN_CONTAINER" "$FALLBACKS_JSON" "default"
validate_yaml "$CONFIG_IN_CONTAINER" "default"
echo "→ Fallback-Kette laut Hermes:"
"${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'HOME=/opt/data $HERMES_BIN fallback list'" | sed 's/^/   /'

# --- 4b) API-Server-Variablen aus der SOT ------------------------------------
# Schreibt API_SERVER_ENABLED/PORT/HOST nach /opt/data/.env. Wird UNABHAENGIG
# vom Pool-Ansatz ausgefuehrt (pool.followActive=false darf die .env nicht
# unangetastet lassen). API_SERVER_KEY ist ein Secret und wird hier bewusst
# NICHT angefasst — der Merge laesst alle anderen Zeilen unverändert durch.
# Aufruf: write_api_server <.env-Pfad im Container> <label>
write_api_server() {
  local env_file="$1" label="$2"
  local base
  base="$(basename "$env_file")"
  # Aktuelle .env holen und die drei API_SERVER_*-Zeilen ersetzen/ergänzen.
  # Bewusst strenger als apply_pool: diese Funktion läuft bei JEDEM Apply. Schlägt
  # das Lesen fehl oder kommt leeres Ergebnis, brechen wir ab — sonst würden wir
  # eine .env mit nur drei Zeilen schreiben und API_SERVER_KEY + Pool-Keys verlieren.
  if ! "${SSH[@]}" "docker exec '$HERMES_CONTAINER' cat $env_file" > "$WORK/$base.apisrv.old"; then
    echo "✗ [$label] $env_file nicht lesbar — API-Server nicht geschrieben (bestehende .env unveraendert)" >&2
    return 1
  fi
  if [[ ! -s "$WORK/$base.apisrv.old" ]]; then
    echo "✗ [$label] $env_file ist leer — Abbruch, um keine Secrets zu ueberschreiben (bestehende .env unveraendert)" >&2
    return 1
  fi
  python3 - "$WORK/$base.apisrv.old" "$API_ENABLED" "$API_PORT" "$API_HOST" > "$WORK/$base.apisrv.new" <<'PY'
import re, sys

enabled = sys.argv[2]
port = sys.argv[3]
host = sys.argv[4]
old = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()

mapping = {
    "API_SERVER_ENABLED": "true" if enabled == "true" else "false",
    "API_SERVER_PORT": port,
    "API_SERVER_HOST": host,
}

out, seen = [], set()
for line in old:
    m = re.match(r"^(API_SERVER_(ENABLED|PORT|HOST))\s*=", line)
    if m:
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(f"{name}={mapping[name]}")
        continue
    out.append(line)          # alles andere — auch API_SERVER_KEY! — bleibt

for name, value in mapping.items():      # fehlende Zeilen ergaenzen
    if name not in seen:
        out.append(f"{name}={value}")
        seen.add(name)

sys.stdout.write("\n".join(out) + "\n")
PY
  # Sicherheitsnetz: bevor die .env ueberschrieben wird, muessen die gesetzten
  # Werte plausibel sein — sonst bricht der Lauf ab und die .env bleibt wie
  # sie ist (Backup/Review noetig).
  if ! python3 - "$WORK/$base.apisrv.new" <<'PY'
import re, sys

BAD_HOST = re.compile(r"^[A-Za-z0-9_.:\[\]-]+$")
bad = []
for line in open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines():
    m = re.match(r"^(API_SERVER_(ENABLED|PORT|HOST))\s*=\s*(.*)$", line)
    if not m:
        continue
    name, value = m.group(1), m.group(3).strip().strip('"').strip("'")
    if name == "API_SERVER_ENABLED":
        if value.lower() not in ("true", "false"):
            bad.append(f"{name}={value!r} (erlaubt: true|false)")
    elif name == "API_SERVER_PORT":
        if not value.isdigit() or not 1 <= int(value) <= 65535:
            bad.append(f"{name}={value!r} (erlaubt: 1-65535)")
    else:
        if not value or not BAD_HOST.match(value):
            bad.append(f"{name}={value!r} (kein plausibler Host/IP)")
if bad:
    sys.stderr.write("✗ API-Server-Werte ungueltig: " + ", ".join(bad) + "\n")
    sys.exit(1)
PY
  then
    echo "✗ [$label] API-Server nicht geschrieben — bestehende .env bleibt unveraendert (Backup/Review noetig)" >&2
    return 1
  fi
  # In-place überschreiben (ein mv auf /opt/data scheitert im Volume).
  "${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > $env_file && chown hermes:hermes $env_file && chmod 600 $env_file'" <"$WORK/$base.apisrv.new"
  echo "✓ [$label] API-Server gesetzt: enabled=$API_ENABLED · $API_HOST:$API_PORT (API_SERVER_KEY unangetastet)"
}

# --- 4d) Fastpath-Skripte + Skills installieren -------------------------------
# API-/Voice-Zugriff läuft laut SOT (.opencode/hestia-hermes.jsonc) über
# /opt/data/bin/*_fast.py — daher werden die Skripte und die beiden Skills hier
# mit installiert, damit der Container-Baum zum Checkout passt. Idempotent:
# tar überschreibt vorhandene Dateien, löscht aber KEINE fremden Skills. Die
# Dateien fließen nicht in den Neustart-Hash ein (nur config.yaml/.env) —
# ein reiner Datei-Abgleich löst also keinen Hermes-Neustart aus.
install_fast_paths() {
  local repo_root stage
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  stage="$WORK/fastpath"
  mkdir -p "$stage/bin" "$stage/skills"
  local script
  for script in notion_fast.py obsidian_fast.py; do
    if [[ ! -r "$repo_root/hestia/scripts/$script" ]]; then
      echo "✗ Fastpath fehlt im Checkout: hestia/scripts/$script" >&2
      return 1
    fi
    cp -a "$repo_root/hestia/scripts/$script" "$stage/bin/$script"
    chmod 0755 "$stage/bin/$script"
  done
  local skill
  for skill in notion-database obsidian-ocis; do
    if [[ ! -d "$repo_root/hestia/skills/$skill" ]]; then
      echo "✗ Skill fehlt im Checkout: hestia/skills/$skill" >&2
      return 1
    fi
    rm -rf "$stage/skills/$skill"
    cp -a "$repo_root/hestia/skills/$skill" "$stage/skills/$skill"
  done
  if ! "${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'command -v tar'" >/dev/null 2>&1; then
    echo "✗ tar fehlt im Container '$HERMES_CONTAINER' — Fastpath-Installation nicht möglich" >&2
    return 1
  fi
  # tar-over-ssh-stdin: kleine Dateien, keine Secrets. Extraktion als root,
  # damit Besitzer/Modi gesetzt werden können; bestehende Dateien werden nur
  # überschrieben, nichts gelöscht.
  if ! tar -C "$stage" -cf - bin skills \
      | "${SSH[@]}" "docker exec -i -u 0 '$HERMES_CONTAINER' tar -x -C /opt/data"; then
    echo "✗ Fastpath-Installation fehlgeschlagen (tar-over-ssh)" >&2
    return 1
  fi
  "${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'chown hermes:hermes /opt/data/bin /opt/data/skills /opt/data/bin/notion_fast.py /opt/data/bin/obsidian_fast.py /opt/data/skills/notion-database /opt/data/skills/obsidian-ocis && chown -R hermes:hermes /opt/data/skills/notion-database /opt/data/skills/obsidian-ocis && chmod 0755 /opt/data/bin/notion_fast.py /opt/data/bin/obsidian_fast.py'"
  echo "✓ Fastpath installiert: bin/{notion,obsidian}_fast.py (0755) · skills/{notion-database,obsidian-ocis} inkl. references/ (keine fremden Skills gelöscht)"
}

# --- 4e) API-Tool-Oberfläche auf die explizite Allowlist begrenzen -----------
# Hermes _get_platform_tools() hängt mit dem Default
# include_default_mcp_servers=True ALLE Default-MCP-Server an die Plattform-
# Liste. /v1/toolsets nutzt bereits False; der API-Turn muss denselben Filter
# verwenden, sonst sind Notion/oCIS trotz Allowlist über tool_search erreichbar.
# Einmaliger, idempotenter Quell-Patch mit Backup und Syntaxprüfung; kein Secret.
# Erfordert einen Neustart, den die Restart-Logik unten über API_SURFACE_PATCHED
# auch ohne config/.env-Änderung auslöst.
API_SURFACE_PATCHED=0
patch_api_tool_surface() {
  cat >"$WORK/patch-api-surface.py" <<'PY'
import os
import shutil
import sys
import time

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/hermes/gateway/platforms/api_server.py"
old = 'enabled_toolsets = sorted(_get_platform_tools(user_config, "api_server"))'
new = 'enabled_toolsets = sorted(_get_platform_tools(user_config, "api_server", include_default_mcp_servers=False))'

with open(path, encoding="utf-8") as handle:
    text = handle.read()

if new in text:
    print("api-surface: already")
    raise SystemExit(0)

count = text.count(old)
if count != 1:
    print(f"api-surface: expected exactly 1 old call, found {count}", file=sys.stderr)
    raise SystemExit(1)

updated = text.replace(old, new, 1)
# Vor dem Schreiben syntaktisch prüfen; die Datei wird nie halb geschrieben.
compile(updated, path, "exec")

backup = path + ".bak-hestia-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(path, backup)
try:
    tmp = path + ".tmp-hestia"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(updated)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
except OSError:
    # Falls /opt/hermes nicht atomar ersetzt werden kann: in-place schreiben.
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(updated)
    try:
        os.remove(path + ".tmp-hestia")
    except FileNotFoundError:
        pass
    os.chmod(path, 0o644)

print("api-surface: changed; backup=" + backup)
PY
  local result
  result="$("${SSH[@]}" "docker exec -i -u 0 '$HERMES_CONTAINER' $HERMES_PYTHON -" <"$WORK/patch-api-surface.py" 2>&1)" || {
    echo "✗ API-Tool-Oberfläche konnte nicht begrenzt werden: $result" >&2
    return 1
  }
  echo "$result"
  if [[ "$result" == *"api-surface: changed"* ]]; then
    API_SURFACE_PATCHED=1
  fi
}

# --- 5a) OPENCODE_SERVER_PASSWORD aus OpenBao (Hermes-only) -------------------
# Liest secret/data/mcp/hermes-opencode-service (Feld "password") — bevorzugt
# per AppRole (OPENBAO_ROLE_ID + OPENBAO_APPROLE_SECRET_ID), sonst über die
# vorhandene OPENBAO_TOKEN_FILE. Das Passwort wandert ausschließlich über
# stdin/Dateien, nie über argv und nie ins Log. Gemergt wird NUR diese eine
# Zeile in /opt/data/.env — alle anderen Keys (API_SERVER_KEY, Pool-Keys, …)
# bleiben unangetastet. Bewusst Hermes-only: startup.sh und die OpenCode-Config
# werden NICHT angefasst. Ist OpenBao nicht erreichbar bzw. liefert kein
# Passwort, bleibt der bestehende Wert erhalten (Warnung statt Überschreiben
# mit leer). Zu kurze Werte brechen ab, BEVOR die .env überschrieben wird.
sync_hermes_service_password() {
  local env_file="$1" label="$2"
  if [[ "$HERMES_SYNC_SERVICE_PASSWORD" != "true" ]]; then
    echo "→ [$label] Service-Passwort-Sync deaktiviert (HERMES_SYNC_SERVICE_PASSWORD=false)"
    return 0
  fi
  local token=""
  if [[ -n "${OPENBAO_ROLE_ID:-}" && -n "${OPENBAO_APPROLE_SECRET_ID:-}" ]]; then
    # AppRole-Login: die secret_id geht per stdin an curl (--data @-), taucht
    # also weder in argv noch im Workflow-Log auf.
    token="$(printf '{"role_id":"%s","secret_id":"%s"}' "$OPENBAO_ROLE_ID" "$OPENBAO_APPROLE_SECRET_ID" \
      | curl -sS -H 'Content-Type: application/json' --data @- "$OPENBAO_ADDR/v1/auth/approle/login" 2>/dev/null \
      | python3 -c 'import json, sys
try:
    print((json.load(sys.stdin).get("auth") or {}).get("client_token") or "")
except Exception:
    print("")')" || token=""
  elif [[ -r "$OPENBAO_TOKEN_FILE" ]]; then
    token="$(tr -d ' \r\n' <"$OPENBAO_TOKEN_FILE" 2>/dev/null)" || token=""
  fi
  if [[ -z "$token" ]]; then
    echo "⚠ [$label] OpenBao nicht nutzbar (AppRole unvollständig/fehlgeschlagen oder $OPENBAO_TOKEN_FILE fehlt) — bestehender OPENCODE_SERVER_PASSWORD bleibt" >&2
    return 0
  fi
  curl -sS -H "X-Vault-Token: $token" "$OPENBAO_ADDR/v1/$SERVICE_SECRET_PATH" 2>/dev/null >"$WORK/svc-pw.json" || true
  python3 - "$WORK/svc-pw.json" "$WORK/svc-pw" <<'PY'
import json, sys
pw = ""
try:
    data = (json.load(open(sys.argv[1], encoding="utf-8")).get("data") or {}).get("data") or {}
    pw = str(data.get("password") or "").strip()
except Exception:
    pw = ""
open(sys.argv[2], "w", encoding="utf-8").write(pw)
PY
  if [[ ! -s "$WORK/svc-pw" ]]; then
    echo "⚠ [$label] OpenBao liefert kein Passwort ($SERVICE_SECRET_PATH:password) — bestehender OPENCODE_SERVER_PASSWORD bleibt" >&2
    return 0
  fi
  local svc_len
  svc_len="$(wc -c <"$WORK/svc-pw")"
  if (( svc_len < 12 )); then
    echo "✗ [$label] OPENCODE_SERVER_PASSWORD aus OpenBao unplausibel kurz ($svc_len Zeichen, mind. 12) — .env bleibt unverändert" >&2
    return 1
  fi
  local base
  base="$(basename "$env_file")"
  # Sicherheitsnetz wie bei write_api_server: leere/unlesbare .env nie überschreiben.
  if ! "${SSH[@]}" "docker exec '$HERMES_CONTAINER' cat $env_file" >"$WORK/$base.svc.old" \
     || [[ ! -s "$WORK/$base.svc.old" ]]; then
    echo "✗ [$label] $env_file nicht lesbar oder leer — OPENCODE_SERVER_PASSWORD nicht geschrieben (bestehende .env unveraendert)" >&2
    return 1
  fi
  python3 - "$WORK/$base.svc.old" "$WORK/svc-pw" "$label" >"$WORK/$base.svc.new" <<'PY'
import re, sys

label = sys.argv[3]
old = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
pw = open(sys.argv[2], encoding="utf-8").read()   # exakt das Passwort (ohne Newline)

out, replaced = [], False
for line in old:
    if re.match(r"^OPENCODE_SERVER_PASSWORD\s*=", line):
        if not replaced:                       # nur die erste Zeile ersetzen
            replaced = True
            out.append(f"OPENCODE_SERVER_PASSWORD={pw}")
        continue                               # Dubletten fallen weg
    out.append(line)
if not replaced:
    out.append(f"OPENCODE_SERVER_PASSWORD={pw}")
# nur die Länge loggen — der Wert selbst bleibt geheim
sys.stderr.write(f"  → [{label}] OPENCODE_SERVER_PASSWORD gesetzt ({len(pw)} Zeichen) · alle übrigen Keys unverändert\n")
sys.stdout.write("\n".join(out) + "\n")
PY
  # Sicherheitsnetz: genau eine Zeile, Wert lang genug — sonst Abbruch OHNE
  # Überschreiben der .env (Backup/Review nötig).
  if ! python3 - "$WORK/$base.svc.new" <<'PY'
import re, sys
hits = [l for l in open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
        if re.match(r"^OPENCODE_SERVER_PASSWORD\s*=", l)]
ok = len(hits) == 1 and len(hits[0].split("=", 1)[1].strip()) >= 12
sys.exit(0 if ok else 1)
PY
  then
    echo "✗ [$label] Merge-Ergebnis für OPENCODE_SERVER_PASSWORD unplausibel — bestehende .env bleibt unverändert (Backup/Review nötig)" >&2
    return 1
  fi
  if cmp -s "$WORK/$base.svc.old" "$WORK/$base.svc.new"; then
    echo "→ [$label] OPENCODE_SERVER_PASSWORD bereits aktuell — .env unverändert"
    return 0
  fi
  # Backup VOR dem In-place-Schreiben (ein mv auf /opt/data scheitert im Volume) …
  "${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'cp -a $env_file $env_file.bak-hestia-\$(date +%s)'"
  # … dann in-place überschreiben; nur diese Funktion fasst die Zeile an.
  "${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > $env_file && chown hermes:hermes $env_file && chmod 600 $env_file'" <"$WORK/$base.svc.new"
  echo "✓ [$label] OPENCODE_SERVER_PASSWORD aus OpenBao synchronisiert (Hermes-only · Backup: $env_file.bak-hestia-*)"
}

# Immer anwenden — unabhaengig von POOL_FOLLOW. Faellt die Validierung durch,
# bricht das Skript ab (set -e) und die .env bleibt unveraendert.
write_api_server "$ENV_IN_CONTAINER" "default"

# --- 4c) Erweiterte Sektionen (Hermes Voice) in die config.yaml mergen --------
# agent / providers / platform_toolsets / platforms / skills kommen aus der SOT
# und werden per Deep-Merge in /opt/data/config.yaml geschrieben. Bewusst NUR
# diese fünf Sektionen: mcp_servers, toolsets.enabled (deprecated), tools und
# model.base_url bleiben unangetastet, ebenso alle nicht genannten Keys
# (z. B. platforms.ntfy). Dicts werden rekursiv verschmolzen, Listen und
# Skalare ERSETZT (nie konkateniert — skills.disabled ist also immer exakt die
# SOT-Liste); Kommentare/Reihenfolge bleiben wo moeglich erhalten
# (ruamel.yaml Round-Trip im Container).
ADV_PATCH="$(python3 - "$AGENT_JSON" "$PROVIDERS_JSON" "$TOOLSETS_JSON" "$PLATFORMS_JSON" "$SKILLS_JSON" <<'PY'
import json, sys
names = ("agent", "providers", "platform_toolsets", "platforms", "skills")
patch = {}
for name, raw in zip(names, sys.argv[1:]):
    try:
        value = json.loads(raw) if raw else None
    except Exception:
        value = None
    if isinstance(value, dict) and value:
        patch[name] = value
print(json.dumps(patch, separators=(",", ":")))
PY
)"

ADV_BACKUP=""
if [[ "$ADV_PATCH" != "{}" ]]; then
  # Gewidmetes Backup VOR diesem Merge — stellt der fehlgeschlagene
  # "hermes config check" unten automatisch wieder her (das allgemeine Backup
  # aus Schritt 2 ist aelter als model/fallback-Schritte).
  ADV_BACKUP="${CONFIG_IN_CONTAINER}.bak-adv-$(date +%s)"
  "${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'cp -a $CONFIG_IN_CONTAINER $ADV_BACKUP'"
  echo "→ Backup für erweiterte Sektionen: $ADV_BACKUP"

  cat >"$WORK/adv-merge.py" <<'PY'
"""Deep-Mergt die erweiterten Hestia-Sektionen in die Hermes-config.yaml.

Aufruf: adv-merge.py <patch.json> < config.yaml   (Ausgabe auf stdout)

argv[1] ist eine JSON-Datei
{agent?, providers?, platform_toolsets?, platforms?, skills?}.
Dicts werden rekursiv verschmolzen, Listen und Skalare ersetzt (nie
konkateniert — skills.disabled ist also exakt die SOT-Liste). Alle anderen
Keys (mcp_servers, tools, model, …), Kommentare und die Reihenfolge bleiben
wo moeglich erhalten (ruamel.yaml Round-Trip).
"""
import json, sys

from ruamel.yaml import YAML

PATCH_PATH = sys.argv[1]


def merge(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = value


yaml = YAML(typ="rt")
yaml.preserve_quotes = True
yaml.width = 4096
yaml.allow_unicode = True
yaml.default_flow_style = False

with open(PATCH_PATH, encoding="utf-8") as fh:
    patch = json.load(fh)

cfg = yaml.load(sys.stdin.read()) or {}
merge(cfg, patch)
yaml.dump(cfg, sys.stdout)
PY
  printf '%s' "$ADV_PATCH" >"$WORK/adv-patch.json"
  "${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > /tmp/hestia-adv-merge.py'" <"$WORK/adv-merge.py"
  "${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > /tmp/hestia-adv-patch.json'" <"$WORK/adv-patch.json"
  # Merge IM Container ausführen. Bewusst nicht "python3" aus dem Login-PATH:
  # sh -lc setzt PATH auf /usr/local/bin (ohne ruamel); die Hermes-venv hat es.
  if ! "${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc '$HERMES_PYTHON /tmp/hestia-adv-merge.py /tmp/hestia-adv-patch.json < $CONFIG_IN_CONTAINER'" >"$WORK/config.adv.new" \
     || [[ ! -s "$WORK/config.adv.new" ]]; then
    "${SSH[@]}" "docker exec '$HERMES_CONTAINER' rm -f /tmp/hestia-adv-merge.py /tmp/hestia-adv-patch.json" >/dev/null 2>&1 || true
    echo "✗ Merge der erweiterten Sektionen fehlgeschlagen — config.yaml unverändert (Backup: $ADV_BACKUP)" >&2
    exit 1
  fi
  # In-place zurückschreiben (ein mv auf /opt/data scheitert im Volume).
  "${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > $CONFIG_IN_CONTAINER'" <"$WORK/config.adv.new"
  "${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'chown hermes:hermes $CONFIG_IN_CONTAINER && chmod 600 $CONFIG_IN_CONTAINER'"
  "${SSH[@]}" "docker exec '$HERMES_CONTAINER' rm -f /tmp/hestia-adv-merge.py /tmp/hestia-adv-patch.json"
  echo "✓ Erweiterte Sektionen gemerged: $(printf '%s' "$ADV_PATCH" | python3 -c 'import json,sys; print(", ".join(sorted(json.load(sys.stdin))))')"
else
  echo "→ Keine erweiterten Sektionen (agent/providers/platform_toolsets/platforms/skills) in der SOT — config.yaml hier unverändert"
fi

# --- 5) Abo-Pool aus OpenBao -------------------------------------------------
# Die KEYS (default1..3) kommen aus OpenBao; welches Abo zuerst steht, wird NICHT
# aus dem OpenBao-Feld "active" gelesen (das existiert nicht mehr), sondern aus der
# SOT .opencode/hestia-accounts.jsonc (Feld "active", von Hestia gepflegt).
# Reihenfolge = aktives Abo zuerst, dann die übrigen; die Nummern-Suffixe
# (_2/_3) stehen für die Priorität im Pool (fill_first).
# Wird für das Default-Profil und jedes SOT-Profil mit pool.followActive=true
# benutzt ($1 = .env-Pfad im Container, $2 = Label, $3 = single true|false).
apply_pool() {
  local env_path="$1" label="$2" single="$3"
  if [[ ! -r "$OPENBAO_TOKEN_FILE" ]]; then
    echo "⚠ [$label] pool.followActive=true, aber OpenBao-Token fehlt: $OPENBAO_TOKEN_FILE — Pool unverändert"
    return 0
  fi
  local vault_token tag
  vault_token="$(cat "$OPENBAO_TOKEN_FILE")"
  # Keys holen und direkt in eine Datei schreiben (nie ausgeben).
  # Reihenfolge: aktives Abo zuerst, dann die restlichen Keys alphabetisch.
  if [[ ! -f "$WORK/pool.py" ]]; then
    cat >"$WORK/pool.py" <<'PY'
"""OpenBao-JSON (stdin) → JSON-Array der Keys in Pool-Reihenfolge.

Quelle der KEYS: das OpenBao-Secret (Felder default1..3).
Quelle des aktiven Abos: die SOT .opencode/hestia-accounts.jsonc, Feld "active"
(als 2. Argument übergeben) — NICHT das OpenBao-Feld "active", das es nicht mehr
gibt.

Wichtig: der Name des aktiven Abos (default1..3) ist kein API-Key. Wird er als
Key missverstanden, landet er in OPENCODE_GO_API_KEY → 401 "Invalid credential".

Aufruf: pool.py [single=true|false] [active=default1|default2|default3]
  single=true → nur der Key des aktiven Abos (kein Fallback auf default2/3).
"""
import json, sys
try:
    d = (json.load(sys.stdin).get("data") or {}).get("data") or {}
except Exception:
    d = {}
keys = {k: str(d[k]).strip() for k in sorted(d) if str(d.get(k) or "").strip()}
active = (sys.argv[2].split("=", 1)[1] if len(sys.argv) > 2 and "=" in sys.argv[2] else "").strip()
active_key = keys.get(active, "")
single = len(sys.argv) > 1 and sys.argv[1] == "true"
if single:
    # Nur das aktive Abo — wenn dessen Key fehlt, ist der Pool bewusst leer.
    pool = [active_key] if active_key else []
else:
    pool = ([active_key] if active_key else []) + [v for k, v in keys.items() if k != active]
sys.stdout.write(json.dumps(pool))
PY
  fi
  # Eigene Arbeitsdateien je Profil, damit parallele Pfade sich nicht mischen.
  tag="$(printf '%s' "$label" | tr -c 'A-Za-z0-9' '_')"
  curl -sS -H "X-Vault-Token: $vault_token" "$OPENBAO_ADDR/v1/$SECRET_PATH" \
    | python3 "$WORK/pool.py" "$single" "active=$SOT_ACTIVE_ACCOUNT" > "$WORK/pool.$tag.json"
  # Aktuelle .env holen und die OPENCODE_GO_API_KEY-Zeilen ersetzen.
  "${SSH[@]}" "docker exec '$HERMES_CONTAINER' cat $env_path" > "$WORK/env.$tag.old" || true
  python3 - "$WORK/env.$tag.old" "$WORK/pool.$tag.json" "$label" > "$WORK/env.$tag.new" <<'PY'
import json, re, sys

label = sys.argv[3] if len(sys.argv) > 3 else "default"
old = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
keys = json.loads(open(sys.argv[2], encoding="utf-8").read())
if not keys:
    # Kein Key im OpenBao-Secret gefunden (oder single=true ohne aktives Abo) —
    # dann dürfen die bestehenden Zeilen nicht entfernt werden.
    sys.stderr.write(f"✗ [{label}] OpenBao lieferte keinen API-Key — Pool bleibt unverändert\n")
    sys.exit(3)
names = ["OPENCODE_GO_API_KEY"] + [f"OPENCODE_GO_API_KEY_{i}" for i in range(2, len(keys) + 1)]
mapping = dict(zip(names, keys))

out, seen = [], set()
for line in old:
    m = re.match(r"^(OPENCODE_GO_API_KEY(_\d+)?)\s*=", line)
    if m:
        name = m.group(1)
        if name in mapping and name not in seen:
            seen.add(name)
            out.append(f"{name}={mapping[name]}")
        # überzählige/veraltete Pool-Zeilen (z. B. _4) werden verworfen
        continue
    out.append(line)

for name, key in mapping.items():       # fehlende Zeilen ergänzen
    if name not in seen:
        out.append(f"{name}={key}")
        seen.add(name)

# nur Längen ausgeben — die Werte selbst bleiben geheim
sys.stderr.write(
    f"  → [{label}] Pool-Keys gesetzt: "
    + ", ".join(f"{n} ({len(v)} Zeichen)" for n, v in mapping.items()) + "\n"
)
sys.stdout.write("\n".join(out) + "\n")
PY
  # Sicherheitsnetz: jeder gesetzte OPENCODE_GO_API_KEY*-Wert muss ein echter
  # Key sein (>= 20 Zeichen). Sonst bricht der Lauf ab, BEVOR die .env
  # überschrieben wird — verhindert den "Name statt Key"-Fehler (401).
  if ! python3 - "$WORK/env.$tag.new" <<'PY'
import re, sys
bad = []
for line in open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines():
    m = re.match(r"^(OPENCODE_GO_API_KEY(?:_\d+)?)\s*=\s*(.*)$", line)
    if m and len(m.group(2).strip()) < 20:
        bad.append(f"{m.group(1)} ({len(m.group(2).strip())} Zeichen)")
if bad:
    sys.stderr.write("✗ Pool-Werte zu kurz, vermutlich Name statt Key: " + ", ".join(bad) + "\n")
    sys.exit(1)
PY
  then
    echo "✗ [$label] Pool nicht geschrieben — bestehende .env bleibt unverändert (Backup/Review nötig)" >&2
    return 1
  fi
  # In-place überschreiben (ein mv auf /opt/data scheitert im Volume).
  "${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > $env_path && chown hermes:hermes $env_path && chmod 600 $env_path'" <"$WORK/env.$tag.new"
  echo "✓ [$label] Pool aus OpenBao übernommen (aktives Abo zuerst)"
  if [[ "$single" == "true" ]]; then
    echo "  → [$label] Modus SINGLE: Pool enthält NUR das aktive Abo (OPENCODE_GO_API_KEY_2/_3 entfernt)"
  fi
}

# Aktives Abo aus der Konten-SOT lesen (JSONC robust parsen, Default default1 —
# analog startup.sh). Der Name wird an pool.py durchgereicht. Wird unabhängig
# vom Default-Profil gelesen, damit auch einzelne Profile mit
# pool.followActive=true den SOT-Account zuerst stellen.
SOT_ACTIVE_ACCOUNT=""
if [[ -r "$HESTIA_ACCOUNTS_PATH" ]]; then
  SOT_ACTIVE_ACCOUNT="$(python3 -c '
import json, re, sys
raw = open(sys.argv[1], encoding="utf-8").read()
try:
    import hjsonc
    cfg = hjsonc.parse(raw) or {}
except Exception:
    raw = re.sub(r"^[ \t]*//.*$|[ \t]+//.*$", "", raw, flags=re.M)
    cfg = json.loads(re.sub(r",(\s*[}\]])", r"\1", raw))
print(str(cfg.get("active") or "").strip())
' "$HESTIA_ACCOUNTS_PATH" 2>/dev/null)" || SOT_ACTIVE_ACCOUNT=""
  [ -z "$SOT_ACTIVE_ACCOUNT" ] && SOT_ACTIVE_ACCOUNT="default1"
  [[ "$POOL_FOLLOW" == "true" ]] && echo "→ Aktives Abo laut Konten-SOT: $SOT_ACTIVE_ACCOUNT"
elif [[ "$POOL_FOLLOW" == "true" ]]; then
  echo "⚠ Konten-SOT nicht lesbar: $HESTIA_ACCOUNTS_PATH — Fallback auf default1"
fi

if [[ "$POOL_FOLLOW" == "true" ]]; then
  apply_pool "$ENV_IN_CONTAINER" "default" "$SINGLE"
else
  echo "→ Pool bleibt unverändert (pool.followActive=false)"
fi

# --- 5b) Weitere Hermes-Profile (Multiplexer) -------------------------------
# Jedes Profil hat eigene Dateien: /opt/data/profiles/<name>/config.yaml und
# .env. Die Hermes-CLI kann das Profil nicht adressieren (HERMES_PROFILE wird
# ignoriert), daher werden die Dateien direkt editiert.
cat >"$WORK/model.py" <<'PY'
"""Setzt model.provider + model.default in der YAML-Config (stdin).

Ersetzt NUR die Zeilen innerhalb des model:-Blocks. Gleichnamige Keys in
anderen Blöcken (z. B. telemetry.provider) bleiben unberührt. Fehlt der Block,
wird er neu angehängt.

Aufruf: model.py <provider> <model>
"""
import re, sys


def yaml_scalar(v):
    v = str(v)
    if re.search(r"[:#\-{}\[\],&*?|<>=!%@`\"'\s]", v):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def main():
    text = sys.stdin.read()
    provider = sys.argv[1] if len(sys.argv) > 1 else ""
    model = sys.argv[2] if len(sys.argv) > 2 else ""
    lines = text.splitlines()

    out, i, in_block, seen = [], 0, False, False
    while i < len(lines):
        line = lines[i]
        if not in_block and re.match(r"^model\s*:", line):
            in_block, seen = True, True
            out.append(line)
            i += 1
            continue
        if in_block:
            # Folgezeilen des Blocks: eingerückt ODER leer. Alles andere
            # (Spalte 0) beendet den Block.
            if line.strip() == "":
                out.append(line)
                i += 1
                continue
            if line[:1] in (" ", "\t"):
                m = re.match(r"^(\s*)provider\s*:", line)
                if m:
                    out.append(f"{m.group(1)}provider: {yaml_scalar(provider)}")
                    i += 1
                    continue
                m = re.match(r"^(\s*)default\s*:", line)
                if m:
                    out.append(f"{m.group(1)}default: {yaml_scalar(model)}")
                    i += 1
                    continue
                out.append(line)
                i += 1
                continue
            in_block = False
        out.append(line)
        i += 1

    if not seen:
        out.append("")
        out.append("model:")
        out.append(f"  provider: {yaml_scalar(provider)}")
        out.append(f"  default: {yaml_scalar(model)}")

    while out and out[-1].strip() == "":
        out.pop()
    sys.stdout.write("\n".join(out) + "\n")


main()
PY

PROFILES_APPLIED=0
if [[ "$PROFILES_JSON" != "[]" ]]; then
  while IFS=$'\t' read -r P_NAME P_PRIMARY_PROVIDER P_PRIMARY_MODEL P_FB_JSON P_POOL_FOLLOW P_POOL_ONLY; do
    [[ -z "$P_NAME" ]] && continue
    P_CONFIG="/opt/data/profiles/$P_NAME/config.yaml"
    P_ENV="/opt/data/profiles/$P_NAME/.env"
    # Profil-Dateien müssen existieren, sonst nichts anfassen.
    if ! "${SSH[@]}" "docker exec '$HERMES_CONTAINER' test -f '$P_CONFIG'" 2>/dev/null; then
      echo "⚠ Profil '$P_NAME' übersprungen: $P_CONFIG nicht gefunden"
      continue
    fi
    echo "→ Profil '$P_NAME': $P_PRIMARY_PROVIDER/$P_PRIMARY_MODEL · Fallbacks: $P_FB_JSON"
    "${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'cp -a $P_CONFIG $P_CONFIG.bak-hestia-\$(date +%s)'"
    echo "  → [$P_NAME] Backup: $("${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'ls -1 $P_CONFIG.bak-hestia-* 2>/dev/null | tail -1'")"

    # Primary + Fallback in einem Durchgang: erst model.provider/model.default,
    # dann der fallback_providers-Block (liest die bereits korrigierte Datei).
    "${SSH[@]}" "docker exec '$HERMES_CONTAINER' cat $P_CONFIG" \
      | python3 "$WORK/model.py" "$P_PRIMARY_PROVIDER" "$P_PRIMARY_MODEL" > "$WORK/p.$P_NAME.model.yaml"
    "${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > $P_CONFIG'" < "$WORK/p.$P_NAME.model.yaml"
    echo "  → [$P_NAME] model.provider=$P_PRIMARY_PROVIDER · model.default=$P_PRIMARY_MODEL"
    write_fallbacks "$P_CONFIG" "$P_FB_JSON" "$P_NAME"
    validate_yaml "$P_CONFIG" "$P_NAME"

    # Pool: nur wenn für dieses Profil followActive gilt.
    P_SINGLE="$SINGLE"
    if [[ "$P_POOL_ONLY" == "true" ]]; then
      P_SINGLE="true"
    fi
    if [[ "$P_POOL_FOLLOW" == "true" ]]; then
      apply_pool "$P_ENV" "$P_NAME" "$P_SINGLE"
    else
      echo "  → [$P_NAME] Pool bleibt unverändert (pool.followActive=false)"
    fi
    PROFILES_APPLIED=$((PROFILES_APPLIED + 1))
  done < <(python3 - "$PROFILES_JSON" <<'PY'
import json, sys
# profiles_json enthält fertige, geerbte Objekte → nur noch in Zeilen zerlegen.
for p in json.loads(sys.argv[1]):
    print("\t".join([
        p["name"], p["provider"], p["model"], p["fallback"],
        p["pool_follow"], p["pool_only_active"],
    ]))
PY
)
  if [[ "$PROFILES_APPLIED" -gt 0 ]]; then
    echo "✓ $PROFILES_APPLIED Profil(e) angewendet (von $(python3 -c 'import json,sys;print(len(json.loads(sys.argv[1])))' "$PROFILES_JSON") in der SOT)"
  fi
fi

# --- 5c) Fastpath-Skripte + Skills und Service-Passwort (Datei-Only) ----------
# Beides NACH den config/.env-Schritten und VOR "hermes config check", damit die
# Prüfung einen materialisierten Baum sieht. Die Fastpath-/Skill-Dateien fließen
# in den Neustart-Hash NICHT ein (nur config.yaml/.env) — die Installation selbst
# löst also keinen Hermes-Neustart aus; nur eine tatsächlich geänderte
# Passwort-Zeile in der .env zählt über den gewohnten Hash-Mechanismus.
install_fast_paths
patch_api_tool_surface
sync_hermes_service_password "$ENV_IN_CONTAINER" "default"

# --- 6) Validierung ----------------------------------------------------------
echo "→ hermes config check"
if ! "${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'HOME=/opt/data $HERMES_BIN config check'" | sed 's/^/   /'; then
  # Fehlgeschlagener Config-Check: den Merge aus Schritt 4c zurückrollen.
  if [[ -n "$ADV_BACKUP" ]]; then
    echo "→ stelle Backup wieder her: $ADV_BACKUP" >&2
    "${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'cat $ADV_BACKUP > $CONFIG_IN_CONTAINER && chown hermes:hermes $CONFIG_IN_CONTAINER && chmod 600 $CONFIG_IN_CONTAINER'"
    echo "✓ config.yaml aus $ADV_BACKUP wiederhergestellt (Merge zurückgerollt)" >&2
  fi
  echo "✗ hermes config check fehlgeschlagen — Backup: siehe oben" >&2
  exit 1
fi

# --- 7) Neustart + Health-Wait ----------------------------------------------
# Nur bei echter Änderung neu starten (STATE_BEFORE oben): ein Neustart bricht
# die laufende Hermes-Aufgabe ab und schickt eine Shutdown-Meldung nach ntfy —
# für einen No-Op-Abgleich ist das reines Rauschen.
RESTARTED=0
STATE_AFTER="$(hermes_state_hash)"
if [[ "$RESTART" != "true" ]]; then
  echo "→ Neustart nicht nötig (SOT restart=false)"
elif [[ -n "$STATE_BEFORE" && "$STATE_BEFORE" == "$STATE_AFTER" && "${API_SURFACE_PATCHED:-0}" != "1" ]]; then
  echo "→ keine Änderung an config.yaml/.env oder API-Quelle — Hermes bleibt unangetastet (kein Neustart)"
else
  RESTARTED=1
  echo "→ Neustart $HERMES_CONTAINER (Konfiguration oder API-Quelle hat sich geändert)"
  "${SSH[@]}" "docker restart '$HERMES_CONTAINER' >/dev/null"
  echo "→ warte auf Health (max. 30s)"
  UP=0
  for _ in $(seq 1 30); do
    if "${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'wget -qO- http://127.0.0.1:9119$HEALTH_PATH 2>/dev/null || curl -sf http://127.0.0.1:9119$HEALTH_PATH 2>/dev/null'" >/dev/null 2>&1; then
      UP=1
      break
    fi
    sleep 1
  done
  if [[ "$UP" == "1" ]]; then
    echo "✓ Health ok"
    "${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'wget -qO- http://127.0.0.1:9119$HEALTH_PATH 2>/dev/null || curl -sf http://127.0.0.1:9119$HEALTH_PATH 2>/dev/null'" | sed 's/^/   /'
  else
    echo "⚠ Health nach 30s nicht erreichbar — bitte $HERMES_CONTAINER prüfen"
  fi
  # API-Server nur HINWEISEN: er ist optional, ein Fehler darf den Apply nicht rot machen.
  if [[ "$API_ENABLED" == "true" ]]; then
    if "${SSH[@]}" "docker exec '$HERMES_CONTAINER' sh -lc 'wget -qO- --timeout=2 http://127.0.0.1:$API_PORT/ 2>/dev/null || curl -sf --max-time 2 http://127.0.0.1:$API_PORT/ 2>/dev/null || (exec 3<>/dev/tcp/127.0.0.1/$API_PORT && echo open)'" >/dev/null 2>&1; then
      echo "  → API-Server $API_HOST:$API_PORT erreichbar"
    else
      echo "  ⚠ API-Server $API_HOST:$API_PORT nicht erreichbar (nur Hinweis — Apply bleibt erfolgreich)"
    fi
  fi
fi

# --- 8) Zusammenfassung ------------------------------------------------------
echo "✓ Hermes angewendet: primary=$PRIMARY_PROVIDER/$PRIMARY_MODEL · fallbacks=$FALLBACKS_JSON · pool=$POOL_FOLLOW · single=$SINGLE · restart=$RESTART (ausgeführt=$RESTARTED)"
echo "✓ API-Server laut SOT: enabled=$API_ENABLED · $API_HOST:$API_PORT (API_SERVER_KEY unveraendert)"
if [[ "$PROFILES_JSON" != "[]" ]]; then
  echo "✓ Profile angewendet: $PROFILES_APPLIED · $PROFILES_JSON"
fi
if [[ -n "$ADV_BACKUP" ]]; then
  echo "✓ Erweiterte Sektionen (agent/providers/platform_toolsets/platforms/skills) gemerged — Backup: $ADV_BACKUP"
fi
