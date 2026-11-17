"""Video Downloader: a local yt-dlp front end. Serves on 127.0.0.1 only."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_from_directory

import yt_dlp
import yt_dlp.cookies as ytc
from yt_dlp.utils import DownloadCancelled

HOST = "127.0.0.1"
PORT = 8765
BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DATA_FILE = BASE / "data.json"
DEFAULT_FOLDER = "~/Downloads/Video Downloader"
BROWSERS = ("off", "safari", "chrome", "arc", "firefox")
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
RECENT_MAX = 10
BEST_CAP = 1080

# Finder launches .command files with a short PATH; make Homebrew tools findable.
for _p in ("/opt/homebrew/bin", "/usr/local/bin"):
    if _p not in os.environ.get("PATH", "").split(":"):
        os.environ["PATH"] = os.environ.get("PATH", "") + ":" + _p


# --- Arc support ---------------------------------------------------------
# yt-dlp has no "arc" browser. Arc is Chromium-based, so register its profile
# folder and its "Arc Safe Storage" keychain entry with yt-dlp's Chromium reader.
def _register_arc():
    if "arc" in ytc.SUPPORTED_BROWSERS:
        return
    original = ytc._get_chromium_based_browser_settings

    def settings(browser_name):
        if browser_name == "arc":
            return {
                "browser_dir": os.path.expanduser("~/Library/Application Support/Arc/User Data"),
                "keyring_name": "Arc",
                "supports_profiles": True,
            }
        return original(browser_name)

    ytc._get_chromium_based_browser_settings = settings
    ytc.CHROMIUM_BASED_BROWSERS.add("arc")
    ytc.SUPPORTED_BROWSERS.add("arc")


_register_arc()


# --- Settings and recent downloads (data.json) ---------------------------
_data_lock = threading.Lock()


def load_data():
    try:
        data = json.loads(DATA_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    settings = data.get("settings") or {}
    if not isinstance(settings.get("folder"), str) or not settings["folder"].strip():
        settings["folder"] = DEFAULT_FOLDER
    if settings.get("cookies") not in BROWSERS:
        settings["cookies"] = "off"
    recent = data.get("recent") if isinstance(data.get("recent"), list) else []
    return {"settings": settings, "recent": recent[:RECENT_MAX]}


def save_data(data):
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, DATA_FILE)


def add_recent(item):
    with _data_lock:
        data = load_data()
        data["recent"] = [r for r in data["recent"] if r.get("path") != item["path"]]
        data["recent"].insert(0, item)
        data["recent"] = data["recent"][:RECENT_MAX]
        save_data(data)


# --- Tools ---------------------------------------------------------------
def tool(name):
    return shutil.which(name)


def tool_version(name):
    path = tool(name)
    if not path:
        return None
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"version (\S+)", out)
    return m.group(1) if m else "installed"


def ytdlp_version():
    try:
        out = subprocess.run(
            [sys.executable, "-c", "import yt_dlp.version as v; print(v.__version__)"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return out or yt_dlp.version.__version__
    except (OSError, subprocess.SubprocessError):
        return yt_dlp.version.__version__


def probe(path):
    """Return {'video': stream|None, 'audio': stream|None, 'duration': float, 'size': int}."""
    out = subprocess.run(
        [tool("ffprobe"), "-v", "error",
         "-show_entries", "stream=codec_type,codec_name,pix_fmt,width,height",
         "-show_entries", "format=duration,size",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    j = json.loads(out.stdout or "{}")
    streams = j.get("streams", [])
    fmt = j.get("format", {})
    return {
        "video": next((s for s in streams if s.get("codec_type") == "video"), None),
        "audio": next((s for s in streams if s.get("codec_type") == "audio"), None),
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or 0),
    }


def plays_in_quicktime(path, info):
    """MP4 with H.264 yuv420p video and AAC audio (or no audio track)."""
    v, a = info["video"], info["audio"]
    return (
        Path(path).suffix.lower() == ".mp4"
        and v is not None
        and v.get("codec_name") == "h264"
        and v.get("pix_fmt") == "yuv420p"
        and (a is None or a.get("codec_name") == "aac")
    )


# --- Errors --------------------------------------------------------------
ANSI = re.compile(r"\x1b\[[0-9;]*m")

ERRORS = {
    "ig_rate_limit": ("Instagram blocked the request",
                      "Instagram is rate-limiting you. Wait a few minutes, or turn on browser cookies in Settings."),
    "login_required": ("This video needs a login",
                       "It's private or needs a login. Log in to the site in your browser, "
                       "then pick that browser under cookies in Settings."),
    "unsupported_url": ("This link isn't supported",
                        "yt-dlp can't download from this page. Check that the link opens a single video or post."),
    "network": ("Couldn't reach the site",
                "Check your internet connection, then retry."),
    "ffmpeg_missing": ("ffmpeg is not installed",
                       "Run brew install ffmpeg in Terminal, then retry."),
    "cookies": ("Couldn't read browser cookies",
                "Make sure the browser is installed and you've logged in to the site with it. "
                "For Safari, give Terminal Full Disk Access in System Settings > Privacy & Security."),
    "bad_url": ("That isn't a web link",
                "Paste a link that starts with http:// or https://."),
    "playlist": ("Playlists aren't supported",
                 "Paste the link to a single video."),
    "convert": ("Conversion failed",
                "ffmpeg couldn't convert the file for QuickTime. The original download was kept."),
    "failed": ("Download failed",
               "yt-dlp reported an error. See details, or update yt-dlp in Settings and retry."),
}


def classify(message, url=""):
    m = ANSI.sub("", message or "")
    low = m.lower()
    host = urlparse(url).netloc.lower()
    if "ffmpeg" in low and ("not found" in low or "not installed" in low):
        code = "ffmpeg_missing"
    elif ("instagram" in low or "instagram.com" in host) and any(
            k in low for k in ("401", "rate-limit", "rate limit", "wait a few minutes",
                               "login required", "not available")):
        code = "ig_rate_limit"
    elif any(k in low for k in ("could not find", "cookies database", "operation not permitted",
                                "failed to load cookies", "safe storage", "keychain")) and "cookie" in low:
        code = "cookies"
    elif "unsupported url" in low:
        code = "unsupported_url"
    elif any(k in low for k in ("private", "login required", "log in", "sign in to confirm",
                                "members-only", "this content isn't available", "authentication",
                                "requires authentication", "age-restricted", "confirm your age")):
        code = "login_required"
    elif any(k in low for k in ("unable to download webpage", "urlopen error", "timed out",
                                "nodename nor servname", "name or service not known",
                                "network is unreachable", "connection refused", "connection reset",
                                "failed to resolve", "temporary failure in name resolution",
                                "remote end closed", "ssl")):
        code = "network"
    else:
        code = "failed"
    return error_payload(code, m)


def error_payload(code, detail=""):
    title, message = ERRORS[code]
    return {"code": code, "title": title, "message": message, "detail": (detail or "").strip()[:2000]}


class QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def base_opts(settings):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": QuietLogger(),
        "color": {"stdout": "no_color", "stderr": "no_color"},
    }
    if settings["cookies"] != "off":
        opts["cookiesfrombrowser"] = (settings["cookies"], None, None, None)
    ff = tool("ffmpeg")
    if ff:
        opts["ffmpeg_location"] = str(Path(ff).parent)
    return opts


def valid_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        return False
    p = urlparse(url.strip())
    return p.scheme in ("http", "https") and bool(p.netloc)


def short_side(f):
    w, h = f.get("width"), f.get("height")
    if w and h:
        return min(w, h)
    return h


# --- Jobs ----------------------------------------------------------------
JOBS = {}


class Job:
    def __init__(self, url, mode, height, audio_format):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.mode = mode
        self.height = height
        self.audio_format = audio_format
        self.state = "fetching"          # fetching, downloading, converting, done, error, canceled
        self.label = "Fetching"
        self.percent = None
        self.speed = None
        self.eta = None
        self.total = None
        self.file = None
        self.result = None
        self.error = None
        self.cancel = threading.Event()
        self.proc = None
        self.started = time.time()
        self.video_id = None
        self.folder = None
        self.preexisting = set()
        self.reencoded = False

    def public(self):
        return {
            "id": self.id, "state": self.state, "label": self.label,
            "percent": self.percent, "speed": self.speed, "eta": self.eta, "total": self.total,
            "result": self.result, "error": self.error,
        }


def video_format(height):
    # Highest resolution at or below the cap; within it prefer H.264 + AAC so most
    # downloads need no re-encode. `res` compares the short side, so vertical
    # 1080x1920 reels count as 1080p.
    sort = [f"res:{height}", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]
    return "bv*+ba/b", sort


def run_job(job):
    settings = load_data()["settings"]
    folder = Path(os.path.expanduser(settings["folder"]))
    job.folder = folder
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        job.error = error_payload("failed", f"Can't create the download folder {folder}: {e}")
        job.state = "error"
        return
    if not tool("ffmpeg") or not tool("ffprobe"):
        job.error = error_payload("ffmpeg_missing")
        job.state = "error"
        return

    parts = {"index": 0, "names": [], "weights": [1.0]}

    def progress(d):
        if job.cancel.is_set():
            raise DownloadCancelled()
        if d.get("status") != "downloading":
            return
        name = d.get("filename")
        if name not in parts["names"]:
            parts["names"].append(name)
        idx = parts["names"].index(name)
        weights = parts["weights"]
        done_bytes = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        frac = min(done_bytes / total, 1.0) if total else 0.0
        before = sum(weights[:idx])
        pct = (before + weights[min(idx, len(weights) - 1)] * frac) / sum(weights) * 100
        job.state = "downloading"
        job.label = "Downloading"
        job.percent = round(min(pct, 100), 1)
        job.speed = d.get("speed")
        job.eta = d.get("eta")
        job.total = total

    def pp_hook(d):
        if job.cancel.is_set():
            raise DownloadCancelled()
        if d.get("status") == "started":
            name = d.get("postprocessor", "")
            job.state = "converting"
            job.percent = None
            job.speed = job.eta = None
            if name == "Merger":
                job.label = "Merging video and audio…"
            elif name == "ExtractAudio":
                job.label = f"Converting to {job.audio_format.upper()}…"
            else:
                job.label = "Finishing…"

    opts = base_opts(settings)
    opts.update({
        "paths": {"home": str(folder)},
        "outtmpl": {"default": "%(title).80s [%(id)s].%(ext)s"},
        "progress_hooks": [progress],
        "postprocessor_hooks": [pp_hook],
        "overwrites": True,
    })
    if job.mode == "video":
        fmt, sort = video_format(job.height)
        opts.update({"format": fmt, "format_sort": sort, "merge_output_format": "mp4"})
    else:
        opts.update({
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": job.audio_format, "preferredquality": "192"},
                {"key": "FFmpegMetadata", "add_metadata": True},
            ],
        })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=False)
            if info.get("_type") in ("playlist", "multi_video") and not info.get("formats"):
                job.error = error_payload("playlist")
                job.state = "error"
                return
            job.video_id = info.get("id")
            job.preexisting = matching_files(folder, job.video_id)
            requested = info.get("requested_formats") or [info]
            sizes = [f.get("filesize") or f.get("filesize_approx") or 0 for f in requested]
            parts["weights"] = sizes if all(sizes) else [1.0] * len(requested)
            job.state = "downloading"
            job.label = "Downloading"
            job.percent = 0
            info = ydl.process_ie_result(info, download=True)
        path = Path(info["requested_downloads"][0]["filepath"])
    except DownloadCancelled:
        cleanup(job)
        job.state = "canceled"
        return
    except Exception as e:  # yt-dlp raises many error types; all go through classify()
        if job.cancel.is_set():
            cleanup(job)
            job.state = "canceled"
            return
        cleanup(job)
        job.error = classify(str(e), job.url)
        job.state = "error"
        return

    if job.cancel.is_set():
        cleanup(job)
        job.state = "canceled"
        return

    try:
        if job.mode == "video":
            path = ensure_quicktime(job, path)
            if path is None:
                return
        meta = probe(path)
    except Exception as e:
        job.error = error_payload("convert", str(e))
        job.state = "error"
        return

    v, a = meta["video"], meta["audio"]
    result = {
        "path": str(path),
        "name": path.name,
        "folder": str(path.parent),
        "kind": job.mode,
        "ext": path.suffix.lstrip(".").upper(),
        "vcodec": v.get("codec_name") if v else None,
        "pix_fmt": v.get("pix_fmt") if v else None,
        "acodec": a.get("codec_name") if a else None,
        "height": short_side(v) if v else None,
        "size": meta["size"],
        "reencoded": job.reencoded,
        "title": info.get("title") or path.stem,
        "at": int(time.time()),
    }
    job.result = result
    job.file = path
    add_recent(result)
    job.state = "done"
    job.label = "Done"
    job.percent = 100


def ensure_quicktime(job, path):
    """Probe the file; re-encode to H.264/AAC MP4 if QuickTime can't play it."""
    meta = probe(path)
    if plays_in_quicktime(path, meta):
        return path

    job.reencoded = True
    job.state = "converting"
    job.label = "Converting for QuickTime…"
    job.percent = 0
    job.speed = job.eta = None

    final = path.with_suffix(".mp4")
    tmp = path.with_name(path.stem + ".converting.mp4")
    cmd = [
        tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?",
        # libx264 with yuv420p needs even dimensions.
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(tmp),
    ]
    duration = meta["duration"] or 0
    with tempfile.TemporaryFile() as errf:
        job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True)
        for line in job.proc.stdout:
            if line.startswith("out_time_us=") and duration:
                try:
                    secs = int(line.split("=", 1)[1]) / 1_000_000
                    job.percent = round(max(0.0, min(secs / duration * 100, 99.9)), 1)
                except ValueError:
                    pass
        code = job.proc.wait()
        errf.seek(0)
        err = errf.read().decode("utf-8", "replace")[-2000:]

    if job.cancel.is_set():
        tmp.unlink(missing_ok=True)
        cleanup(job)
        job.state = "canceled"
        return None
    if code != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        job.error = error_payload("convert", err)
        job.state = "error"
        return None

    check = probe(tmp)
    if not plays_in_quicktime(tmp, check):
        tmp.unlink(missing_ok=True)
        job.error = error_payload("convert", f"Converted file still not H.264/AAC: {check}")
        job.state = "error"
        return None
    os.replace(tmp, final)
    if final != path:
        path.unlink(missing_ok=True)
    return final


