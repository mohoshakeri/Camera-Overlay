#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS="$ROOT/requirements.txt"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

ensure_venv() {
  if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Creating virtualenv at .venv ..."
    "$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
    return
  fi
  if [[ -f "$VENV_DIR/pyvenv.cfg" ]] \
    && grep -q '^include-system-site-packages = false$' "$VENV_DIR/pyvenv.cfg"; then
    echo "Enabling system site packages in .venv for GTK/libadwaita ..."
    sed -i 's/^include-system-site-packages = false$/include-system-site-packages = true/' \
      "$VENV_DIR/pyvenv.cfg"
  fi
}

deps_ready() {
  "$VENV_PYTHON" - <<'PY'
import importlib.util
import sys

needed = ("PyQt5", "PIL")
missing = [name for name in needed if importlib.util.find_spec(name) is None]
sys.exit(1 if missing else 0)
PY
}

gtk_ready() {
  "$VENV_PYTHON" - <<'PY'
import sys
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk  # noqa: F401
except Exception:
    sys.exit(1)
sys.exit(0)
PY
}

ensure_python_deps() {
  if deps_ready; then
    return
  fi
  echo "Installing Python dependencies into .venv ..."
  "$VENV_PYTHON" -m pip install --upgrade pip
  if ! "$VENV_PYTHON" -m pip install --default-timeout=180 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r "$REQUIREMENTS"; then
    echo "Mirror failed; retrying with official PyPI ..."
    "$VENV_PYTHON" -m pip install --default-timeout=180 -r "$REQUIREMENTS"
  fi
  if ! deps_ready; then
    echo "Python dependencies are still missing after install." >&2
    exit 1
  fi
}

ensure_ffmpeg() {
  if command -v ffmpeg >/dev/null 2>&1; then
    return
  fi
  echo "ffmpeg is required but was not found in PATH." >&2
  echo "Run ./install.sh once, or install ffmpeg manually." >&2
  exit 1
}

ensure_gtk() {
  if gtk_ready; then
    return
  fi
  echo "GTK4/libadwaita Python bindings are missing." >&2
  echo "Run ./install.sh once to install python3-gi / gir packages." >&2
  exit 1
}

ensure_venv
ensure_python_deps
ensure_gtk
ensure_ffmpeg

exec "$VENV_PYTHON" -m camera_overlay "$@"
