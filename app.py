"""Video Downloader: a local yt-dlp front end.

Serves on 127.0.0.1. While "Use from my phone" is on, a second server also
listens on the Mac's Wi-Fi address and every request there needs the PIN.
"""

import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, g, jsonify, request, send_file, send_from_directory

APP_NAME = "Video Downloader"
APP_VERSION = "2.0.0"
HOST = "127.0.0.1"
PORT = int(os.environ.get("VD_PORT", "8765"))

FROZEN = getattr(sys, "frozen", False)
BASE = Path(__file__).resolve().parent
RES = Path(getattr(sys, "_MEIPASS", BASE))          # bundled resources (PyInstaller) or the repo
STATIC = RES / "static"
BUNDLED_BIN = RES / "bin"                             # yt-dlp, ffmpeg, ffprobe, deno
SUPPORT = Path(os.environ.get("VD_SUPPORT_DIR")
               or os.path.expanduser(f"~/Library/Application Support/{APP_NAME}"))
DATA_FILE = SUPPORT / "data.json"
TOKEN_FILE = SUPPORT / "extension-token"
USER_BIN = SUPPORT / "bin"                            # yt-dlp replaced by the Update button

DEFAULT_FOLDER = "~/Downloads/Video Downloader"
BROWSERS = ("off", "safari", "chrome", "arc", "firefox")
BROWSER_APPS = {"safari": "Safari", "chrome": "Google Chrome", "arc": "Arc", "firefox": "Firefox"}
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
RECENT_MAX = 10
BEST_CAP = 1080
AUDIO_EXT = {"mp3", "m4a"}
MEDIA_SUFFIXES = {".mp4", ".mp3", ".m4a"}
YTDLP_RELEASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"

SUPPORT.mkdir(parents=True, exist_ok=True)


# --- Bundled tools -------------------------------------------------------
def _executable(p):
    return p.is_file() and os.access(p, os.X_OK)


