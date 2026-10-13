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
# 4) optional den Abo-Pool (OPENCODE_GO_API_KEY*) aus OpenBao neu setzen
# 5) hermes config check, optionaler Neustart + Health-Wait, Zusammenfassung
#
# Sicherheit: die API-Keys stehen nie im Log, nie in einer Kommandozeile und nie
# im Workflow-Log — sie wandern ausschließlich über stdin.
#
# Env:
#   SOT_PATH              Default .opencode/hestia-hermes.jsonc
#   HERMES_CONTAINER      Default hermes
#   OPENBAO_ADDR          Default https://openbao.mueller-nas.de
#   OPENBAO_TOKEN_FILE    Default /opt/docker-compose/github-runner/openbao-switch.token
#   CODER_SSH_HOST        Default 10.0.10.17
#   CODER_SSH_KEY         Default ~/.ssh/id_ed25519_coderdeploy
set -euo pipefail

SOT_PATH="${SOT_PATH:-.opencode/hestia-hermes.jsonc}"
HERMES_CONTAINER="${HERMES_CONTAINER:-hermes}"
OPENBAO_ADDR="${OPENBAO_ADDR:-https://openbao.mueller-nas.de}"
OPENBAO_TOKEN_FILE="${OPENBAO_TOKEN_FILE:-/opt/docker-compose/github-runner/openbao-switch.token}"
CODER_SSH_HOST="${CODER_SSH_HOST:-10.0.10.17}"
CODER_SSH_KEY="${CODER_SSH_KEY:-$HOME/.ssh/id_ed25519_coderdeploy}"
SECRET_PATH="secret/data/mcp/opencode-go"
CONFIG_IN_CONTAINER="/opt/data/config.yaml"
ENV_IN_CONTAINER="/opt/data/.env"
HERMES_BIN="/opt/hermes/bin/hermes"
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
IFS=$'\t' read -r PRIMARY_PROVIDER PRIMARY_MODEL FALLBACKS_JSON POOL_FOLLOW RESTART ENABLED < <(
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
fb = []
for f in (cfg.get("fallback") or []):
    if not isinstance(f, dict):
        continue
    if f.get("provider") and f.get("model"):
        fb.append({"provider": str(f["provider"]), "model": str(f["model"])})
print("\t".join([
    str(p.get("provider") or ""),
    str(p.get("model") or ""),
    json.dumps(fb, separators=(",", ":")),
    "true" if (cfg.get("pool") or {}).get("followActive") else "false",
    "false" if cfg.get("restart") is False else "true",
    "true" if cfg.get("enabled", True) else "false",
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
echo "→ Pool folgt aktivem Konto: $POOL_FOLLOW · Restart: $RESTART"

# Kurzer Health-Check: Container muss laufen, sonst ist alles sinnlos.
if ! "${SSH[@]}" "docker inspect -f '{{.State.Running}}' '$HERMES_CONTAINER' 2>/dev/null" | grep -q true; then
  echo "✗ Container '$HERMES_CONTAINER' läuft nicht (auf $CODER_SSH_HOST)" >&2
  exit 1
fi

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
            # Folgezeilen einrücken (Listenelemente, Kommentare) überspringen
            while i < len(lines) and (lines[i].strip() == "" or lines[i][:1] in (" ", "\t")):
                i += 1
            continue
        out.append(line)
        i += 1

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

"${SSH[@]}" "docker exec '$HERMES_CONTAINER' cat $CONFIG_IN_CONTAINER" \
  | python3 "$WORK/fallbacks.py" "$FALLBACKS_JSON" > "$WORK/config.yaml"
"${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > $CONFIG_IN_CONTAINER'" < "$WORK/config.yaml"
"${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'chown hermes:hermes $CONFIG_IN_CONTAINER; chmod 600 $CONFIG_IN_CONTAINER'"
echo "→ fallback_providers ersetzt (alter Block entfernt, neue Liste angehängt)"
echo "→ Fallback-Kette laut Hermes:"
"${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'HOME=/opt/data $HERMES_BIN fallback list'" | sed 's/^/   /'

# --- 5) Abo-Pool aus OpenBao -------------------------------------------------
# Reihenfolge = aktives Abo zuerst, dann die übrigen; die Nummern-Suffixe
# (_2/_3) stehen für die Priorität im Pool (fill_first).
if [[ "$POOL_FOLLOW" == "true" ]]; then
  if [[ ! -r "$OPENBAO_TOKEN_FILE" ]]; then
    echo "⚠ pool.followActive=true, aber OpenBao-Token fehlt: $OPENBAO_TOKEN_FILE — Pool unverändert"
  else
    VAULT_TOKEN="$(cat "$OPENBAO_TOKEN_FILE")"
    # Keys holen und direkt in eine Datei schreiben (nie ausgeben).
    # Reihenfolge: aktives Abo zuerst, dann die restlichen Keys alphabetisch.
    cat >"$WORK/pool.py" <<'PY'
"""OpenBao-JSON (stdin) → JSON-Array der Keys in Pool-Reihenfolge."""
import json, sys
try:
    d = (json.load(sys.stdin).get("data") or {}).get("data") or {}
except Exception:
    d = {}
keys = [str(d[k]).strip() for k in sorted(d) if k != "active" and str(d.get(k) or "").strip()]
active = str(d.get("active") or "").strip()
sys.stdout.write(json.dumps([active] + [k for k in keys if k != active] if active else keys))
PY
    curl -sS -H "X-Vault-Token: $VAULT_TOKEN" "$OPENBAO_ADDR/v1/$SECRET_PATH" \
      | python3 "$WORK/pool.py" > "$WORK/pool.json"
    # Aktuelle .env holen und die OPENCODE_GO_API_KEY-Zeilen ersetzen.
    "${SSH[@]}" "docker exec '$HERMES_CONTAINER' cat $ENV_IN_CONTAINER" > "$WORK/env.old" || true
    python3 - "$WORK/env.old" "$WORK/pool.json" > "$WORK/env.new" <<'PY'
import json, re, sys

old = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
keys = json.loads(open(sys.argv[2], encoding="utf-8").read())
names = ["OPENCODE_GO_API_KEY"] + [f"OPENCODE_GO_API_KEY_{i}" for i in range(2, len(keys) + 1)]
mapping = dict(zip(names, keys))

out, seen = [], set()
for line in old:
    m = re.match(r"^(OPENCODE_GO_API_KEY(_[23])?)\s*=", line)
    if m:
        name = m.group(1)
        if name in mapping and name not in seen:
            seen.add(name)
            out.append(f"{name}={mapping[name]}")
        elif name not in mapping:
            out.append(f"{name}=")   # Key ohne Pool-Inhalt: leer, aber Struktur bleibt
        continue
    out.append(line)

for name, key in mapping.items():       # fehlende Zeilen ergänzen
    if name not in seen:
        out.append(f"{name}={key}")
        seen.add(name)

# nur Längen ausgeben — die Werte selbst bleiben geheim
sys.stderr.write(
    "→ Pool-Keys gesetzt: " + ", ".join(f"{n} ({len(v)} Zeichen)" for n, v in mapping.items()) + "\n"
)
sys.stdout.write("\n".join(out) + "\n")
PY
    # In-place überschreiben (ein mv auf /opt/data scheitert im Volume).
    "${SSH[@]}" "docker exec -i '$HERMES_CONTAINER' sh -lc 'cat > $ENV_IN_CONTAINER && chown hermes:hermes $ENV_IN_CONTAINER && chmod 600 $ENV_IN_CONTAINER'" <"$WORK/env.new"
    echo "✓ Pool aus OpenBao übernommen (aktives Abo zuerst)"
  fi
else
  echo "→ Pool bleibt unverändert (pool.followActive=false)"
fi

# --- 6) Validierung ----------------------------------------------------------
echo "→ hermes config check"
"${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'HOME=/opt/data $HERMES_BIN config check'" | sed 's/^/   /' || {
  echo "✗ hermes config check fehlgeschlagen — Backup: siehe oben" >&2
  exit 1
}

# --- 7) Neustart + Health-Wait ----------------------------------------------
if [[ "$RESTART" == "true" ]]; then
  echo "→ Neustart $HERMES_CONTAINER"
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
fi

# --- 8) Zusammenfassung ------------------------------------------------------
echo "✓ Hermes angewendet: primary=$PRIMARY_PROVIDER/$PRIMARY_MODEL · fallbacks=$FALLBACKS_JSON · pool=$POOL_FOLLOW · restart=$RESTART"
