#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS="$ROOT/requirements.txt"
ICON_PATH="$ROOT/assets/icon.png"
DESKTOP_TEMPLATE="$ROOT/packaging/camera-overlay.desktop.in"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$DESKTOP_DIR/camera-overlay.desktop"
APP_WM_CLASS="CameraOverlay"

log() {
  printf '==> %s\n' "$*"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_system_packages() {
  local apt_packages=(
    ffmpeg
    python3-venv
    python3-pip
    python3-gi
    gir1.2-gtk-4.0
    gir1.2-adw-1
  )
  local missing=()

  if ! need_cmd ffmpeg; then
    missing+=(ffmpeg)
  fi
  if ! "$PYTHON_BIN" -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Gtk, Adw" >/dev/null 2>&1; then
    missing+=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1)
  fi
  if ! "$PYTHON_BIN" -m venv -h >/dev/null 2>&1; then
    missing+=(python3-venv)
  fi

  local unique=()
  local item
  for item in "${missing[@]+"${missing[@]}"}"; do
    local seen=0
    local existing
    for existing in "${unique[@]+"${unique[@]}"}"; do
      if [[ "$existing" == "$item" ]]; then
        seen=1
        break
      fi
    done
    if [[ $seen -eq 0 ]]; then
      unique+=("$item")
    fi
  done
  missing=("${unique[@]+"${unique[@]}"}")

  if [[ ${#missing[@]} -eq 0 ]]; then
    log "System packages already available"
    return
  fi

  if ! need_cmd apt-get; then
    echo "Could not auto-install: ${missing[*]}" >&2
    echo "Install them with your package manager, then re-run ./install.sh" >&2
    exit 1
  fi

  log "Installing system packages: ${missing[*]}"
  if need_cmd sudo; then
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}" "${apt_packages[@]}"
  else
    apt-get update
    apt-get install -y "${missing[@]}" "${apt_packages[@]}"
  fi
}

create_venv() {
  if [[ ! -x "$VENV_PYTHON" ]]; then
    log "Creating virtualenv at .venv (with system site packages for GTK)"
    "$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
  else
    if grep -q '^include-system-site-packages = false$' "$VENV_DIR/pyvenv.cfg"; then
      log "Enabling system site packages in existing .venv for GTK/libadwaita"
      sed -i 's/^include-system-site-packages = false$/include-system-site-packages = true/' \
        "$VENV_DIR/pyvenv.cfg"
    else
      log "Virtualenv already exists"
    fi
  fi
}

install_python_deps() {
  log "Installing Python packages into .venv"
  "$VENV_PYTHON" -m pip install --upgrade pip
  if ! "$VENV_PYTHON" -m pip install --default-timeout=180 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r "$REQUIREMENTS"; then
    log "Mirror failed; retrying with official PyPI"
    "$VENV_PYTHON" -m pip install --default-timeout=180 -r "$REQUIREMENTS"
  fi
}

generate_icon() {
  log "Generating application icon"
  "$VENV_PYTHON" "$ROOT/scripts/generate_icon.py" "$ICON_PATH"
}

install_desktop_entry() {
  mkdir -p "$DESKTOP_DIR"
  sed "s|@ROOT@|${ROOT}|g" "$DESKTOP_TEMPLATE" > "$DESKTOP_FILE"
  chmod +x "$ROOT/run.sh" "$ROOT/install.sh" "$ROOT/scripts/generate_icon.py"
  log "Desktop entry installed: $DESKTOP_FILE (StartupWMClass=${APP_WM_CLASS})"

  if need_cmd update-desktop-database; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
  fi
}

main() {
  install_system_packages
  create_venv
  install_python_deps
  generate_icon
  install_desktop_entry
  log "Install complete."
  log "Launch from the app menu, or run: ./run.sh"
}

main "$@"
