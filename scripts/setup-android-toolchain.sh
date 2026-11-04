#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Android-Patching-Toolchain (portabel, ohne root)
#
# Ziel-Verzeichnis liegt ABSICHTLICH im persistenten Workspace-Volume
# (/home/<owner>/opencode-coder-env ist ein Docker-Volume, siehe
# coder-templates/infrastructure/main.tf) — damit überlebt die Toolchain
# Workspace-Restarts und Template-Pushes.
#
# Nach einem Container-Neuerstart:
#   source .toolchains/android/bin/env.sh
# Ist das Verzeichnis weg (Volume neu), einfach dieses Skript erneut ausführen.
#
# Enthalten: JDK 21 (Temurin), Android SDK (cmdline-tools, build-tools 34,
# platform-tools, platforms;android-34), apktool, jadx, bundletool.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${ANDROID_TOOLCHAIN_DIR:-$REPO_ROOT/.toolchains/android}"
BIN="$BASE/bin"
SDK="$BASE/android-sdk"
DL="$BASE/dl"
mkdir -p "$BIN" "$SDK" "$DL"

log() { echo "[$(date +%H:%M:%S)] $*"; }

gh_asset() { # repo, regex
  curl -sL "https://api.github.com/repos/$1/releases/latest" \
    | grep -o '"browser_download_url": *"[^"]*"' \
    | sed 's/.*"\(https[^"]*\)"/\1/' \
    | grep -E "$2" | head -1
}

# ---------- JDK 21 (Temurin) ----------
if [ ! -x "$BASE/jdk/bin/java" ]; then
  log "JDK 21 laden"
  curl -sL "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse" -o "$DL/jdk.tar.gz"
  mkdir -p "$BASE/jdk"
  tar -xzf "$DL/jdk.tar.gz" -C "$BASE/jdk" --strip-components=1
  rm -f "$DL/jdk.tar.gz"
fi
export JAVA_HOME="$BASE/jdk"
export PATH="$JAVA_HOME/bin:$BIN:$PATH"
log "JDK: $("$JAVA_HOME/bin/java" -version 2>&1 | head -1)"

# ---------- Android cmdline-tools ----------
if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
  log "Android cmdline-tools laden"
  curl -sL "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip" -o "$DL/cmdtools.zip"
  mkdir -p "$SDK/cmdline-tools"
  python3 -c "import zipfile;zipfile.ZipFile('$DL/cmdtools.zip').extractall('$SDK/cmdline-tools')"
  [ -d "$SDK/cmdline-tools/cmdline-tools" ] && mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest"
  # python-zipfile setzt kein +x → sonst "Permission denied" auf sdkmanager
  chmod -R a+x "$SDK/cmdline-tools/latest/bin"
  rm -f "$DL/cmdtools.zip"
fi

log "SDK-Pakete installieren (build-tools, platform-tools, platform)"
yes 2>/dev/null | "$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" --licenses >/dev/null 2>&1 || true
"$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" \
  "platform-tools" "build-tools;34.0.0" "platforms;android-34" 2>&1 | tail -3

# ---------- apktool ----------
if [ ! -f "$BASE/apktool.jar" ]; then
  log "apktool laden"
  curl -sL "$(gh_asset iBotPeaches/Apktool 'apktool_[0-9.]+\.jar$')" -o "$BASE/apktool.jar"
fi

# ---------- jadx ----------
if [ ! -d "$BASE/jadx" ]; then
  log "jadx laden"
  curl -sL "$(gh_asset skylot/jadx 'jadx-[0-9.]+\.zip$')" -o "$DL/jadx.zip"
  python3 -c "import zipfile;zipfile.ZipFile('$DL/jadx.zip').extractall('$BASE/jadx')"
  chmod +x "$BASE/jadx/bin/jadx" "$BASE/jadx/bin/jadx-gui" 2>/dev/null || true
  rm -f "$DL/jadx.zip"
fi

# ---------- bundletool (Split-APKs → universal APK) ----------
if [ ! -f "$BASE/bundletool.jar" ]; then
  log "bundletool laden"
  curl -sL "$(gh_asset google/bundletool 'bundletool-all-[0-9.]+\.jar$')" -o "$BASE/bundletool.jar"
fi

# ---------- Wrapper + env ----------
cat > "$BIN/apktool" <<'EOF'
#!/usr/bin/env bash
exec "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/jdk/bin/java" -jar "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/apktool.jar" "$@"
EOF
cat > "$BIN/bundletool" <<'EOF'
#!/usr/bin/env bash
exec "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/jdk/bin/java" -jar "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/bundletool.jar" "$@"
EOF
cat > "$BIN/jadx" <<'EOF'
#!/usr/bin/env bash
exec "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/jadx/bin/jadx" "$@"
EOF
cat > "$BIN/env.sh" <<EOF
# source .toolchains/android/bin/env.sh
export ANDROID_TOOLCHAIN_DIR="$BASE"
export JAVA_HOME="$BASE/jdk"
export ANDROID_SDK_ROOT="$SDK"
export ANDROID_HOME="$SDK"
export PATH="$BASE/jdk/bin:$SDK/platform-tools:$SDK/build-tools/34.0.0:$BASE/jadx/bin:$BIN:\$PATH"
EOF
chmod +x "$BIN"/*

log "=== Versionen ==="
. "$BIN/env.sh"
java -version 2>&1 | head -1
echo "apktool $(apktool --version)"
echo "jadx $(jadx --version)"
adb version | head -1
echo "apksigner $(apksigner --version)"
aapt2 version 2>&1 | head -1
log "FERTIG – Toolchain in $BASE"
log "Aktivieren mit: source $BIN/env.sh"