def bin_path(name):
    """A bundled binary. Outside the app bundle, fall back to PATH (Homebrew) for development."""
    p = BUNDLED_BIN / name
    if _executable(p):
        return str(p)
    if FROZEN:
        return None
    return shutil.which(name, path=os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin")


def ytdlp_cmd():
    user = USER_BIN / "yt-dlp"
    if _executable(user):
        return [str(user)]
    bundled = BUNDLED_BIN / "yt-dlp"
    if _executable(bundled):
        return [str(bundled)]
    if not FROZEN:
        return [sys.executable, "-m", "yt_dlp"]      # development: the venv's yt-dlp
    return None


_version_cache = {}


def tool_version(cmd, flag="-version", pattern=r"version (\S+)"):
    if not cmd:
        return None
    key = (tuple(cmd), flag)
    if key in _version_cache:
        return _version_cache[key]
    try:
        out = subprocess.run([*cmd, flag], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(pattern, out) if pattern else None
    v = m.group(1) if m else (out.strip().splitlines() or ["installed"])[0]
    _version_cache[key] = v
    return v


def ytdlp_version():
    return tool_version(ytdlp_cmd(), "--version", None)


def ffmpeg():
    return bin_path("ffmpeg")


def ffprobe():
    return bin_path("ffprobe")


# --- Settings and recent downloads ---------------------------------------
_data_lock = threading.Lock()


def _migrate_old_data():
    """Earlier versions kept data.json next to app.py. Copy it once; the old file stays."""
    old = BASE / "data.json"
    if not DATA_FILE.exists() and old.is_file():
        try:
            shutil.copy2(old, DATA_FILE)
        except OSError:
            pass


_migrate_old_data()


def load_data():
    try:
        data = json.loads(DATA_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    s = data.get("settings") or {}
    if not isinstance(s.get("folder"), str) or not s["folder"].strip():
        s["folder"] = DEFAULT_FOLDER
    if s.get("cookies") not in BROWSERS:
        s["cookies"] = "off"
    if s.get("height") != "best" and not (isinstance(s.get("height"), int) and 100 <= s["height"] <= 4320):
        s["height"] = "best"
    if s.get("audio_format") not in AUDIO_EXT:
        s["audio_format"] = "mp3"
    recent = data.get("recent") if isinstance(data.get("recent"), list) else []
    return {"settings": s, "recent": recent[:RECENT_MAX]}


def save_data(data):
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, DATA_FILE)


def update_settings(**changes):
    with _data_lock:
        data = load_data()
        data["settings"].update(changes)
        save_data(data)
        return data["settings"]


def add_recent(item):
    with _data_lock:
        data = load_data()
        data["recent"] = [r for r in data["recent"] if r.get("path") != item["path"]]
        data["recent"].insert(0, item)
        data["recent"] = data["recent"][:RECENT_MAX]
        save_data(data)


def download_folder():
    return Path(os.path.expanduser(load_data()["settings"]["folder"]))


# --- Chrome extension token ----------------------------------------------
_token_lock = threading.Lock()


def extension_token():
    with _token_lock:
        try:
            t = TOKEN_FILE.read_text().strip()
            if len(t) >= 32:
                return t
        except OSError:
            pass
        t = secrets.token_urlsafe(24)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t)
        return t


# --- ffprobe -------------------------------------------------------------
def probe(path):
    """Return {'video': stream|None, 'audio': stream|None, 'duration': float, 'size': int}."""
    out = subprocess.run(
        [ffprobe(), "-v", "error",
         "-show_entries", "stream=codec_type,codec_name,pix_fmt,width,height",
         "-show_entries", "format=duration,size",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120,
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
                      "Instagram is rate-limiting you or wants you logged in. "
                      "Retry with a browser where you're logged in to Instagram."),
    "login_required": ("This video needs a login",
                       "It's private or needs a login. Retry with a browser where you're logged in to the site."),
    "unsupported_url": ("This link isn't supported",
                        "yt-dlp can't download from this page. Check that the link opens a single video or post."),
    "network": ("Couldn't reach the site", "Check your internet connection, then retry."),
    "tools_missing": ("Part of the app is missing",
                      "yt-dlp or ffmpeg couldn't be found. Download Video Downloader again from GitHub."),
    "cookies": ("Couldn't read browser cookies",
                "Make sure you're logged in to the site in that browser. For Safari, give Video Downloader "
                "Full Disk Access in System Settings › Privacy & Security."),
    "bad_url": ("That isn't a web link", "Paste a link that starts with http:// or https://."),
    "playlist": ("Playlists aren't supported", "Paste the link to a single video."),
    "convert": ("Conversion failed",
                "ffmpeg couldn't convert the file for QuickTime. The original download was kept."),
    "folder": ("Can't save to the download folder",
               "Pick another folder in Settings, or check that the drive is connected."),
    "failed": ("Download failed",
               "yt-dlp reported an error. See details, or update yt-dlp in Settings and retry."),
}
FIXABLE_WITH_COOKIES = {"ig_rate_limit", "login_required", "cookies"}


def installed_browsers():
    found = []
    for key, name in BROWSER_APPS.items():
        for root in ("/Applications", os.path.expanduser("~/Applications")):
            if os.path.isdir(f"{root}/{name}.app"):
                found.append(key)
                break
    return found


def classify(message, url=""):
    m = ANSI.sub("", message or "")
    low = m.lower()
    host = urlparse(url).netloc.lower()
    if ("instagram" in low or "instagram.com" in host) and any(
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
    out = {"code": code, "title": title, "message": message, "detail": (detail or "").strip()[-2000:]}
    if code in FIXABLE_WITH_COOKIES:
        current = load_data()["settings"]["cookies"]
        out["browsers"] = [b for b in installed_browsers() if b != current or code == "cookies"]
        out["current_browser"] = current
    return out


# --- yt-dlp as a subprocess ----------------------------------------------
class YtdlpError(Exception):
    pass


class Cookies:
    """Command-line arguments for browser cookies. Arc isn't a yt-dlp browser, so its
    cookies are exported with the bundled yt-dlp library to a private temp file."""

    def __init__(self, browser):
        self.browser = browser
        self.tmpdir = None

    def __enter__(self):
        if self.browser == "off":
            return []
        if self.browser != "arc":
            return ["--cookies-from-browser", self.browser]
        import yt_dlp.cookies as ytc  # only needed for Arc
        _register_arc(ytc)
        jar = ytc.extract_cookies_from_browser("arc")
        self.tmpdir = tempfile.mkdtemp(prefix="vd-cookies-")
        path = os.path.join(self.tmpdir, "cookies.txt")
        jar.filename = path
        jar.save(ignore_discard=True, ignore_expires=True)
        os.chmod(path, 0o600)
        return ["--cookies", path]

    def __exit__(self, *exc):
        if self.tmpdir:
            shutil.rmtree(self.tmpdir, ignore_errors=True)


def _register_arc(ytc):
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


def common_args():
    args = ["--no-playlist", "--no-warnings", "--no-color", "--ignore-config"]
    ff = ffmpeg()
    if ff:
        args += ["--ffmpeg-location", ff]
    deno = BUNDLED_BIN / "deno"
    if _executable(deno):
        args += ["--js-runtimes", f"deno:{deno}"]
    return args


def _popen(cmd, **kw):
    # Own process group, so cancel also stops the ffmpeg children yt-dlp starts.
    return subprocess.Popen(cmd, start_new_session=True, **kw)


def _kill(proc):
    if proc and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass


def ytdlp_json(url, browser, extra=(), job=None):
    cmd = ytdlp_cmd()
    if not cmd:
        raise YtdlpError("yt-dlp is missing")
    with Cookies(browser) as cookie_args, tempfile.TemporaryFile() as errf:
        proc = _popen([*cmd, "-J", *common_args(), *cookie_args, *extra, "--", url],
                      stdout=subprocess.PIPE, stderr=errf)
        if job:
            job.proc = proc
        try:
            out, _ = proc.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            _kill(proc)
            raise YtdlpError("ERROR: timed out while reading the page")
        errf.seek(0)
        err = errf.read().decode("utf-8", "replace")
    if proc.returncode != 0:
        raise YtdlpError(err or f"yt-dlp exited with {proc.returncode}")
    try:
        return json.loads(out)
    except ValueError:
        raise YtdlpError(err or "yt-dlp returned no data")


def valid_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(c in url for c in "\r\n\t "):
        return False
    p = urlparse(url.strip())
    return p.scheme in ("http", "https") and bool(p.netloc)


def short_side(f):
    w, h = f.get("width"), f.get("height")
    if w and h:
        return min(w, h)
    return h


def _fsize(f, duration):
    s = f.get("filesize") or f.get("filesize_approx")
    if not s and f.get("tbr") and duration:
        s = f["tbr"] * 1000 / 8 * duration
    return int(s) if s else None


def estimate_sizes(data):
    """Approximate file size for each resolution and audio format, from yt-dlp's format list."""
    fmts = data.get("formats") or []
    dur = data.get("duration")
    audios = [f for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best_audio = max(audios, key=lambda f: (str(f.get("acodec", "")).startswith("mp4a"), f.get("abr") or 0),
                     default=None)
    audio_size = _fsize(best_audio, dur) if best_audio else None
    by_height = {}
    for f in fmts:
        h = short_side(f)
        if f.get("vcodec") in (None, "none") or not h:
            continue
        rank = (str(f.get("vcodec", "")).startswith(("avc1", "h264")), f.get("ext") == "mp4", f.get("tbr") or 0)
        if h not in by_height or rank > by_height[h][0]:
            by_height[h] = (rank, f)
    sizes = []
    for h in sorted(by_height, reverse=True):
        f = by_height[h][1]
        v = _fsize(f, dur)
        needs_audio = f.get("acodec") == "none"
        total = None if v is None or (needs_audio and audios and audio_size is None) else v + ((audio_size or 0) if needs_audio else 0)
        sizes.append({"height": h, "size": total})
    if not sizes and data.get("height"):
        sizes.append({"height": short_side(data), "size": _fsize(data, dur)})
    audio = {"mp3": int(dur * 192000 / 8) if dur else None, "m4a": audio_size}
    return sizes, audio


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def find_existing(video_id):
    """A finished download of this video that's still on disk, or None."""
    if not video_id or not VIDEO_ID.match(video_id):
        return None
    tag = f"[{video_id}]"
    for i, r in enumerate(load_data()["recent"]):
        p = r.get("path", "")
        if tag in Path(p).name and os.path.isfile(p):
            return {"path": p, "name": Path(p).name, "kind": r.get("kind"), "size": r.get("size"),
                    "height": r.get("height"), "at": r.get("at")}
    folder = download_folder()
    try:
        files = [p for p in folder.glob(f"*[[]{video_id}[]]*")
                 if p.suffix.lower() in MEDIA_SUFFIXES and ".converting" not in p.name and p.is_file()]
    except OSError:
        return None
    if not files:
        return None
    p = max(files, key=lambda x: x.stat().st_mtime)
    st = p.stat()
    return {"path": str(p), "name": p.name, "kind": "audio" if p.suffix.lower() != ".mp4" else "video",
            "size": st.st_size, "height": None, "at": int(st.st_mtime)}


# --- Download jobs -------------------------------------------------------
JOBS = {}
PROGRESS_TEMPLATE = ("download:VDP %(progress.downloaded_bytes)s %(progress.total_bytes)s "
                     "%(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s %(info.format_id)s")
PP_TEMPLATE = "postprocess:VDPP %(progress.status)s %(progress.postprocessor)s"


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
        self.video_id = None
        self.title = None
        self.folder = None
        self.stage = None                # private folder yt-dlp works in
        self.reencoded = False

    def public(self, phone=False):
        result = self.result
        if result and phone:
            result = {k: v for k, v in result.items() if k not in ("path", "folder")}
        return {
            "id": self.id, "state": self.state, "label": self.label, "title": self.title,
            "percent": self.percent, "speed": self.speed, "eta": self.eta, "total": self.total,
            "result": result, "error": self.error,
        }


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def format_args(job):
    if job.mode == "video":
        return ["-f", "bv*+ba/b", "-S", f"res:{job.height},vcodec:h264,acodec:aac,ext:mp4:m4a",
                "--merge-output-format", "mp4"]
    return ["-f", "bestaudio[ext=m4a]/bestaudio/best", "-x", "--audio-format", job.audio_format,
            "--audio-quality", "192K", "--embed-metadata"]


def run_job(job):
    settings = load_data()["settings"]
    folder = Path(os.path.expanduser(settings["folder"]))
    job.folder = folder
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return fail(job, error_payload("folder", f"{folder}: {e}"))
    if not ytdlp_cmd() or not ffmpeg() or not ffprobe():
        return fail(job, error_payload("tools_missing"))

    try:
        info = ytdlp_json(job.url, settings["cookies"], format_args(job), job)
    except YtdlpError as e:
        if job.cancel.is_set():
            job.state = "canceled"
            return
        return fail(job, classify(str(e), job.url))
    if job.cancel.is_set():
        job.state = "canceled"
        return
    if info.get("_type") in ("playlist", "multi_video") and not info.get("formats"):
        return fail(job, error_payload("playlist"))

    job.video_id = info.get("id")
    job.title = info.get("title")
    # yt-dlp works in a private hidden folder, so it never sees or replaces existing files
    # (it would reuse a same-named .m4a, convert it and delete it). The result is moved
    # out at the end under a name that is free.
    job.stage = folder / f".vd-{job.id}"
    try:
        job.stage.mkdir()
    except OSError as e:
        return fail(job, error_payload("folder", f"{folder}: {e}"))
    requested = info.get("requested_formats") or [info]
    dur = info.get("duration")
    weights = {str(f.get("format_id")): (_fsize(f, dur) or 0) for f in requested}
    if not all(weights.values()):
        weights = {k: 1 for k in weights}
    order = list(weights)
    done_parts = {}

    job.state, job.label, job.percent = "downloading", "Downloading", 0
    workdir = tempfile.mkdtemp(prefix="vd-job-")
    info_path = os.path.join(workdir, "info.json")
    with open(info_path, "w") as f:
        json.dump(info, f)

    final_path = None
    err = ""
    try:
        with Cookies(settings["cookies"]) as cookie_args, tempfile.TemporaryFile() as errf:
            cmd = [*ytdlp_cmd(), *common_args(), *cookie_args, *format_args(job),
                   "--load-info-json", info_path,
                   "-P", f"home:{job.stage}", "-o", "%(title).80s [%(id)s].%(ext)s",
                   "--force-overwrites",
                   "--quiet", "--progress", "--newline", "--no-simulate",
                   "--progress-template", PROGRESS_TEMPLATE,
                   "--progress-template", PP_TEMPLATE,
                   "--print", "after_move:VDFILE %(filepath)s"]
            job.proc = _popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True, bufsize=1)
            for line in job.proc.stdout:
                line = line.rstrip("\n")
                if line.startswith("VDP "):
                    p = line.split(" ")
                    got, total, est, speed, eta, fid = (p + ["NA"] * 7)[1:7]
                    total_b = _num(total) or _num(est)
                    frac = min((_num(got) or 0) / total_b, 1.0) if total_b else 0.0
                    if fid not in weights:
                        weights.setdefault(fid, 1)
                        order.append(fid)
                    done_parts[fid] = frac
                    whole = sum(weights.values()) or 1
                    pct = sum(weights[k] * done_parts.get(k, 0) for k in order) / whole * 100
                    job.state, job.label = "downloading", "Downloading"
                    job.percent = round(min(pct, 100), 1)
                    job.speed, job.eta, job.total = _num(speed), _num(eta), total_b
                elif line.startswith("VDPP "):
                    _, status, name = (line.split(" ", 2) + ["", ""])[:3]
                    if status == "started":
                        job.state, job.percent, job.speed, job.eta = "converting", None, None, None
                        job.label = {"Merger": "Merging video and audio…",
                                     "ExtractAudio": f"Converting to {job.audio_format.upper()}…"}.get(
                                         name, "Finishing…")
                elif line.startswith("VDFILE "):
                    final_path = Path(line[7:])
            code = job.proc.wait()
            errf.seek(0)
            err = errf.read().decode("utf-8", "replace")
    except Exception as e:  # cookie export or process start failed
        code, err = 1, str(e)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if job.cancel.is_set():
        cleanup(job)
        job.state = "canceled"
        return
    if code != 0 or not final_path or not final_path.exists():
        cleanup(job)
        return fail(job, classify(err or "yt-dlp didn't produce a file", job.url))

    try:
        path = final_path
        if job.mode == "video":
            path = ensure_quicktime(job, path)
            if path is None:
                cleanup(job)
                return
        meta = probe(path)
        dest = unique_output(folder, path.stem, path.suffix.lstrip("."))
        try:
            os.replace(path, dest)        # onto the name just reserved
        except OSError:
            dest.unlink(missing_ok=True)  # drop the empty placeholder
            raise
        path = dest
    except Exception as e:
        cleanup(job)
        return fail(job, error_payload("convert", str(e)))
    cleanup(job)

    v, a = meta["video"], meta["audio"]
    result = {
        "path": str(path), "name": path.name, "folder": str(path.parent),
        "folder_name": path.parent.name,
        "kind": job.mode, "ext": path.suffix.lstrip(".").upper(),
        "vcodec": v.get("codec_name") if v else None,
        "pix_fmt": v.get("pix_fmt") if v else None,
        "acodec": a.get("codec_name") if a else None,
        "height": short_side(v) if v else None,
        "size": meta["size"], "reencoded": job.reencoded,
        "title": info.get("title") or path.stem,
        "thumbnail": _thumb(info), "at": int(time.time()),
    }
    job.result = result
    job.file = path
    add_recent(result)
    job.state, job.label, job.percent = "done", "Done", 100


def fail(job, err):
    job.error = err
    job.state = "error"


def _thumb(data):
    t = data.get("thumbnail")
    return t if isinstance(t, str) and t.startswith(("http://", "https://")) else None


def ensure_quicktime(job, path):
    """Probe the file; re-encode to H.264/AAC MP4 if QuickTime can't play it."""
    meta = probe(path)
    if plays_in_quicktime(path, meta):
        return path

    job.reencoded = True
    job.state, job.label, job.percent = "converting", "Converting for QuickTime…", 0
    job.speed = job.eta = None

    final = path.with_suffix(".mp4")
    tmp = path.with_name(path.stem + ".converting.mp4")
    cmd = [
        ffmpeg(), "-hide_banner", "-nostdin", "-y", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",       # libx264 + yuv420p needs even sizes
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(tmp),
    ]
    code, err = run_ffmpeg(job, cmd, meta["duration"])
    if job.cancel.is_set():
        tmp.unlink(missing_ok=True)
        cleanup(job)
        job.state = "canceled"
        return None
    if code != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        fail(job, error_payload("convert", err))
        return None
    check = probe(tmp)
    if not plays_in_quicktime(tmp, check):
        tmp.unlink(missing_ok=True)
        fail(job, error_payload("convert", f"Converted file still isn't H.264/AAC: {check}"))
        return None
    os.replace(tmp, final)
    if final != path:
        path.unlink(missing_ok=True)
    return final


def run_ffmpeg(job, cmd, duration):
    """Run ffmpeg with -progress pipe:1 and update job.percent. Returns (exit code, stderr tail)."""
    with tempfile.TemporaryFile() as errf:
        job.proc = _popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True)
        for line in job.proc.stdout:
            if line.startswith("out_time_us=") and duration:
                try:
                    secs = int(line.split("=", 1)[1]) / 1_000_000
                    job.percent = round(max(0.0, min(secs / duration * 100, 99.9)), 1)
                except ValueError:
                    pass
        code = job.proc.wait()
        errf.seek(0)
        return code, errf.read().decode("utf-8", "replace")[-2000:]


def cleanup(job):
    """Delete this job's private folder: partial downloads, fragments and temp files."""
    if job.stage:
        shutil.rmtree(job.stage, ignore_errors=True)


# --- Convert jobs --------------------------------------------------------
CONVERTS = {}
CONVERT_SLOT = threading.Semaphore(1)          # one file at a time
CONVERT_FORMATS = {"mp3", "m4a", "wav"}


def unique_output(folder, stem, ext):
    """Reserve '<stem>.<ext>' in folder, or '<stem> (1).<ext>', '(2)'… Never replaces a file."""
    n = 0
    while n < 1000:
        name = f"{stem}.{ext}" if n == 0 else f"{stem} ({n}).{ext}"
        p = folder / name
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            os.close(fd)
            return p
        except FileExistsError:
            n += 1
    raise RuntimeError("Too many files with this name")


class ConvertJob:
    def __init__(self, src, fmt, quality):
        self.id = uuid.uuid4().hex[:12]
        self.src = src
        self.fmt = fmt
        self.quality = quality
        self.state = "waiting"      # waiting, converting, done, error, canceled
        self.percent = None
        self.out = None
        self.error = None
        self.cancel = threading.Event()
        self.proc = None
        self.size = os.path.getsize(src)

    def public(self):
        state, out = self.state, self.out
        done = state == "done" and out is not None
        try:
            out_size = out.stat().st_size if done else None
        except OSError:
            out_size = None
        return {
            "id": self.id, "state": state, "percent": self.percent,
            "name": self.src.name, "size": self.size,
            "out_name": out.name if done else None,
            "out_size": out_size,
            "renamed": bool(done and out.name != f"{self.src.stem}.{self.fmt}"),
            "error": self.error,
        }


def convert_codec_args(fmt, quality):
    if fmt == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", f"{quality}k", "-id3v2_version", "3"]
    if fmt == "m4a":
        return ["-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"]
    return ["-c:a", "pcm_s16le"]


def run_convert(job):
    with CONVERT_SLOT:
        if job.cancel.is_set():
            job.state = "canceled"
            return
        job.state, job.percent = "converting", 0
        try:
            meta = probe(job.src)
        except Exception as e:
            job.state, job.error = "error", {"code": "unreadable", "message": "This file can't be read.",
                                             "detail": str(e)[-500:]}
            return
        if not meta["audio"]:
            job.state, job.error = "error", {"code": "no_audio",
                                             "message": "This file has no audio track, so there's nothing to convert."}
            return
        folder = job.src.parent
        try:
            job.out = unique_output(folder, job.src.stem, job.fmt)
        except OSError as e:
            job.state, job.error = "error", {"code": "write", "message": "Can't save next to this file.",
                                             "detail": str(e)}
            return
        tmp = folder / f".{job.out.stem}.vdconvert.{job.fmt}"
        cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-i", str(job.src),
               "-map", "0:a:0", "-vn", "-map_metadata", "0",
               *convert_codec_args(job.fmt, job.quality),
               "-progress", "pipe:1", "-nostats", str(tmp)]
        code, err = run_ffmpeg(job, cmd, meta["duration"])
        if job.cancel.is_set() or code != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            job.out.unlink(missing_ok=True)
            if job.cancel.is_set():
                job.state = "canceled"
            else:
                job.state, job.error = "error", {"code": "convert", "message": "ffmpeg couldn't convert this file.",
                                                 "detail": err}
            return
        os.replace(tmp, job.out)   # onto the name we reserved
        job.state, job.percent = "done", 100


