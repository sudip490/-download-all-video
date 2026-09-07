"""Local video downloader: paste a link, pick a quality, get the file."""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import yt_dlp
from yt_dlp.extractor import gen_extractor_classes
from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.serving import make_server

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
COOKIES_FILE = BASE_DIR / "cookies.txt"
DOWNLOAD_DIR.mkdir(exist_ok=True)
JOB_TTL_SECONDS = 60 * 60  # finished downloads are deleted after one hour
BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi", "safari")
YTDLP_PACKAGE = "yt-dlp[default,curl-cffi,secretstorage]"

app = Flask(__name__)
SITES = sorted({ie.IE_NAME.split(":")[0] for ie in gen_extractor_classes()
                if ie.IE_NAME != "generic"}, key=str.lower)
jobs = {}
jobs_lock = threading.Lock()
update_lock = threading.Lock()
server = None
restart_requested = threading.Event()


class QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def valid_url(url):
    return isinstance(url, str) and re.match(r"^https?://\S+$", url.strip()) is not None


def clean_error(err):
    text = str(err).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    text = re.sub(r"^\[[^\]]+\]\s*[^:]*:\s*", "", text)  # drop "[site] id:" prefix
    return text or "Something went wrong."


def human_size(n):
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def format_selector(choice):
    if choice == "audio":
        return "bestaudio/best"
    if choice.isdigit():
        h = int(choice)
        return (f"bestvideo*[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo*[height<={h}]+bestaudio/best[height<={h}]/best")
    return "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/bestvideo*+bestaudio/best"


def cookie_opts(source):
    """source is '' (no login), 'file' (cookies.txt next to app.py) or a browser name."""
    if not source:
        return {}
    if source == "file":
        if not COOKIES_FILE.is_file():
            raise ValueError("cookies.txt was not found next to app.py. "
                             "Export it from your browser first (see README).")
        return {"cookiefile": str(COOKIES_FILE)}
    if source in BROWSERS:
        return {"cookiesfrombrowser": (source,)}
    raise ValueError("Unknown cookie source.")


def base_opts(source):
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": QuietLogger()}
    opts.update(cookie_opts(source))
    return opts


