# Third-party notices

Video Downloader's own code is under the MIT License (see `LICENSE`). The Mac app bundles the programs and libraries below. Each keeps its own licence. In the app, the licence texts are in `Video Downloader.app/Contents/Resources/licenses/`.

## Bundled programs

### FFmpeg and FFprobe 9.1 (GPL version 3)

- What it does: converts and checks video and audio.
- Build: static macOS builds by Martin Riedl, https://ffmpeg.martin-riedl.de. The build script is at https://git.martin-riedl.de/ffmpeg/build-script.
- Licence: these builds include GPL components such as x264 and x265, so the binaries as a whole are under the GNU General Public License version 3. The full text is in `licenses/COPYING.GPLv3`, with FFmpeg's own `LICENSE.md`.
- Version and configuration: `licenses/FFMPEG-BUILD.txt` records the exact binary URL, `ffmpeg -version` and `ffmpeg -buildconf`.
- Source code: every GitHub release of Video Downloader has the matching FFmpeg source archive (`ffmpeg-9.1.tar.xz`) attached. It is also at https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz. The build script linked above has the source of the extra libraries.
- FFmpeg runs as a separate program. Video Downloader only starts it with arguments; it does not link to it.

### yt-dlp 2026.10.26 (The Unlicense)

- What it does: reads video pages and downloads the media.
- Source: https://github.com/yt-dlp/yt-dlp. The macOS binary is the official `yt-dlp_macos` release file.
- Licence: public domain dedication (The Unlicense). The binary includes Python and other components under their own permissive licences, listed in the yt-dlp repository.
- The Update yt-dlp button in Settings downloads newer official releases into `~/Library/Application Support/Video Downloader/bin/` and checks them against the published SHA-256 sums.

### Deno 2.9.8 (MIT)

- What it does: yt-dlp uses it to run YouTube's JavaScript challenges.
- Source: https://github.com/denoland/deno

```
MIT License

Copyright 2018-2026 the Deno authors

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## Python libraries in the app

| Library | Licence | Source |
|---|---|---|
| Python 3.12 | PSF License | https://www.python.org |
| Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, click, blinker | BSD 3-Clause | https://palletsprojects.com |
| yt-dlp (library, for Arc cookies) | The Unlicense | https://github.com/yt-dlp/yt-dlp |
| pywebview | BSD 3-Clause | https://github.com/r0x0r/pywebview |
| bottle, proxy_tools (pywebview dependencies) | MIT | https://bottlepy.org, https://github.com/jtushman/proxy_tools |
| PyObjC | MIT | https://github.com/ronaldoussoren/pyobjc |
| segno (QR codes) | BSD 3-Clause | https://github.com/heuer/segno |
| PyInstaller bootloader | GPL 2.0 with an exception that allows bundling any program | https://pyinstaller.org |