def valid_source(p):
    """An existing regular file given as an absolute path."""
    if not isinstance(p, str) or not p or len(p) > 4096 or "\x00" in p or not os.path.isabs(p):
        return None
    real = Path(os.path.realpath(p))
    try:
        st = os.stat(real)
    except OSError:
        return None
    return real if stat.S_ISREG(st.st_mode) else None


# --- Phone mode ----------------------------------------------------------
PHONE_COOKIE = "vd_phone"


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))          # no packet is sent; picks the outgoing interface
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    addr = ipaddress.ip_address(ip)
    return ip if addr.is_private and not addr.is_loopback else None


class Phone:
    FAIL_WINDOW = 600       # seconds
    FAIL_PER_IP = 5
    FAIL_TOTAL = 20         # then the PIN changes

    def __init__(self):
        self.lock = threading.Lock()
        self.server = None
        self.ip = None
        self.pin = None
        self.sessions = set()
        self.fails = {}
        self.fail_total = 0

    @property
    def on(self):
        return self.server is not None

    @property
    def host(self):
        return f"{self.ip}:{PORT}"

    def new_pin(self):
        self.pin = f"{secrets.randbelow(10 ** 6):06d}"
        self.sessions.clear()
        self.fails.clear()
        self.fail_total = 0

    def start(self):
        from werkzeug.serving import make_server
        with self.lock:
            if self.server:
                return
            ip = lan_ip()
            if not ip:
                raise RuntimeError("This Mac isn't on a Wi-Fi or local network.")

            def lan_wsgi(environ, start_response):
                environ["vd.lan"] = True
                return app(environ, start_response)

            server = make_server(ip, PORT, lan_wsgi, threaded=True)
            self.ip = ip
            self.new_pin()
            self.server = server
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop(self):
        with self.lock:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
            self.server = None
            self.pin = None
            self.sessions.clear()

    def locked_out(self, ip):
        now = time.time()
        q = self.fails.get(ip)
        while q and now - q[0] > self.FAIL_WINDOW:
            q.popleft()
        return bool(q) and len(q) >= self.FAIL_PER_IP

    def login(self, ip, pin):
        with self.lock:
            if not self.on:
                return "off"
            if self.locked_out(ip):
                return "locked"
            if isinstance(pin, str) and hmac.compare_digest(re.sub(r"\D", "", pin).encode(), self.pin.encode()):
                token = secrets.token_urlsafe(32)
                self.sessions.add(token)
                return token
            self.fails.setdefault(ip, deque()).append(time.time())
            self.fail_total += 1
            if self.fail_total >= self.FAIL_TOTAL:
                self.new_pin()
            return "wrong"

    def valid(self, token):
        if not token:
            return False
        with self.lock:
            return any(hmac.compare_digest(token.encode(), s.encode()) for s in self.sessions)


