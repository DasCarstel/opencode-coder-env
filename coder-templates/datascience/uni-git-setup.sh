#!/bin/bash
# uni-git-setup.sh – Git-Repos der Studienmodule sicherstellen (idempotent).
#
# Pro Studienmodul ein eigenes Repo:
#   lokal   ~/uni/module/<modulnummer>-<slug>/     (persistentes Volume)
#   remote  https://github.com/<owner>/fernuni-<modulnummer>-<slug>.git  (privat)
#
# Zweck: Versionierung der eigenen Modul-Arbeiten + Zugriff vom PC + Wieder-
# herstellung in einem NEUEN Workspace (dort ist ~/uni leer – dieses Skript
# baut die Repos dann selbst wieder auf).
#
# Ablauf je Modul:
#   1. .git vorhanden              → nur `git remote get-url origin` prüfen,
#                                    NICHTS verändern; `git pull --ff-only`
#                                    nur bei sauberem Arbeitsbaum.
#   2. .git fehlt, Remote hat Refs      → `git clone <url> <pfad>`
#   3. .git fehlt, Remote fehlt/leer    → `git init -b main` + Remote +
#                                         README.md + .gitignore + Erstcommit
#                                         + `push -u origin main`
#
# Token-Behandlung (bewusst so entschieden):
#   * Das Token wird NUR zur Laufzeit aus $GITHUB_TOKEN gelesen und NIE in
#     eine Datei im Repo oder in den Workspace geschrieben.
#   * Die Remote-URL wird OHNE Token gesetzt (https://github.com/<owner>/<repo>.git),
#     damit `.git/config` und `git remote -v` sauber bleiben.
#   * Authentifiziert wird über `credential.helper store` mit
#     /root/.git-credentials (0600) – außerhalb des Repos, außerhalb des
#     Volumes ~/uni. Das entspricht scripts/setup-git-credentials.sh und ist
#     robuster als `url.insteadOf`, die den Token dauerhaft in ~/.gitconfig
#     schreiben würde (mehr Leak-Flächen in jedem `git config --list`).
#   * Fehlt das Token, wird KEIN Push versucht – das Skript meldet es nur.
#
# Kein `set -e`: ein einzelnes Modul darf den Rest nicht abbrechen.
# Aufruf: bash uni-git-setup.sh            (aus uni-bootstrap.sh im Hintergrund)

set -uo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

WS_OWNER="${WS_OWNER:-${WORKSPACE_OWNER:-carstenmueller2002}}"
UNI_DIR="${UNI_DIR:-/home/${WS_OWNER}/uni}"
MODULE_DIR="$UNI_DIR/module"
LOG_DIR="$UNI_DIR/logs"
GITHUB_OWNER="${GITHUB_OWNER:-DasCarstel}"
GITHUB_HOST="${GITHUB_HOST:-github.com}"

# Name/E-Mail nur setzen, wenn global noch nichts konfiguriert ist
GIT_NAME_FALLBACK="carstenmueller2002"
GIT_EMAIL_FALLBACK="carstenmueller2002@users.noreply.github.com"

# Modul-Liste: "<modulnummer>|<slug>|<modulname>|<moodle-kurs>|<vault-ordner>"
MODULE=(
  "63123|data-engineering-fuer-data-science|Data Engineering für Data Science|6556|10-Semester/1-Semester/63123-Data-Engineering-fuer-Data-Science"
  "64401|maschinelles-lernen|Maschinelles Lernen|6591|10-Semester/1-Semester/64401-Maschinelles-Lernen"
  "64511|einfuehrung-in-data-science|Einführung in Data Science|–|10-Semester/1-Semester/64511-Einfuehrung-in-Data-Science"
  "61811|mathematische-grundlagen-von-data-science|Mathematische Grundlagen von Data Science|–|10-Semester/1-Semester/61811-Mathematische-Grundlagen-von-Data-Science"
)

echo ""
echo "=== $(date -Is) uni-git-setup start (owner=$WS_OWNER, Modul-Dir=$MODULE_DIR) ==="
mkdir -p "$MODULE_DIR" "$LOG_DIR"