def matching_files(folder, video_id):
    if not video_id:
        return set()
    return {p for p in folder.glob(f"*[[]{video_id}[]]*")}


def cleanup(job):
    """Delete files this job created (partial downloads, fragments, temp files)."""
    if not job.folder or not job.video_id:
        return
    for p in matching_files(job.folder, job.video_id) - job.preexisting:
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass


# --- Flask app -----------------------------------------------------------
app = Flask(__name__, static_folder=None)


@app.before_request
def only_localhost():
    if request.host not in ALLOWED_HOSTS:
        abort(403)
    origin = request.headers.get("Origin")
    if origin and origin not in ALLOWED_ORIGINS:
        abort(403)
    if request.method == "POST" and not request.is_json:
        abort(415)


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(415)
def http_error(e):
    return jsonify({"error": {"code": "http", "title": e.name, "message": e.description}}), e.code


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC, name)


@app.get("/api/env")
def env():
    ff, fp = tool_version("ffmpeg"), tool_version("ffprobe")
    missing = []
    if not ff or not fp:
        missing.append("ffmpeg")
    return jsonify({
        "ytdlp": yt_dlp.version.__version__,
        "ffmpeg": ff,
        "ffprobe": fp,
        "ok": not missing,
        "command": f"brew install {' '.join(missing)}" if missing else None,
    })