PHONE = Phone()


def qr_svg(text):
    try:
        import io
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(text, error="m", micro=False).save(
        buf, kind="svg", border=0, dark="#000", xmldecl=False, svgns=True, nl=False, omitsize=True)
    return buf.getvalue().decode().replace('stroke="#000"', 'stroke="currentColor"').replace(
        'class="segno"', f'class="qr" role="img" aria-label="QR code for {text}"')


# --- Flask app -----------------------------------------------------------
app = Flask(__name__, static_folder=None)

# Endpoints a phone may use. Everything else is Mac-only.
PHONE_PUBLIC = {"index", "static_files", "manifest", "phone_login", "phone_session"}
PHONE_ALLOWED = {"env", "info", "download", "job_status", "job_cancel", "recent", "file_download"}


@app.before_request
def guard():
    origin = request.headers.get("Origin")
    g.phone = bool(request.environ.get("vd.lan"))
    if g.phone:
        if not PHONE.on or request.host != PHONE.host:
            abort(403)
        if origin and origin != f"http://{PHONE.host}":
            abort(403)
        if request.method == "POST" and not request.is_json:
            abort(415)
        if request.endpoint in PHONE_PUBLIC:
            return None
        if request.endpoint not in PHONE_ALLOWED:
            abort(403)
        if not PHONE.valid(request.cookies.get(PHONE_COOKIE)):
            return jsonify({"error": {"code": "pin", "message": "Enter the PIN from your Mac."}}), 401
        return None

    if request.host not in ALLOWED_HOSTS:
        abort(403)
    if (request.endpoint or "").startswith("ext_"):
        # Chrome extension: chrome-extension:// origin (or none) plus the pairing token.
        if origin and not origin.startswith("chrome-extension://"):
            abort(403)
        sent = request.headers.get("X-VD-Token", "")
        if not hmac.compare_digest(sent.encode(), extension_token().encode()):
            return jsonify({"error": {"code": "token", "message": "Paste the token from the app's Settings."}}), 401
    elif origin and origin not in ALLOWED_ORIGINS:
        abort(403)            # other websites, and the extension outside /api/ext
    if request.method == "POST" and not request.is_json:
        abort(415)
    return None


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(415)
def http_error(e):
    return jsonify({"error": {"code": "http", "title": e.name, "message": e.description}}), e.code


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC, name)


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(STATIC, "manifest.webmanifest", mimetype="application/manifest+json")


