#!/bin/bash
# Build Video Downloader.app and its DMG for this Mac's architecture.
# Usage: packaging/build.sh            (builds for $(uname -m))
# Needs: .venv with packaging/requirements.txt, and packaging/fetch-binaries.sh run first.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ARCH="$(uname -m)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
VERSION="$("$PY" -c 'import re;print(re.search(r"APP_VERSION = \"(.+?)\"", open("app.py").read())[1])')"

PYARCH="$("$PY" -c 'import platform;print(platform.machine())')"
[ "$PYARCH" = "$ARCH" ] || { echo "Python is $PYARCH but this Mac is $ARCH. Build each architecture on its own Mac." >&2; exit 1; }
for b in yt-dlp ffmpeg ffprobe deno; do
  [ -x "packaging/vendor/$ARCH/bin/$b" ] || { echo "Missing $b. Run packaging/fetch-binaries.sh $ARCH first." >&2; exit 1; }
done

rm -rf "build/$ARCH" "dist/$ARCH"
VD_ARCH="$ARCH" VD_VERSION="$VERSION" "$PY" -m PyInstaller --noconfirm --clean \
  --workpath "build/$ARCH" --distpath "dist/$ARCH" packaging/VideoDownloader.spec

APP="dist/$ARCH/Video Downloader.app"
# Ad-hoc signature, inside out: bundled tools first, then the app.
find "$APP/Contents" -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' \) -print0 |
  xargs -0 codesign --force --sign - --timestamp=none 2>/dev/null || true
codesign --force --sign - --timestamp=none "$APP"
codesign --verify --strict --verbose=2 "$APP"

packaging/make-dmg.sh "$APP" "dist/Video-Downloader-$VERSION-$ARCH.dmg"
