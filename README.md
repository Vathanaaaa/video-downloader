# Video Downloader

A small Mac app that downloads video or audio from a link with yt-dlp. Videos are saved as MP4 (H.264 + AAC) and checked with ffprobe, so they play in QuickTime and Quick Look, including Instagram reels that come as VP9.

**Needs:** macOS, [Homebrew](https://brew.sh), then `brew install ffmpeg python` in Terminal.

**Install:** `git clone https://github.com/Vathanaaaa/video-downloader.git`, or Code › Download ZIP on GitHub.

**Start:** double-click `start.command`. If you downloaded the ZIP, macOS blocks it the first time: right-click it, choose Open, then Open. The first run installs Flask and yt-dlp into `.venv`. The app opens at http://127.0.0.1:8765 and stops when you close the Terminal window.

**Files land in** `~/Downloads/Video Downloader` unless you pick another folder in Settings. Names look like `Title [video id].mp4`.

**Instagram blocked?** In Settings, set "Use cookies from browser" to a browser where you're logged in to Instagram.

The app runs only on your Mac (127.0.0.1) and nothing is uploaded. Settings and the last 10 downloads are kept in `data.json`. `preview.html` is the design reference and opens without the server.

Only download content you have the right to save.