@app.get("/api/env")
def env():
    ff, fp, yt = ffmpeg(), ffprobe(), ytdlp_cmd()
    return jsonify({
        "version": APP_VERSION,
        "ytdlp": ytdlp_version() if yt else None,
        "ffmpeg": tool_version([ff]) if ff else None,
        "ffprobe": tool_version([fp]) if fp else None,
        "ok": bool(ff and fp and yt),
        "phone": g.phone,
    })


@app.post("/api/info")
def info():
    url = (request.get_json(silent=True) or {}).get("url", "")
    if not valid_url(url):
        return jsonify({"error": error_payload("bad_url")}), 400
    settings = load_data()["settings"]
    try:
        data = ytdlp_json(url.strip(), settings["cookies"])
    except YtdlpError as e:
        return jsonify({"error": classify(str(e), url)}), 422
    if data.get("_type") in ("playlist", "multi_video") and not data.get("formats"):
        return jsonify({"error": error_payload("playlist")}), 422

    sizes, audio_sizes = estimate_sizes(data)
    existing = find_existing(data.get("id"))
    if existing:
        existing = {k: v for k, v in existing.items() if k != "path"}   # revealed by video id
    return jsonify({
        "id": data.get("id"),
        "title": data.get("title") or "Untitled",
        "uploader": data.get("uploader") or data.get("channel") or data.get("uploader_id"),
        "duration": data.get("duration"),
        "thumbnail": _thumb(data),
        "site": urlparse(data.get("webpage_url") or url).netloc.removeprefix("www."),
        "heights": [s["height"] for s in sizes],
        "sizes": sizes,
        "audio_sizes": audio_sizes,
        "has_video": bool(sizes),
        "existing": existing,
        "defaults": {"height": settings["height"], "audio_format": settings["audio_format"]},
    })