# ── 0. GITHUB_TOKEN aus /etc/opencode.env nachladen (falls nicht exportiert) ─
#    /etc/opencode.env wird von shared/startup.sh geschrieben (liegt außerhalb
#    des Repos und außerhalb von ~/uni).
if [ -z "${GITHUB_TOKEN:-}" ] && [ -f /etc/opencode.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /etc/opencode.env
  set +a
fi

# ── 1. Git-Identität + Credentials (außerhalb des Repos!) ──────────────────
if [ -z "$(git config --global --get user.name || true)" ]; then
  git config --global user.name "$GIT_NAME_FALLBACK"
  echo "  ✓ git user.name gesetzt ($GIT_NAME_FALLBACK)"
else
  echo "  ✓ git user.name: $(git config --global --get user.name)"
fi
if [ -z "$(git config --global --get user.email || true)" ]; then
  git config --global user.email "$GIT_EMAIL_FALLBACK"
  echo "  ✓ git user.email gesetzt ($GIT_EMAIL_FALLBACK)"
else
  echo "  ✓ git user.email: $(git config --global --get user.email)"
fi

GIT_AUTH_OK=0
if [ -f /root/.git-credentials ]; then
  GIT_AUTH_OK=1
elif [ -n "${GITHUB_TOKEN:-}" ]; then
  # printf ist ein bash-Builtin → das Token steht nicht in einer Prozessliste.
  printf 'https://x-access-token:%s@%s\n' "$GITHUB_TOKEN" "$GITHUB_HOST" > /root/.git-credentials
  chmod 600 /root/.git-credentials
  git config --global credential.helper store
  GIT_AUTH_OK=1
  echo "  ✓ git-Credentials aus GITHUB_TOKEN gesetzt (/root/.git-credentials, 0600)"
fi
if [ "$GIT_AUTH_OK" = "1" ]; then
  git config --global credential.helper store
  echo "  ✓ git-Authentifizierung: credential.helper=store (Datei außerhalb des Repos)"
else
  echo "  ⚠ GITHUB_TOKEN fehlt und /root/.git-credentials fehlt – nur lokale Repos, kein Push"
fi

# git init -b braucht git >= 2.28
GIT_VER="$(git --version 2>/dev/null | awk '{print $3}')"
GIT_MAJ="${GIT_VER%%.*}"
GIT_MIN="${GIT_VER#*.}"; GIT_MIN="${GIT_MIN%%.*}"
GIT_INIT_ARGS=(-b main)
if [ "${GIT_MAJ:-0}" -lt 2 ] || { [ "${GIT_MAJ:-0}" -eq 2 ] && [ "${GIT_MIN:-0}" -lt 28 ]; }; then
  GIT_INIT_ARGS=()
  echo "  ⚠ git $GIT_VER < 2.28 – Default-Branch wird per symbolischem Ref gesetzt"
else
  echo "  ✓ git $GIT_VER (init -b main verfügbar)"
fi

# ── Hilfsfunktionen ────────────────────────────────────────────────────────
# Klassifiziert das Remote:
#   vorhanden – erreichbar und hat Refs (0 Refs + rc 0 = "leer", rc != 0 = "fehlt")
# Wichtig: ein per API angelegtes Repo OHNE Auto-Init ist erreichbar, hat aber
# keine Refs – das ist NICHT "fehlt", sondern der Zustand vor dem ersten Push.
remote_state() {  # $1 = url
  local out rc
  out="$(GIT_TERMINAL_PROMPT=0 git ls-remote "$1" 2>/dev/null)"; rc=$?
  if [ "$rc" -ne 0 ]; then printf 'fehlt'
  elif [ -z "$out" ]; then printf 'leer'
  else printf 'vorhanden'
  fi
}

schreibe_readme() {  # $1 = pfad  $2 = modulnummer  $3 = name  $4 = moodle  $5 = vault
  cat > "$1/README.md" <<EOF
# $3

|  |  |
|---|---|
| **Modulnummer** | $2 |
| **Modulname** | $3 |
| **Moodle-Kurs-ID** | $4 |
| **Studiengang** | M.Sc. Data Science, FernUniversität in Hagen (1. Semester) |

## Zweck dieses Repos

Versionierung der eigenen Arbeiten (Übungen, Skripte, Notebooks) für dieses Modul.
Ein Repo pro Modul; für jeden neuen Workspace wird es aus dem privaten
GitHub-Remote wiederhergestellt (\`~/uni/module/\` ist persistent).

## Wo liegt das Material?

Die Moodle-Materialien (Kursunterlagen, Übungsblätter, Lösungen) werden **nicht
hier** versioniert. Sie liegen im OpenCloud-/Vault-Baum:

\`\`\`
$5
\`\`\`

Der Sync dorthin läuft über \`fernuni-sync\` (siehe \`~/uni/state\`,
\`docs/coder-templates.md\`). Dieses Repo enthält nur eigene Arbeit.

## Umgebung

* Python-Umgebungen: \`~/uni/envs/ml\` (Jupyter-Kernel \`Python (ml)\`) und
  \`~/uni/envs/dl\` (\`Python (dl)\`)
* JupyterLab: Port 8890

## Neu aufsetzen (neuer Workspace)

\`\`\`bash
bash /home/$WS_OWNER/opencode-coder-env/coder-templates/datascience/uni-git-setup.sh
\`\`\`

Das Skript klont dieses Repo nach \`~/uni/module/$(basename "$1")\` – oder legt es an, falls
das Remote fehlt.
EOF
}

schreibe_gitignore() {  # $1 = pfad
  cat > "$1/.gitignore" <<'EOF'
# Python
__pycache__/
*.py[cod]
.ipynb_checkpoints/
.venv/
venv/

# Secrets
.env

# Daten (gehören nicht ins Repo – Material liegt im Vault)
*.csv
*.parquet
*.h5
*.pkl
data/

# macOS / R
.DS_Store
.Rhistory
EOF
}

# ── 2. Module abarbeiten ───────────────────────────────────────────────────
for eintrag in "${MODULE[@]}"; do
  IFS='|' read -r NUM SLUG NAME MOODLE VAULT <<< "$eintrag"
  PFAD="$MODULE_DIR/$NUM-$SLUG"
  URL="https://$GITHUB_HOST/$GITHUB_OWNER/fernuni-$NUM-$SLUG.git"

  echo ""
  echo "── Modul $NUM ($NAME)"
  echo "   Pfad:   $PFAD"
  echo "   Remote: $URL"
  mkdir -p "$PFAD"

  if [ -d "$PFAD/.git" ]; then
    # ── Fall 1: Repo existiert lokal → nicht anfassen ──────────────────────
    IST_URL="$(git -C "$PFAD" remote get-url origin 2>/dev/null || echo '<kein origin>')"
    if [ "$IST_URL" = "$URL" ]; then
      echo "   ✓ Repo vorhanden, origin korrekt (keine Änderung)"
    else
      echo "   ⚠ Repo vorhanden, origin weicht ab: $IST_URL (erwartet $URL) – NICHT geändert"
    fi
    if [ -n "$(git -C "$PFAD" status --porcelain 2>/dev/null)" ]; then
      echo "   ⚠ lokale Änderungen – pull --ff-only übersprungen"
    else
      if git -C "$PFAD" pull --ff-only >/dev/null 2>&1; then
        echo "   ✓ pull --ff-only ok ($(git -C "$PFAD" log --oneline -1 2>/dev/null))"
      else
        echo "   ⚠ pull --ff-only nicht möglich (kein Remote-Zugriff o. Divergenz) – übersprungen"
      fi
    fi
    continue
  fi

  REMOTE_STATE="$(remote_state "$URL")"
  if [ "$REMOTE_STATE" = "vorhanden" ]; then
    # ── Fall 2: Remote da, lokal leer → klonen ────────────────────────────
    echo "   → Remote vorhanden, klone..."
    if git clone "$URL" "$PFAD" >/dev/null 2>&1; then
      echo "   ✓ geklont ($(git -C "$PFAD" log --oneline -1 2>/dev/null))"
    else
      echo "   ⚠ Klonen fehlgeschlagen – nächster Lauf versucht es erneut"
    fi
    continue
  fi

  # ── Fall 3: Remote fehlt oder noch leer → Repo lokal anlegen + pushen ───
  if [ "$REMOTE_STATE" = "leer" ]; then
    echo "   → Remote existiert, ist aber leer (Repo ohne Auto-Init) → Repo lokal anlegen"
  else
    echo "   → Remote nicht vorhanden/nicht erreichbar → Repo lokal anlegen (Push legt es an)"
  fi
  if [ "${#GIT_INIT_ARGS[@]}" -eq 0 ]; then
    git -C "$PFAD" init -q
    git -C "$PFAD" symbolic-ref HEAD refs/heads/main
  else
    git -C "$PFAD" init -q "${GIT_INIT_ARGS[@]}"
  fi
  git -C "$PFAD" remote add origin "$URL" 2>/dev/null || true
  schreibe_readme "$PFAD" "$NUM" "$NAME" "$MOODLE" "$VAULT"
  schreibe_gitignore "$PFAD"
  git -C "$PFAD" add README.md .gitignore
  git -C "$PFAD" commit -q -m "Initial commit: $NAME (Modul $NUM)" \
    || echo "   ⚠ Commit fehlgeschlagen"
  echo "   ✓ Erstcommit: $(git -C "$PFAD" log --oneline -1 2>/dev/null)"

  if [ "$GIT_AUTH_OK" = "1" ]; then
    if git -C "$PFAD" push -u origin main >/dev/null 2>&1; then
      echo "   ✓ gepusht nach origin/main"
    else
      echo "   ⚠ Push fehlgeschlagen (Token-Rechte? Netz?) – Remote muss manuell angelegt werden"
    fi
  else
    echo "   ⚠ kein GITHUB_TOKEN – Push übersprungen (nur lokal)"
  fi
done

echo ""
echo "=== $(date -Is) uni-git-setup ende ==="
ls -1 "$MODULE_DIR" 2>/dev/null | sed 's/^/   Modulordner: /'
