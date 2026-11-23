#!/bin/bash
# Development launcher: runs Video Downloader from source in its own window.
# The release build is the .app in the DMG (see packaging/).
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# yt-dlp needs Python 3.10 or newer. macOS's own python3 is 3.9.
PY=""
for c in python3.13 python3.12 python3.14 python3.11 python3.10 python3; do
  p="$(command -v "$c")" || continue
  if "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is required. Install it with:"
  echo "  brew install python"
  read -r -p "Press Return to close."
  exit 1
fi

# Create the venv once; rebuild it if it broke (for example after a Python upgrade).
if ! .venv/bin/python -c 'import flask, yt_dlp, webview, segno' 2>/dev/null; then
  echo "Installing Flask, yt-dlp, pywebview and segno into .venv. This happens once."
  rm -rf .venv
  "$PY" -m venv .venv || { read -r -p "Couldn't create .venv with $PY. Press Return to close."; exit 1; }
  .venv/bin/python -m pip install --quiet --upgrade pip
  if ! .venv/bin/python -m pip install --quiet flask yt-dlp pywebview segno; then
    echo "Couldn't install the Python packages. Check your internet connection, then double-click start.command again."
    read -r -p "Press Return to close."
    exit 1
  fi
fi

exec .venv/bin/python main.py