@app.post("/api/info")
def info():
    url = (request.get_json(silent=True) or {}).get("url", "")
    if not valid_url(url):
        return jsonify({"error": error_payload("bad_url")}), 400
    settings = load_data()["settings"]
    opts = base_opts(settings)
    opts["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url.strip(), download=False)
    except Exception as e:
        return jsonify({"error": classify(str(e), url)}), 422
    if data.get("_type") in ("playlist", "multi_video") and not data.get("formats"):
        return jsonify({"error": error_payload("playlist")}), 422

    heights = sorted({
        short_side(f) for f in data.get("formats") or []
        if f.get("vcodec") not in (None, "none") and short_side(f)
    }, reverse=True)
    if not heights and data.get("height"):
        heights = [short_side(data)]
    thumb = data.get("thumbnail")
    return jsonify({
        "title": data.get("title") or "Untitled",
        "uploader": data.get("uploader") or data.get("channel") or data.get("uploader_id"),
        "duration": data.get("duration"),
        "thumbnail": thumb if isinstance(thumb, str) and thumb.startswith(("http://", "https://")) else None,
        "site": urlparse(data.get("webpage_url") or url).netloc.removeprefix("www."),
        "heights": heights,
        "has_video": bool(heights),
    })


@app.post("/api/download")
def download():
    body = request.get_json(silent=True) or {}
    url = body.get("url", "")
    if not valid_url(url):
        return jsonify({"error": error_payload("bad_url")}), 400
    mode = body.get("mode")
    if mode not in ("video", "audio"):
        abort(400)
    height = body.get("height", "best")
    if height == "best":
        height = BEST_CAP
    elif not (isinstance(height, int) and 100 <= height <= 4320):
        abort(400)
    audio_format = body.get("audio_format", "mp3")
    if audio_format not in ("mp3", "m4a"):
        abort(400)
    job = Job(url.strip(), mode, height, audio_format)
    JOBS[job.id] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return jsonify(job.public())


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id) or abort(404)
    return jsonify(job.public())