def update(job_id, **fields):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def run_download(job_id, job_dir, url, choice, source):
    def hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = round(done / total * 100, 1) if total else 0
            speed = d.get("speed")
            eta = d.get("eta")
            msg = f"Downloading… {pct}% of {human_size(total)}"
            if speed:
                msg += f" · {human_size(speed)}/s"
            if eta:
                msg += f" · {int(eta)}s left"
            update(job_id, status="downloading", progress=pct, message=msg)
        elif status == "finished":
            update(job_id, status="processing", progress=100, message="Processing with ffmpeg…")

    opts = base_opts(source)
    opts.update({
        "format": format_selector(choice),
        "outtmpl": str(job_dir / "%(title).100B.%(ext)s"),
        "progress_hooks": [hook],
        "windowsfilenames": True,
    })
    if choice == "audio":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        files = [p for p in job_dir.iterdir()
                 if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
        if not files:
            raise RuntimeError("Download finished but no file was produced.")
        final = max(files, key=lambda p: p.stat().st_size)
        update(job_id, status="done", progress=100, message="Ready", file=str(final))
    except Exception as e:  # noqa: BLE001
        update(job_id, status="error", message=clean_error(e))


def cleanup_loop():
    while True:
        time.sleep(300)
        cutoff = time.time() - JOB_TTL_SECONDS
        with jobs_lock:
            expired = [j for j, v in jobs.items()
                       if v["created"] < cutoff and v["status"] in ("done", "error")]
            for j in expired:
                jobs.pop(j, None)
        for j in expired:
            shutil.rmtree(DOWNLOAD_DIR / j, ignore_errors=True)


def installed_ytdlp_version():
    """Ask a fresh interpreter, so we see the version on disk even after a pip upgrade."""
    out = subprocess.run([sys.executable, "-c", "import yt_dlp; print(yt_dlp.version.__version__)"],
                         capture_output=True, text=True, timeout=60)
    return out.stdout.strip() or "unknown"


def restart_server():
    """Stop the serving loop; the main thread then closes the socket and re-executes app.py."""
    restart_requested.set()
    threading.Thread(target=server.shutdown, daemon=True).start()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/sites")
def sites():
    return render_template("sites.html", sites=SITES, count=len(SITES))


@app.get("/api/settings")
def api_settings():
    return jsonify(
        ytdlp_version=yt_dlp.version.__version__,
        cookies_file=COOKIES_FILE.is_file(),
        browsers=list(BROWSERS),
        sites=len(SITES),
    )


@app.post("/api/update")
def api_update():
    if not update_lock.acquire(blocking=False):
        return jsonify(error="An update is already running."), 409
    try:
        before = yt_dlp.version.__version__
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", YTDLP_PACKAGE],
            capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            return jsonify(error="Update failed: " + (detail[-1] if detail else "pip error")), 500
        after = installed_ytdlp_version()
    except subprocess.TimeoutExpired:
        return jsonify(error="Update timed out. Check your internet connection."), 500
    finally:
        update_lock.release()
    restarting = after != before
    if restarting:
        threading.Timer(1.0, restart_server).start()
    return jsonify(before=before, after=after, restarting=restarting)


@app.post("/api/info")
def api_info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    source = str(data.get("cookies") or "")
    if not valid_url(url):
        return jsonify(error="Please enter a valid http(s) link."), 400
    try:
        opts = base_opts(source)
        opts["skip_download"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:  # noqa: BLE001
        return jsonify(error=clean_error(e)), 400
    if info.get("_type") == "playlist" and info.get("entries"):
        info = next((e for e in info["entries"] if e), info)
    heights = sorted({f.get("height") for f in info.get("formats", [])
                      if f.get("height") and f.get("vcodec") not in (None, "none")},
                     reverse=True)
    return jsonify(
        title=info.get("title"),
        thumbnail=info.get("thumbnail"),
        duration=info.get("duration"),
        uploader=info.get("uploader") or info.get("channel"),
        site=info.get("extractor_key"),
        heights=heights,
        url=info.get("webpage_url") or url,
    )


@app.post("/api/download")
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    choice = str(data.get("format") or "best")
    source = str(data.get("cookies") or "")
    if not valid_url(url):
        return jsonify(error="Please enter a valid http(s) link."), 400
    if not (choice in ("best", "audio") or choice.isdigit()):
        return jsonify(error="Unknown format."), 400
    try:
        cookie_opts(source)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    job_id = uuid.uuid4().hex
    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir()
    with jobs_lock:
        jobs[job_id] = {"status": "starting", "progress": 0, "message": "Starting…",
                        "file": None, "created": time.time()}
    threading.Thread(target=run_download, args=(job_id, job_dir, url, choice, source),
                     daemon=True).start()
    return jsonify(job_id=job_id)


def get_job(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return None
    with jobs_lock:
        return dict(jobs[job_id]) if job_id in jobs else None


@app.get("/api/progress/<job_id>")
def api_progress(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify(error="Unknown download."), 404
    return jsonify(status=job["status"], progress=job["progress"], message=job["message"],
                   filename=Path(job["file"]).name if job["file"] else None)


@app.get("/api/file/<job_id>")
def api_file(job_id):
    job = get_job(job_id)
    if not job or job["status"] != "done" or not job["file"]:
        abort(404)
    path = Path(job["file"]).resolve()
    if not path.is_file() or DOWNLOAD_DIR.resolve() not in path.parents:
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


if __name__ == "__main__":
    for leftover in DOWNLOAD_DIR.iterdir():
        if leftover.is_dir():
            shutil.rmtree(leftover, ignore_errors=True)
    threading.Thread(target=cleanup_loop, daemon=True).start()
    print(f"Video downloader running at http://127.0.0.1:5000  (yt-dlp {yt_dlp.version.__version__})",
          flush=True)
    server = make_server("127.0.0.1", 5000, app, threaded=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    if restart_requested.is_set():
        print("Restarting to load the new yt-dlp…", flush=True)
        os.chdir(BASE_DIR)
        os.execv(sys.executable, [sys.executable, str(BASE_DIR / "app.py")])
