#!/bin/bash
# Builds every icon from assets/logo-cutout.png (the transparent tile cut from assets/logo.png).
# Needs ffmpeg, sips and iconutil (macOS). Run from anywhere.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/assets/logo-cutout.png"
OUT="$ROOT/assets/icon"
mkdir -p "$OUT" "$ROOT/static/icons" "$ROOT/chrome-extension/icons"

# Tile is 673.7 px wide inside the 679x681 cutout. macOS grid: 824 px tile, centred on 1024.
ffmpeg -v error -y -i "$SRC" \
  -vf "scale=830:833:flags=lanczos+accurate_rnd+full_chroma_int,format=rgba,pad=1024:1024:(ow-iw)/2:(oh-ih)/2:color=0x00000000" \
  -frames:v 1 "$OUT/AppIcon-1024.png"

# Full-bleed square: the tile edge to edge, corners filled with the rim colour.
# Used where the platform rounds the corners itself (iOS home screen, Android maskable).
ffmpeg -v error -y -f lavfi -i "color=0x141413:s=1024x1024" -i "$SRC" \
  -filter_complex "[1]crop=674:674:2:3,scale=1024:1024:flags=lanczos[t];[0][t]overlay=format=auto" \
  -frames:v 1 "$OUT/FullBleed-1024.png"

# Tight tile: transparent, almost no margin. Reads better at 16-48 px than the macOS grid.
ffmpeg -v error -y -i "$SRC" -vf "crop=679:679:0:1,format=rgba" -frames:v 1 "$OUT/Tight-679.png"

resize() { sips -z "$2" "$2" "$1" --out "$3" >/dev/null; }

# macOS app icon (16 to 1024, with @2x)
SET="$OUT/AppIcon.iconset"; rm -rf "$SET"; mkdir -p "$SET"
for s in 16 32 128 256 512; do
  resize "$OUT/AppIcon-1024.png" "$s" "$SET/icon_${s}x${s}.png"
  resize "$OUT/AppIcon-1024.png" "$((s * 2))" "$SET/icon_${s}x${s}@2x.png"
done
iconutil -c icns "$SET" -o "$OUT/AppIcon.icns"
cp "$OUT/AppIcon.icns" "$OUT/VolumeIcon.icns"     # DMG volume icon
rm -rf "$SET"

# Web app: favicon, header mark (28 px at 1x/2x/3x), PWA and home-screen icons
resize "$OUT/Tight-679.png" 32 "$ROOT/static/icons/favicon-32.png"
resize "$OUT/Tight-679.png" 16 "$ROOT/static/icons/favicon-16.png"
for m in 1 2 3; do resize "$OUT/Tight-679.png" $((28 * m)) "$ROOT/static/icons/mark-$((28 * m)).png"; done
resize "$OUT/AppIcon-1024.png" 192 "$ROOT/static/icons/icon-192.png"
resize "$OUT/AppIcon-1024.png" 512 "$ROOT/static/icons/icon-512.png"
resize "$OUT/FullBleed-1024.png" 512 "$ROOT/static/icons/maskable-512.png"
resize "$OUT/FullBleed-1024.png" 180 "$ROOT/static/icons/apple-touch-icon.png"

# Chrome extension: tight tile at small sizes, a little margin at 128
for s in 16 32 48; do resize "$OUT/Tight-679.png" "$s" "$ROOT/chrome-extension/icons/icon-$s.png"; done
ffmpeg -v error -y -i "$OUT/Tight-679.png" \
  -vf "scale=112:112:flags=lanczos,format=rgba,pad=128:128:8:8:color=0x00000000" \
  -frames:v 1 "$ROOT/chrome-extension/icons/icon-128.png"

echo "Icons written to assets/icon, static/icons and chrome-extension/icons"