@app.post("/api/jobs/<job_id>/cancel")
def job_cancel(job_id):
    job = JOBS.get(job_id) or abort(404)
    job.cancel.set()
    if job.proc and job.proc.poll() is None:
        job.proc.kill()
    return jsonify({"ok": True})


@app.get("/api/settings")
def get_settings():
    return jsonify(load_data()["settings"])


@app.post("/api/settings")
def set_settings():
    body = request.get_json(silent=True) or {}
    with _data_lock:
        data = load_data()
        if "folder" in body:
            folder = body["folder"]
            if not isinstance(folder, str) or not folder.strip():
                return jsonify({"error": {"message": "Enter a folder path."}}), 400
            expanded = os.path.expanduser(folder.strip())
            if not os.path.isabs(expanded):
                return jsonify({"error": {"message": "Use a full path, like ~/Downloads/Video Downloader."}}), 400
            data["settings"]["folder"] = folder.strip()
        if "cookies" in body:
            if body["cookies"] not in BROWSERS:
                abort(400)
            data["settings"]["cookies"] = body["cookies"]
        save_data(data)
    return jsonify(data["settings"])


@app.get("/api/recent")
def recent():
    items = load_data()["recent"]
    for r in items:
        r["exists"] = os.path.exists(r.get("path", ""))
    return jsonify(items)


