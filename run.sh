#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

print_python_install_help() {
  cat >&2 <<'EOF'
[error] Python is not installed or not on PATH.

Install Python 3:
  macOS (Homebrew):  brew install python
  Ubuntu/Debian:     sudo apt update && sudo apt install -y python3 python3-venv
  Fedora:            sudo dnf install -y python3
  Windows:           https://www.python.org/downloads/

Optional auto-install (macOS/Homebrew only):
  AUTO_INSTALL_PYTHON=1 ./run.sh ...
EOF
}

try_auto_install_python() {
  if [ "${AUTO_INSTALL_PYTHON:-0}" != "1" ]; then
    return 1
  fi
  if ! command -v brew >/dev/null 2>&1; then
    echo "[error] AUTO_INSTALL_PYTHON=1 was set, but Homebrew is not available." >&2
    return 1
  fi
  echo "[setup] AUTO_INSTALL_PYTHON=1 set, installing Python via Homebrew..."
  brew install python
}

if [ -n "${PYTHON_BIN:-}" ]; then
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "[error] PYTHON_BIN is set to '$PYTHON_BIN' but that executable was not found." >&2
    exit 1
  fi
else
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    if try_auto_install_python; then
      if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
      elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
      else
        print_python_install_help
        exit 1
      fi
    else
      print_python_install_help
      exit 1
    fi
  fi
fi

if ! "$PYTHON_BIN" -c "import venv" >/dev/null 2>&1; then
  echo "[error] Python venv module is unavailable. Install a full Python 3 distribution." >&2
  exit 1
fi

if ! command -v pdftoppm >/dev/null 2>&1; then
  echo "[error] 'pdftoppm' is required (install Poppler first)." >&2
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "[setup] Creating virtual environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

if ! python -m pip --version >/dev/null 2>&1; then
  python -m ensurepip --upgrade >/dev/null 2>&1 || {
    echo "[error] pip is unavailable in the virtual environment." >&2
    exit 1
  }
fi

if ! python -c "import PIL" >/dev/null 2>&1; then
  echo "[setup] Installing Pillow into .venv"
  python -m pip install --quiet pillow
fi

if [ "$#" -eq 0 ]; then
  cat <<'EOF'
Usage:
  ./run.sh [arguments passed to add_proxy_cut_guides.py]

Examples:
  ./run.sh ./examples/blank-example.pdf
  ./run.sh --watch
  ./run.sh --singles-mode --watch
EOF
  exit 0
fi

python "$ROOT_DIR/add_proxy_cut_guides.py" "$@"
