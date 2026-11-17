#!/bin/bash
# Double-click to start Video Downloader.
cd "$(dirname "$0")" || exit 1
PORT=8765
URL="http://127.0.0.1:$PORT"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# If the app is already running, open it.
if curl -s -o /dev/null "$URL/api/env" -H "Host: 127.0.0.1:$PORT"; then
  echo "Video Downloader is already running at $URL"
  open "$URL"
  exit 0
fi

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
if ! .venv/bin/python -c 'import flask, yt_dlp' 2>/dev/null; then
  echo "Installing Flask and yt-dlp into .venv. This happens once."
  rm -rf .venv
  "$PY" -m venv .venv || { read -r -p "Couldn't create .venv with $PY. Press Return to close."; exit 1; }
  .venv/bin/python -m pip install --quiet --upgrade pip
  if ! .venv/bin/python -m pip install --quiet flask yt-dlp; then
    echo "Couldn't install Flask and yt-dlp. Check your internet connection, then double-click start.command again."
    read -r -p "Press Return to close."
    exit 1
  fi
fi

echo "Video Downloader: $URL"
echo "Close this window to stop the app."
( for _ in $(seq 1 50); do
    curl -s -o /dev/null "$URL/api/env" -H "Host: 127.0.0.1:$PORT" && { open "$URL"; break; }
    sleep 0.2
  done ) &
exec .venv/bin/python app.py