def parse_download(body, use_defaults=False):
    url = body.get("url", "")
    if not valid_url(url):
        return None, (jsonify({"error": error_payload("bad_url")}), 400)
    mode = body.get("mode")
    if mode not in ("video", "audio"):
        abort(400)
    settings = load_data()["settings"]
    height = settings["height"] if use_defaults else body.get("height", "best")
    if height != "best" and not (isinstance(height, int) and 100 <= height <= 4320):
        abort(400)
    audio_format = settings["audio_format"] if use_defaults else body.get("audio_format", "mp3")
    if audio_format not in AUDIO_EXT:
        abort(400)
    return Job(url.strip(), mode, BEST_CAP if height == "best" else height, audio_format), (height, audio_format)


def start_job(job):
    JOBS[job.id] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()


@app.post("/api/download")
def download():
    job, extra = parse_download(request.get_json(silent=True) or {})
    if job is None:
        return extra
    height, audio_format = extra
    # The last choice becomes the default for the extension and the next download.
    if job.mode == "video":
        update_settings(height=height)
    else:
        update_settings(audio_format=audio_format)
    start_job(job)
    return jsonify(job.public(g.phone))


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id) or abort(404)
    return jsonify(job.public(g.phone))


@app.post("/api/jobs/<job_id>/cancel")
def job_cancel(job_id):
    job = JOBS.get(job_id) or abort(404)
    job.cancel.set()
    _kill(job.proc)
    return jsonify({"ok": True})


