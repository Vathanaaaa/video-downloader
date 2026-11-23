# Video Downloader

A Mac app that saves video or audio from a link. It uses yt-dlp to download, then checks every video with ffprobe and converts it to MP4 (H.264 and AAC) when needed, so it plays in QuickTime and Quick Look. That includes Instagram reels that arrive as VP9.

Everything runs on your Mac. Nothing is uploaded, and there are no accounts or analytics.

## Install

1. Download the DMG for your Mac from [Releases](https://github.com/Vathanaaaa/video-downloader/releases):
   - `…-arm64.dmg` for Apple silicon (M1 and later)
   - `…-x86_64.dmg` for Intel
2. Open the DMG and drag Video Downloader to Applications.
3. The app is not notarized, so the first time macOS blocks it. Right-click the app, choose Open, then Open again. On recent macOS versions you may need System Settings › Privacy & Security › Open Anyway instead.

yt-dlp, FFmpeg and Deno are inside the app. You don't need Homebrew or Python.

## Use

- **Download:** paste a link and press Return. Pick Video or Audio, and a resolution if you like. Each resolution shows its approximate size. If you copied a link, the app offers it when you switch back to the window.
- **Convert:** drop video or audio files on the window, or click Choose files. You get MP3, M4A or WAV next to each original. Existing files are never replaced: a new name like `song (1).mp3` is used instead.
- **Keyboard:** ⌘V pastes a link, Return downloads, Esc cancels, ⌘, opens Settings.

Files are saved to `~/Downloads/Video Downloader` unless you choose another folder in Settings. Names look like `Title [video id].mp4`. Downloading the same thing again saves a new copy, like `Title [video id] (1).mp4`. Existing files are never replaced or deleted.

**Instagram or another site wants a login?** The error offers "Retry with Chrome" (or your browser). Or in Settings, set "Use cookies from browser" to a browser where you're logged in.

**A site stopped working?** Settings › Update yt-dlp fetches the newest official yt-dlp and checks its checksum.

## Chrome extension

The extension adds a button that sends the current tab to the app.

1. In Chrome, open `chrome://extensions`, turn on Developer mode, click Load unpacked, and pick the `chrome-extension` folder from this repository.
2. In the app, open Settings and copy the token under "Connect Chrome extension".
3. Open the extension's options, paste the token, and click Save and test.

The app only accepts the extension's requests when they carry this token.

## Use from your phone

Settings › Use from my phone lets a phone on the same Wi-Fi use the app. Scan the QR code and enter the 6-digit PIN shown on the Mac. Downloads still run and save on the Mac. The phone can save a copy from Recent downloads.

While this is off, the app listens only on 127.0.0.1. It turns off when you quit. After 5 wrong PINs in 10 minutes from one device, that device has to wait. After 20 wrong PINs in total, the PIN changes.

## Where things are kept

`~/Library/Application Support/Video Downloader/` holds settings, the last 10 downloads, the extension token and any updated yt-dlp. Delete that folder to reset the app.

## Build from source

You need macOS 12 or later and Python 3.10 or later.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r packaging/requirements.txt
packaging/fetch-binaries.sh "$(uname -m)"
packaging/build.sh
```

The DMG is written to `dist/`. Each build targets the Mac it runs on, so build Intel on an Intel Mac. Pushing a `v*` tag runs `.github/workflows/release.yml`, which builds both DMGs and attaches them, with the FFmpeg source, to a draft release.

`fetch-binaries.sh` downloads the versions pinned in `packaging/binaries.env` and refuses any file whose SHA-256 does not match.

Martin Riedl's build server plans to stop making Intel release builds of FFmpeg in January 2027. Intel builds after that need another FFmpeg source.

To run from source without building, double-click `start.command`.

## Licence

MIT for Video Downloader's own code. The bundled FFmpeg is GPL version 3, so the DMG as distributed also follows the GPL for those binaries. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Only download content you have the right to save.
