"""Video Downloader as a Mac app: a pywebview window around the local Flask server.

Run in development with:  .venv/bin/python main.py
PyInstaller builds the .app from this file (see packaging/).
"""
import atexit
import fcntl
import json
import os
import subprocess
import sys
import threading

import webview
from webview.dom import DOMEventHandler

import app as backend

URL = f"http://{backend.HOST}:{backend.PORT}/"


def alert(title, message):
    """A plain macOS alert, for failures before the window exists."""
    script = f'display alert {json.dumps(title)} message {json.dumps(message)} as critical'
    subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True)


def single_instance():
    """Hold a lock for the life of the process. Returns None if another copy holds it."""
    backend.SUPPORT.mkdir(parents=True, exist_ok=True)
    fh = open(backend.SUPPORT / "app.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


class Api:
    """Called from app.js as window.pywebview.api.*"""

    def __init__(self):
        self.window = None

    def choose_files(self):
        r = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        return list(r or [])

    def choose_folder(self):
        r = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        return r[0] if r else None


_stopped = threading.Event()


def stop_everything(server):
    """Quit means quit: no server, no phone listener, no stray yt-dlp or ffmpeg,
    and no half-downloaded files. Safe to call more than once."""
    if _stopped.is_set():
        return
    _stopped.set()
    for job in list(backend.JOBS.values()) + list(backend.CONVERTS.values()):
        if job.state in ("done", "error", "canceled"):
            continue
        job.cancel.set()
        backend._kill(job.proc)
        if isinstance(job, backend.Job):
            backend.cleanup(job)
        elif job.out is not None:          # convert: hidden temp file and the reserved name
            (job.out.parent / f".{job.out.stem}.vdconvert.{job.fmt}").unlink(missing_ok=True)
            job.out.unlink(missing_ok=True)
    backend.PHONE.stop()
    threading.Thread(target=server.shutdown, daemon=True).start()


def main():
    lock = single_instance()
    if lock is None:
        # Another copy is running. macOS normally brings it forward; nothing else to do.
        return 0

    backend.quiet_logs()
    try:
        server = backend.serve_local()
    except OSError:
        alert("Video Downloader can’t start",
              f"Port {backend.PORT} is in use by another program. Quit it, then open Video Downloader again.")
        return 1
    thread = threading.Thread(target=server.serve_forever, name="server", daemon=True)
    thread.start()

    api = Api()
    window = webview.create_window(
        backend.APP_NAME, URL, js_api=api,
        width=600, height=860, min_size=(440, 620),
    )
    api.window = window

    def on_drop(e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        if paths:
            window.evaluate_js(f"window.vdDropFiles({json.dumps(paths)})")

    def on_loaded():
        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=False)

    window.events.loaded += on_loaded
    # ⌘Q ends the process inside Cocoa without returning from webview.start(), so
    # clean up when the window is closing, with atexit as a backstop.
    window.events.closing += lambda: stop_everything(server)
    atexit.register(stop_everything, server)
    try:
        webview.start(private_mode=False)
    finally:
        stop_everything(server)
        thread.join(timeout=5)
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
