#!/bin/bash
# Usage: packaging/make-dmg.sh "path/Video Downloader.app" out.dmg
set -euo pipefail
APP="$1"; OUT="$2"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

ditto "$APP" "$STAGE/Video Downloader.app"
ln -s /Applications "$STAGE/Applications"
cp "$ROOT/assets/icon/VolumeIcon.icns" "$STAGE/.VolumeIcon.icns"

rm -f "$OUT"
hdiutil create -volname "Video Downloader" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$OUT.tmp.dmg" >/dev/null
# Show the volume icon: set the custom-icon flag on the mounted volume, then recompress.
MNT="$(mktemp -d)"
hdiutil convert "$OUT.tmp.dmg" -format UDRW -o "$OUT.rw.dmg" >/dev/null
hdiutil attach "$OUT.rw.dmg" -mountpoint "$MNT" -nobrowse -quiet
SetFile -a C "$MNT" 2>/dev/null || true
hdiutil detach "$MNT" -quiet
hdiutil convert "$OUT.rw.dmg" -format UDZO -imagekey zlib-level=9 -o "$OUT" >/dev/null
rm -f "$OUT.tmp.dmg" "$OUT.rw.dmg"
rmdir "$MNT" 2>/dev/null || true
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