@app.get("/api/settings")
def get_settings():
    return jsonify(load_data()["settings"])


@app.post("/api/settings")
def set_settings():
    body = request.get_json(silent=True) or {}
    changes = {}
    if "folder" in body:
        folder = body["folder"]
        if not isinstance(folder, str) or not folder.strip():
            return jsonify({"error": {"message": "Enter a folder path."}}), 400
        if not os.path.isabs(os.path.expanduser(folder.strip())):
            return jsonify({"error": {"message": "Use a full path, like ~/Downloads/Video Downloader."}}), 400
        changes["folder"] = folder.strip()
    if "cookies" in body:
        if body["cookies"] not in BROWSERS:
            abort(400)
        changes["cookies"] = body["cookies"]
    return jsonify(update_settings(**changes))


@app.post("/api/choose-folder")
def choose_folder():
    path = osascript_choose('choose folder with prompt "Choose where downloads are saved"')
    if path:
        update_settings(folder=path.rstrip("/").replace(os.path.expanduser("~"), "~", 1))
    return jsonify(load_data()["settings"])


@app.get("/api/recent")
def recent():
    items = load_data()["recent"]
    out = []
    for i, r in enumerate(items):
        r = dict(r, index=i, exists=os.path.exists(r.get("path", "")))
        if g.phone:
            r.pop("path", None)
            r.pop("folder", None)
        out.append(r)
    return jsonify(out)


def resolve_file(body):
    """Only files this app made: a job id, an index into recent, a convert job, or a found video id."""
    path = None
    if "job_id" in body:
        job = JOBS.get(str(body["job_id"]))
        path = str(job.file) if job and job.file else None
    elif isinstance(body.get("recent"), int):
        items = load_data()["recent"]
        i = body["recent"]
        path = items[i]["path"] if 0 <= i < len(items) else None
    elif "convert_id" in body:
        cj = CONVERTS.get(str(body["convert_id"]))
        path = str(cj.out) if cj and cj.out and cj.state == "done" else None
    elif "existing" in body:
        found = find_existing(str(body["existing"]))
        path = found["path"] if found else None
    return path if path and os.path.isfile(path) else None


@app.post("/api/reveal")
def reveal():
    path = resolve_file(request.get_json(silent=True) or {})
    if not path:
        return jsonify({"error": {"message": "The file was moved or deleted."}}), 404
    subprocess.run(["/usr/bin/open", "-R", path], check=False)
    return jsonify({"ok": True})


@app.post("/api/open")
def open_file():
    path = resolve_file(request.get_json(silent=True) or {})
    if not path:
        return jsonify({"error": {"message": "The file was moved or deleted."}}), 404
    subprocess.run(["/usr/bin/open", path], check=False)
    return jsonify({"ok": True})


@app.get("/api/file")
def file_download():
    """Stream a finished file (used by "Save to this device" on the phone)."""
    body = {}
    if request.args.get("job"):
        body["job_id"] = request.args["job"]
    elif request.args.get("recent", "").isdigit():
        body["recent"] = int(request.args["recent"])
    path = resolve_file(body)
    if not path:
        return jsonify({"error": {"message": "The file was moved or deleted."}}), 404
    return send_file(path, as_attachment=True, download_name=Path(path).name)


