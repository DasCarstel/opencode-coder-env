#!/bin/bash
# uni-envs-setup.sh – richtet die zwei Python-Umgebungen ein (idempotent).
#
#   ml → Python 3.14.4 + numpy 2.4.4, pandas 3.0.2, matplotlib 3.10.9,
#        scikit-learn 1.8.0 (+ ipykernel) → Jupyter-Kernel "Python (ml)"
#   dl → Python 3.12.0 + numpy 1.26.4, pandas 3.0.2, matplotlib 3.10.9,
#        scikit-learn 1.8.0, tensorflow 2.17.0 (+ ipykernel) → "Python (dl)"
#
# Idempotenz:
#   * Die venvs liegen im persistenten Host-Ordner ~/uni/envs/<name>; ein
#     Marker .ready verhindert das erneute Einrichten (die Installation darf
#     den Workspace-Start nicht blockieren → Aufruf im Hintergrund).
#   * Der Container wird bei Coder-"Stop" verworfen, ~/uni bleibt. Deshalb
#     werden die Kernel-Specs bei JEDEM Start neu installiert (sie liegen in
#     /root/.local/share/jupyter/kernels, also außerhalb des Host-Ordners).
#
# Kein `set -e`: ein Netzfehler darf den Rest nicht abbrechen.

set -uo pipefail

export PATH="/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export UV_PYTHON_INSTALL_DIR=/opt/uv-python

WS_OWNER="${WS_OWNER:-${WORKSPACE_OWNER:-carstenmueller2002}}"
UNI_DIR="${UNI_DIR:-/home/${WS_OWNER}/uni}"
LOG_DIR="$UNI_DIR/logs"
KERNEL_DIR="/root/.local/share/jupyter/kernels"
RESTART_FLAG="/root/.jupyter-restart-request"

echo ""
echo "=== $(date -Is) uni-envs-setup start ==="
mkdir -p "$UNI_DIR/envs" "$LOG_DIR"

# uv sicherstellen (im Image vorhanden – Fallback für manuelle Läufe)
if ! command -v uv >/dev/null 2>&1; then
  echo "  → uv installieren..."
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -2
fi
echo "  uv: $(uv --version 2>&1)"

# ── Interpreter installieren (idempotent) ─────────────────────────────────
uv python install 3.14.4 3.12.0 2>&1 | tail -3 \
  || echo "  ⚠ uv python install teilweise fehlgeschlagen – Fallback beim venv"

# venv mit Wunsch-Python anlegen; fällt auf die nächstkleinere Angabe zurück,
# falls ein Pin von uv noch nicht angeboten wird (wird geloggt).
uv_venv() {
  local dir="$1"; shift
  local py
  for py in "$@"; do
    rm -rf "$dir"
    if uv venv --python "$py" "$dir" >/dev/null 2>&1; then
      echo "$py"
      return 0
    fi
    echo "  ⚠ Python $py nicht verfügbar – nächster Versuch" >&2
  done
  return 1
}

# setup_env <name> <pkgs> <python-wunschliste...>
setup_env() {
  local name="$1" pkgs="$2"; shift 2
  local dir="$UNI_DIR/envs/$name"
  local got

  if [ -x "$dir/bin/python" ] && [ -f "$dir/.ready" ]; then
    echo "  ✓ $name bereits eingerichtet ($("$dir/bin/python" --version 2>&1)) – kein Download"
  else
    echo "  → $name einrichten (Wunsch: $*)..."
    got="$(uv_venv "$dir" "$@" | tail -1)"
    if [ -z "$got" ]; then
      echo "  ⚠ $name: kein passender Python-Interpreter – Umgebung bleibt leer"
      return 1
    fi
    echo "  ✓ $name: venv mit Python $got"
    # shellcheck disable=SC2086
    if uv pip install --python "$dir/bin/python" $pkgs; then
      touch "$dir/.ready"
      echo "  ✓ $name: Pakete installiert"
      # Kernel-Specs sind jetzt neu → laufendes JupyterLab neu starten lassen
      # (der Supervisor in uni-bootstrap.sh liest das Flag aus).
      touch "$RESTART_FLAG"
      pkill -f "jupyter-lab --ip=" >/dev/null 2>&1 || true
    else
      echo "  ⚠ $name: Paketinstallation fehlgeschlagen (siehe oben) – kein .ready"
      return 1
    fi
  fi

  # Kernel-Spec bei jedem Start setzen (liegt außerhalb des Host-Ordners)
  if ! "$dir/bin/python" -m ipykernel install --user --name "$name" \
        --display-name "Python ($name)" >/dev/null 2>&1; then
    echo "  ⚠ ipykernel-Spec '$name' konnte nicht installiert werden"
  fi
}

setup_env ml "numpy==2.4.4 pandas==3.0.2 matplotlib==3.10.9 scikit-learn==1.8.0 ipykernel" 3.14.4 3.14
setup_env dl "numpy==1.26.4 pandas==3.0.2 matplotlib==3.10.9 scikit-learn==1.8.0 tensorflow==2.17.0 ipykernel" 3.12.0 3.12

echo "  Kernel-Specs: $(ls -1 "$KERNEL_DIR" 2>/dev/null | tr '\n' ' ')"
echo "=== $(date -Is) uni-envs-setup ende ==="
