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
# 5) dasselbe für jedes weitere SOT-Profil ("profiles") — der Multiplexer
#    bedient mehrere Profile mit je eigenen Dateien unter /opt/data/profiles/
# 6) hermes config check, optionaler Neustart + Health-Wait, Zusammenfassung
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
# IFS=Tabulator, weil die JSON-Werte Leerzeichen enthalten.
IFS=$'\t' read -r PRIMARY_PROVIDER PRIMARY_MODEL FALLBACKS_JSON POOL_FOLLOW RESTART ENABLED PROFILES_JSON < <(
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

write_fallbacks "$CONFIG_IN_CONTAINER" "$FALLBACKS_JSON" "default"
echo "→ Fallback-Kette laut Hermes:"
"${SSH[@]}" "docker exec -u 0 '$HERMES_CONTAINER' sh -lc 'HOME=/opt/data $HERMES_BIN fallback list'" | sed 's/^/   /'

# --- 5) Abo-Pool aus OpenBao -------------------------------------------------
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

Wichtig: "active" ist der NAME (default1..3); die eigentlichen Keys stehen in
den Feldern default1..3. Wird der Name als Key missverstanden, landet er in
OPENCODE_GO_API_KEY → 401 "Invalid credential".

Aufruf: pool.py [single=true|false]
  single=true → nur der Key des aktiven Abos (kein Fallback auf default2/3).
"""
import json, sys
try:
    d = (json.load(sys.stdin).get("data") or {}).get("data") or {}
except Exception:
    d = {}
keys = {k: str(d[k]).strip() for k in sorted(d) if k != "active" and str(d.get(k) or "").strip()}
active = str(d.get("active") or "").strip()
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
    | python3 "$WORK/pool.py" "$single" > "$WORK/pool.$tag.json"
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
echo "✓ Hermes angewendet: primary=$PRIMARY_PROVIDER/$PRIMARY_MODEL · fallbacks=$FALLBACKS_JSON · pool=$POOL_FOLLOW · single=$SINGLE · restart=$RESTART"
if [[ "$PROFILES_JSON" != "[]" ]]; then
  echo "✓ Profile angewendet: $PROFILES_APPLIED · $PROFILES_JSON"
fi