@app.post("/api/clipboard")
def clipboard():
    """The clipboard, but only when it's a single web link. Asked for when the window gets focus."""
    try:
        text = subprocess.run(["/usr/bin/pbpaste"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        text = ""
    return jsonify({"url": text if valid_url(text) else None})


# --- Convert -------------------------------------------------------------
def osascript_choose(script):
    """Native macOS chooser via AppleScript. Returns POSIX path(s) or None if canceled."""
    full = f'set r to {script}\n'
    if "multiple selections" in script:
        full += 'set out to ""\nrepeat with f in r\nset out to out & POSIX path of f & linefeed\nend repeat\nreturn out'
    else:
        full += "return POSIX path of r"
    try:
        p = subprocess.run(["/usr/bin/osascript", "-e", full], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


@app.post("/api/choose-files")
def choose_files():
    out = osascript_choose('choose file with prompt "Choose video or audio files to convert" '
                           'with multiple selections allowed')
    return jsonify({"paths": [p for p in (out or "").splitlines() if p]})


@app.post("/api/convert")
def convert():
    body = request.get_json(silent=True) or {}
    fmt = body.get("format", "mp3")
    quality = body.get("quality", 192)
    if fmt not in CONVERT_FORMATS or quality not in (192, 320):
        abort(400)
    paths = body.get("paths")
    if not isinstance(paths, list) or not 1 <= len(paths) <= 50:
        abort(400)
    if not ffmpeg() or not ffprobe():
        return jsonify({"error": error_payload("tools_missing")}), 500
    jobs, rejected = [], []
    for p in paths:
        src = valid_source(p)
        if not src:
            rejected.append({"name": os.path.basename(str(p))[:200],
                             "message": "Not a file, or it no longer exists."})
            continue
        job = ConvertJob(src, fmt, quality)
        CONVERTS[job.id] = job
        jobs.append(job)
        threading.Thread(target=run_convert, args=(job,), daemon=True).start()
    return jsonify({"jobs": [j.public() for j in jobs], "rejected": rejected})


@app.get("/api/convert/<job_id>")
def convert_status(job_id):
    job = CONVERTS.get(job_id) or abort(404)
    return jsonify(job.public())


@app.post("/api/convert/<job_id>/cancel")
def convert_cancel(job_id):
    job = CONVERTS.get(job_id) or abort(404)
    job.cancel.set()
    _kill(job.proc)
    return jsonify({"ok": True})


# --- yt-dlp update (replaces the binary in Application Support) ---------
_update_lock = threading.Lock()


@app.post("/api/update-ytdlp")
def update_ytdlp():
    if any(j.state in ("fetching", "downloading", "converting") for j in JOBS.values()):
        return jsonify({"error": {"message": "Wait for the current download to finish."}}), 409
    if not _update_lock.acquire(blocking=False):
        return jsonify({"error": {"message": "An update is already running."}}), 409
    try:
        before = ytdlp_version()
        USER_BIN.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="vd-update-", dir=USER_BIN))
        try:
            binf, sums = work / "yt-dlp_macos", work / "SHA2-256SUMS"
            for name, dest in (("yt-dlp_macos", binf), ("SHA2-256SUMS", sums)):
                r = subprocess.run(["/usr/bin/curl", "-fsSL", "--proto", "=https", "--max-time", "300",
                                    "-o", str(dest), YTDLP_RELEASE + name],
                                   capture_output=True, text=True, timeout=320)
                if r.returncode != 0:
                    raise RuntimeError(f"Download failed: {r.stderr.strip()}")
            want = next((ln.split()[0] for ln in sums.read_text().splitlines()
                         if ln.split()[-1:] == ["yt-dlp_macos"]), None)
            got = subprocess.run(["/usr/bin/shasum", "-a", "256", str(binf)],
                                 capture_output=True, text=True).stdout.split()[:1]
            if not want or got != [want]:
                raise RuntimeError("The checksum didn't match. Nothing was changed.")
            os.chmod(binf, 0o755)
            v = subprocess.run([str(binf), "--version"], capture_output=True, text=True, timeout=120)
            if v.returncode != 0 or not v.stdout.strip():
                raise RuntimeError("The new yt-dlp didn't start. Nothing was changed.")
            os.replace(binf, USER_BIN / "yt-dlp")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        _version_cache.clear()
        return jsonify({"before": before, "after": ytdlp_version()})
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        return jsonify({"error": {"message": "Update failed. Check your internet connection.",
                                  "detail": str(e)}}), 500
    finally:
        _update_lock.release()


# --- Chrome extension ------------------------------------------------------
@app.get("/api/extension-token")
def get_extension_token():
    return jsonify({"token": extension_token(), "port": PORT})


@app.get("/api/ext/ping")
def ext_ping():
    s = load_data()["settings"]
    return jsonify({"ok": True, "version": APP_VERSION,
                    "defaults": {"height": s["height"], "audio_format": s["audio_format"]}})


@app.post("/api/ext/download")
def ext_download():
    job, extra = parse_download(request.get_json(silent=True) or {}, use_defaults=True)
    if job is None:
        return extra
    start_job(job)
    return jsonify({**job.public(), "folder_name": download_folder().name})


@app.get("/api/ext/jobs/<job_id>")
def ext_job(job_id):
    job = JOBS.get(job_id) or abort(404)
    return jsonify({k: v for k, v in job.public(phone=True).items()})


# --- Phone mode ----------------------------------------------------------
def phone_state():
    if not PHONE.on:
        return {"on": False}
    url = f"http://{PHONE.host}"
    return {"on": True, "url": url, "pin": PHONE.pin, "qr": qr_svg(url)}


@app.get("/api/phone")
def phone_get():
    return jsonify(phone_state())


@app.post("/api/phone")
def phone_set():
    on = (request.get_json(silent=True) or {}).get("on")
    if not isinstance(on, bool):
        abort(400)
    try:
        PHONE.start() if on else PHONE.stop()
    except (RuntimeError, OSError) as e:
        return jsonify({"error": {"message": str(e) if isinstance(e, RuntimeError)
                                  else "Couldn't listen on the network. Another app may be using port "
                                       f"{PORT}."}}), 409
    return jsonify(phone_state())


@app.get("/api/phone/session")
def phone_session():
    if not g.phone:
        return jsonify({"phone": False})
    return jsonify({"phone": True, "authed": PHONE.valid(request.cookies.get(PHONE_COOKIE))})


@app.post("/api/phone/login")
def phone_login():
    if not g.phone:
        abort(403)
    pin = (request.get_json(silent=True) or {}).get("pin")
    result = PHONE.login(request.remote_addr, pin)
    if result == "locked":
        return jsonify({"error": {"code": "locked",
                                  "message": "Too many wrong PINs. Wait 10 minutes, then try again."}}), 429
    if result in ("wrong", "off"):
        return jsonify({"error": {"code": "pin", "message": "That PIN didn't match. Check the number on your Mac."}}), 401
    resp = jsonify({"ok": True})
    resp.set_cookie(PHONE_COOKIE, result, httponly=True, samesite="Strict")
    return resp


def serve_local():
    """The 127.0.0.1 server, as an object main.py can shut down."""
    from werkzeug.serving import make_server
    return make_server(HOST, PORT, app, threaded=True)


def quiet_logs():
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)


if __name__ == "__main__":
    quiet_logs()
    server = serve_local()
    print(f"{APP_NAME}: http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    finally:
        PHONE.stop()
