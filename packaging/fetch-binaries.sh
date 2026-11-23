#!/bin/bash
# Download the pinned binaries for one architecture into packaging/vendor/<arch>/.
# Usage: packaging/fetch-binaries.sh arm64|x86_64
set -euo pipefail
ARCH="${1:-$(uname -m)}"
case "$ARCH" in arm64|x86_64) ;; *) echo "Usage: $0 arm64|x86_64" >&2; exit 2 ;; esac

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/binaries.env"
OUT="$HERE/vendor/$ARCH"
DL="$HERE/vendor/downloads"
mkdir -p "$OUT/bin" "$OUT/licenses" "$DL"

var() { eval "printf '%s' \"\${$1}\""; }

# fetch URL SHA256 FILE: download once, always verify.
fetch() {
  local url="$1" sum="$2" file="$DL/$3"
  if [ ! -f "$file" ] || ! echo "$sum  $file" | shasum -a 256 -c --status; then
    echo "Downloading $url"
    curl -fsSL --retry 3 -o "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  if ! echo "$sum  $file" | shasum -a 256 -c --status; then
    echo "Checksum mismatch for $3. Expected $sum." >&2
    rm -f "$file"
    exit 1
  fi
}

# unzip_one ZIP NAME: extract one file from a zip into OUT/bin.
unzip_one() {
  local tmp; tmp="$(mktemp -d)"
  ditto -x -k "$1" "$tmp"
  local f; f="$(find "$tmp" -type f -name "$2" | head -1)"
  [ -n "$f" ] || { echo "$2 not found in $1" >&2; exit 1; }
  install -m 0755 "$f" "$OUT/bin/$2"
  rm -rf "$tmp"
}

fetch "$YTDLP_URL" "$YTDLP_SHA256" "yt-dlp_macos-$YTDLP_VERSION"
install -m 0755 "$DL/yt-dlp_macos-$YTDLP_VERSION" "$OUT/bin/yt-dlp"

fetch "$(var "FFMPEG_${ARCH}_URL")" "$(var "FFMPEG_${ARCH}_SHA256")" "ffmpeg-$FFMPEG_VERSION-$ARCH.zip"
unzip_one "$DL/ffmpeg-$FFMPEG_VERSION-$ARCH.zip" ffmpeg
fetch "$(var "FFPROBE_${ARCH}_URL")" "$(var "FFPROBE_${ARCH}_SHA256")" "ffprobe-$FFMPEG_VERSION-$ARCH.zip"
unzip_one "$DL/ffprobe-$FFMPEG_VERSION-$ARCH.zip" ffprobe

fetch "$(var "DENO_${ARCH}_URL")" "$(var "DENO_${ARCH}_SHA256")" "deno-$DENO_VERSION-$ARCH.zip"
unzip_one "$DL/deno-$DENO_VERSION-$ARCH.zip" deno

# FFmpeg source: its licence texts go into the app; the archive goes on the release.
[ -n "$FFMPEG_SRC_SHA256" ] || { echo "Set FFMPEG_SRC_SHA256 in binaries.env first." >&2; exit 1; }
fetch "$FFMPEG_SRC_URL" "$FFMPEG_SRC_SHA256" "ffmpeg-$FFMPEG_VERSION.tar.xz"
tar -xJf "$DL/ffmpeg-$FFMPEG_VERSION.tar.xz" -C "$OUT/licenses" --strip-components 1 \
  "ffmpeg-$FFMPEG_VERSION/COPYING.GPLv3" "ffmpeg-$FFMPEG_VERSION/COPYING.GPLv2" "ffmpeg-$FFMPEG_VERSION/LICENSE.md"

# Remove the quarantine flag so the bundled tools run without prompts.
xattr -cr "$OUT/bin"

# Record exactly what was bundled: the GPL requires the version and configuration.
{
  echo "FFmpeg $FFMPEG_VERSION ($ARCH), static build by Martin Riedl"
  echo "Binary: $(var "FFMPEG_${ARCH}_URL")"
  echo "Source: $FFMPEG_SRC_URL"
  echo
  if [ "$ARCH" = "$(uname -m)" ]; then
    "$OUT/bin/ffmpeg" -hide_banner -version
    echo
    "$OUT/bin/ffmpeg" -hide_banner -buildconf
  else
    echo "(Configuration printed when built on $ARCH.)"
  fi
} > "$OUT/licenses/FFMPEG-BUILD.txt"

echo "Binaries for $ARCH are in $OUT/bin:"
ls -l "$OUT/bin"