def resolve_file(body):
    """Only files this app downloaded: a job id or an index into recent downloads."""
    if "job_id" in body:
        job = JOBS.get(body["job_id"])
        path = str(job.file) if job and job.file else None
    elif isinstance(body.get("recent"), int):
        items = load_data()["recent"]
        i = body["recent"]
        path = items[i]["path"] if 0 <= i < len(items) else None
    else:
        path = None
    return path if path and os.path.exists(path) else None


@app.post("/api/reveal")
def reveal():
    path = resolve_file(request.get_json(silent=True) or {})
    if not path:
        return jsonify({"error": {"message": "The file was moved or deleted."}}), 404
    subprocess.run(["open", "-R", path], check=False)
    return jsonify({"ok": True})


@app.post("/api/open")
def open_file():
    path = resolve_file(request.get_json(silent=True) or {})
    if not path:
        return jsonify({"error": {"message": "The file was moved or deleted."}}), 404
    subprocess.run(["open", path], check=False)
    return jsonify({"ok": True})


@app.post("/api/update-ytdlp")
def update_ytdlp():
    if any(j.state in ("fetching", "downloading", "converting") for j in JOBS.values()):
        return jsonify({"error": {"message": "Wait for the current download to finish."}}), 409
    before = yt_dlp.version.__version__
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "yt-dlp"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        return jsonify({"error": {"message": "Update failed. Check your internet connection.",
                                  "detail": proc.stderr[-2000:]}}), 500
    after = ytdlp_version()
    restarting = after != before
    if restarting:
        # Load the new yt-dlp by restarting this process in place.
        threading.Timer(0.5, lambda: os.execv(sys.executable, [sys.executable, str(BASE / "app.py")])).start()
    return jsonify({"before": before, "after": after, "restarting": restarting})


if __name__ == "__main__":
    # Keep the Terminal window readable: no startup banner, no per-request lines.
    import logging
    import flask.cli
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    flask.cli.show_server_banner = lambda *args, **kwargs: None
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
