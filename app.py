"""Local video downloader: paste a link, pick options, get the file(s)."""
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path

import yt_dlp
from yt_dlp.extractor import gen_extractor_classes
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import download_range_func
from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from werkzeug.serving import make_server

# If launched from inside a Flatpak app (for example VS Code's terminal), XDG_CONFIG_HOME points at the
# sandbox's config folder and browser cookies would not be found. Point it back at the real one.
if "/.var/app/" in os.environ.get("XDG_CONFIG_HOME", ""):
    os.environ["XDG_CONFIG_HOME"] = str(Path.home() / ".config")

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
COOKIES_FILE = BASE_DIR / "cookies.txt"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# ---- hosting configuration (environment variables) ----
# PUBLIC_MODE=1   safe defaults for a server on the internet: password required, browser cookies,
#                 save-folder and self-update disabled, size/time/concurrency limits applied.
# APP_PASSWORD    when set, every page and API call needs this password (any username).
# HOST / PORT     where to listen. PORT is set automatically by hosts like Render.
def env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

PUBLIC_MODE = env_flag("PUBLIC_MODE")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_MINUTES", "30" if PUBLIC_MODE else "60")) * 60
MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", "2048" if PUBLIC_MODE else "0"))  # 0 = no limit
MAX_JOBS = int(os.environ.get("MAX_JOBS", "2" if PUBLIC_MODE else "0"))  # running at once, 0 = no limit
MAX_PLAYLIST = int(os.environ.get("MAX_PLAYLIST", "50" if PUBLIC_MODE else "300"))
BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi", "safari")
YTDLP_PACKAGE = "yt-dlp[default,curl-cffi,secretstorage]"
CONTAINERS = ("mp4", "mkv", "webm", "original")
AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav", "original")
SPONSOR_CATEGORIES = ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "music_offtopic"]
THUMB_OK = {"mp3", "mkv", "mka", "ogg", "opus", "flac", "m4a", "mp4", "m4v", "mov"}
SKIP_SUFFIXES = (".part", ".ytdl", ".webp", ".jpg", ".jpeg", ".png", ".json", ".zip", ".temp")
MAX_BATCH = 50
MAX_HISTORY = 50
SAVE_ROOTS = (Path.home(), Path("/media"), Path("/mnt"), Path("/run/media"))
PP_NAMES = {
    "FFmpegMerger": "Merging video and audio",
    "FFmpegExtractAudio": "Converting audio",
    "FFmpegVideoRemuxer": "Changing container",
    "FFmpegSubtitlesConvertor": "Converting subtitles",
    "FFmpegEmbedSubtitle": "Embedding subtitles",
    "SponsorBlock": "Checking SponsorBlock",
    "ModifyChapters": "Removing sponsor segments",
    "FFmpegMetadata": "Writing metadata",
    "EmbedThumbnail": "Embedding thumbnail",
    "FFmpegSplitChapters": "Splitting chapters",
    "MoveFiles": "Finishing",
}

app = Flask(__name__)
SITES = sorted({ie.IE_NAME.split(":")[0] for ie in gen_extractor_classes()
                if ie.IE_NAME != "generic"}, key=str.lower)
jobs = {}
jobs_lock = threading.Lock()
update_lock = threading.Lock()
zip_lock = threading.Lock()
server = None
restart_requested = threading.Event()


class QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


# ---------- small helpers ----------

def valid_url(url):
    return isinstance(url, str) and re.match(r"^https?://\S+$", url.strip()) is not None


def split_urls(text):
    urls = re.findall(r"https?://\S+", text)
    if not urls and re.fullmatch(r"[\w.-]+\.[a-z]{2,}(/\S*)?", text):
        urls = ["https://" + text]  # a bare domain like youtube.com/watch?v=...
    return urls


def clean_error(err):
    text = str(err).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    text = re.sub(r"^\[[^\]]+\]\s*[^:]*:\s*", "", text)  # drop "[site] id:" prefix
    if "confirm you" in text and "not a bot" in text:
        where = "this server" if PUBLIC_MODE else "this computer"
        return (f"YouTube is blocking {where} as a bot. In Settings choose 'Paste cookies.txt text' and paste "
                "cookies exported from a browser where you are signed in to YouTube. "
                + ("YouTube blocks most cloud servers, so running the app on your own computer is the reliable fix."
                   if PUBLIC_MODE else "Or pick your browser under 'Login cookies'."))
    if "Requested format is not available" in text:
        return ("No downloadable stream was available for that choice. Try 'Best quality' or 'Audio only'. "
                + ("On a hosted server YouTube often hides all streams from the server's address even when "
                   "logged in; the app on your own computer does not have this problem." if PUBLIC_MODE
                   else "If the site lists no qualities at all, it may be blocking downloads."))
    if "Unable to extract course id" in text:
        return ("Udemy did not show the course page. On a hosted server this means Udemy's protection is "
                "blocking the server address; use the app on your own computer for Udemy. It also happens when "
                "the account is not logged in or not enrolled in the course.") if PUBLIC_MODE else (
                "Udemy did not show the course page. Check that you are logged in (pick your browser under "
                "'Login cookies') and that the course is in your account.")
    return text or "Something went wrong."


def human_size(n):
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", str(name or "")).strip()[:100] or "download"


def short_codec(codec):
    if not codec or codec == "none":
        return None
    c = codec.lower()
    for prefix, name in (("avc", "h264"), ("h264", "h264"), ("hev", "h265"), ("hvc", "h265"), ("vp09", "vp9"),
                         ("vp9", "vp9"), ("av01", "av1"), ("mp4a", "aac"), ("opus", "opus"), ("vorbis", "vorbis")):
        if c.startswith(prefix):
            return name
    return c.split(".")[0]


def parse_time(value):
    s = str(value or "").strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) > 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError("Clip times must look like 90, 1:30 or 0:01:30.")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def validate_save_dir(value):
    s = str(value or "").strip()
    if not s:
        return None
    if PUBLIC_MODE:
        raise ValueError("Saving to a folder is disabled on a hosted server.")
    p = Path(s).expanduser()
    if not p.is_absolute():
        raise ValueError("Save folder must be a full path, for example /home/you/Videos.")
    p = p.resolve()
    if not any(p == root or root in p.parents for root in SAVE_ROOTS):
        raise ValueError("Save folder must be inside your home folder or a mounted drive.")
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------- yt-dlp option builders ----------

MAX_COOKIE_TEXT = 256 * 1024


def json_cookies_to_netscape(text):
    """Browser extensions often export cookies as JSON. Convert that to the Netscape cookies.txt format."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        data = data["cookies"]
    if not isinstance(data, list):
        return None
    lines = ["# Netscape HTTP Cookie File"]
    for c in data:
        if not isinstance(c, dict) or not c.get("name") or not c.get("domain"):
            continue
        domain = str(c["domain"])
        if c.get("hostOnly") is False and not domain.startswith("."):
            domain = "." + domain
        try:
            expiry = int(float(c.get("expirationDate") or c.get("expires") or 0))
        except (TypeError, ValueError):
            expiry = 0
        lines.append("\t".join([
            ("#HttpOnly_" if c.get("httpOnly") else "") + domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            str(c.get("path") or "/"),
            "TRUE" if c.get("secure") else "FALSE",
            str(expiry),
            str(c["name"]),
            str(c.get("value", "")),
        ]))
    return "\n".join(lines) if len(lines) > 1 else None


def cookie_opts(source, text="", work_dir=None):
    """source is '' (no login), 'file' (cookies.txt next to app.py), 'paste' (cookies.txt text sent
    from the page, written into work_dir) or a browser name."""
    if not source:
        return {}
    if source == "file":
        if not COOKIES_FILE.is_file():
            raise ValueError("cookies.txt was not found next to app.py. "
                             "Export it from your browser first (see README).")
        return {"cookiefile": str(COOKIES_FILE)}
    if source == "paste":
        text = str(text or "").strip()
        if not text:
            raise ValueError("Paste the contents of your cookies.txt file in Settings first.")
        if len(text) > MAX_COOKIE_TEXT:
            raise ValueError("The pasted cookies text is too large.")
        if text[0] in "[{":
            text = json_cookies_to_netscape(text) or text
        looks_ok = text.startswith("# Netscape HTTP Cookie File") or text.startswith("# HTTP Cookie File") or any(
            len(line.split("\t")) == 7 for line in text.splitlines() if line and not line.startswith("#"))
        if not looks_ok:
            raise ValueError("That does not look like a cookies.txt file. Export it with a "
                             "'Get cookies.txt LOCALLY' browser extension and paste the whole file.")
        if work_dir is None:
            raise ValueError("Internal error: no work folder for cookies.")
        path = Path(work_dir) / "_cookies.txt"
        path.write_text(text + "\n")
        return {"cookiefile": str(path)}
    if source in BROWSERS:
        if PUBLIC_MODE:
            raise ValueError("Browser cookies are not available on a hosted server. Use a cookies.txt file.")
        return {"cookiesfrombrowser": (source,)}
    raise ValueError("Unknown cookie source.")


def connection_opts(data, work_dir=None):
    """Options shared by every yt-dlp call: cookies, proxy, impersonation, speed."""
    opts = {"quiet": True, "no_warnings": True, "logger": QuietLogger(),
            "concurrent_fragment_downloads": 4, "geo_bypass": True}
    opts.update(cookie_opts(str(data.get("cookies") or ""), data.get("cookies_text"), work_dir))
    proxy = str(data.get("proxy") or "").strip()
    if proxy:
        if not re.match(r"^(https?|socks[45]h?)://\S+$", proxy):
            raise ValueError("Proxy must start with http://, https:// or socks5://")
        opts["proxy"] = proxy
    if data.get("impersonate"):
        opts["impersonate"] = ImpersonateTarget.from_str("chrome")
    if PUBLIC_MODE:
        # A server cannot produce YouTube's "PO token", and yt-dlp then drops every stream that wants one.
        # Keep those streams anyway; with a pasted login they often still download.
        opts["extractor_args"] = {"youtube": {"formats": ["missing_pot"]}}
    return opts


def build_format(fmt, kind, needs_audio, container):
    if fmt.startswith("id:"):
        fid = fmt[3:]
        if not re.fullmatch(r"[A-Za-z0-9_.=+\-]+", fid):
            raise ValueError("Bad format id.")
        if needs_audio:
            pref = f"{fid}+bestaudio[ext=m4a]/" if container == "mp4" else ""
            return pref + f"{fid}+bestaudio/{fid}/best"
        return f"{fid}/best"
    if kind == "audio":
        return "bestaudio/best"
    vext, aext = {"mp4": ("mp4", "m4a"), "webm": ("webm", "webm")}.get(container, (None, None))
    if fmt.isdigit():
        h = int(fmt)
        pref = f"bestvideo*[height<={h}][ext={vext}]+bestaudio[ext={aext}]/" if vext else ""
        return pref + f"bestvideo*[height<={h}]+bestaudio/best[height<={h}]/best"
    pref = f"bestvideo*[ext={vext}]+bestaudio[ext={aext}]/" if vext else ""
    return pref + "bestvideo*+bestaudio/best"


def ydl_options(job_dir, base, o, playlist):
    """Turn the options chosen in the page into yt-dlp parameters. Raises ValueError on bad input."""
    kind = "audio" if o.get("kind") == "audio" else "video"
    container = o.get("container") if o.get("container") in CONTAINERS else "mp4"
    audio_format = o.get("audio_format") if o.get("audio_format") in AUDIO_FORMATS else "mp3"
    opts = dict(base)
    opts.update({
        "format": build_format(str(o.get("format") or "best"), kind, bool(o.get("needs_audio")), container),
        "outtmpl": {
            "default": str(job_dir / "%(playlist_index&{} - |)s%(title).100B.%(ext)s"),
            "chapter": str(job_dir / "%(title).80B - %(section_number)02d %(section_title).60B.%(ext)s"),
        },
        "windowsfilenames": True,
        "noplaylist": not playlist,
        "ignoreerrors": "only_download" if playlist else False,
    })
    if MAX_FILESIZE_MB > 0:
        opts["max_filesize"] = MAX_FILESIZE_MB * 1024 * 1024
    if playlist:
        items = re.sub(r"\s", "", str(o.get("playlist_items") or ""))
        if items:
            if not re.fullmatch(r"[0-9,\-:]+", items):
                raise ValueError("Bad playlist selection.")
            opts["playlist_items"] = items
        else:
            opts["playlistend"] = MAX_PLAYLIST

    pps = []
    lang = str(o.get("subs_lang") or "").strip()
    if lang:
        if not re.fullmatch(r"[A-Za-z0-9._\-]+", lang):
            raise ValueError("Bad subtitle language.")
        opts.update({"writesubtitles": True, "writeautomaticsub": bool(o.get("subs_auto")),
                     "subtitleslangs": [lang], "subtitlesformat": "srt/best"})
        pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"})

    sponsor = bool(o.get("sponsorblock"))
    if sponsor:
        pps.append({"key": "SponsorBlock", "categories": SPONSOR_CATEGORIES, "when": "after_filter"})

    final_ext = None
    if kind == "audio":
        if audio_format != "original":
            pps.append({"key": "FFmpegExtractAudio", "preferredcodec": audio_format, "preferredquality": "0"})
            final_ext = audio_format
    else:
        if container != "original":
            opts["merge_output_format"] = container
            pps.append({"key": "FFmpegVideoRemuxer", "preferedformat": container})
            final_ext = container
        if lang and o.get("subs_mode") == "embed":
            pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})

    if sponsor:
        pps.append({"key": "ModifyChapters", "remove_sponsor_segments": SPONSOR_CATEGORIES, "force_keyframes": False})

    if o.get("embed", True):
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True, "add_infojson": False})
        if final_ext in THUMB_OK:
            opts["writethumbnail"] = True
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

    if kind == "video" and o.get("split_chapters"):
        pps.append({"key": "FFmpegSplitChapters", "force_keyframes": False})

    start, end = parse_time(o.get("clip_start")), parse_time(o.get("clip_end"))
    if start is not None or end is not None:
        s = start or 0.0
        e = end if end is not None else float("inf")
        if e <= s:
            raise ValueError("Clip end must be later than clip start.")
        opts["download_ranges"] = download_range_func(None, [(s, e)])
        opts["force_keyframes_at_cuts"] = True

    opts["postprocessors"] = pps
    return opts


# ---------- info payloads ----------

def thumb_of(e):
    thumbs = e.get("thumbnails") or []
    return e.get("thumbnail") or (thumbs[-1].get("url") if thumbs else None)


def entries_of(info):
    out = []
    for i, e in enumerate([e for e in (info.get("entries") or []) if e], 1):
        out.append({
            "index": e.get("playlist_index") or i,
            "title": e.get("title") or e.get("id") or f"Item {i}",
            "duration": e.get("duration"),
            "thumbnail": thumb_of(e),
            "url": e.get("webpage_url") or e.get("url"),
            "uploader": e.get("uploader") or e.get("channel"),
        })
    return out


def video_payload(info, url):
    formats = []
    for f in info.get("formats") or []:
        has_v = f.get("vcodec") not in (None, "none")
        has_a = f.get("acodec") not in (None, "none")
        if (not has_v and not has_a) or f.get("ext") == "mhtml" or not f.get("format_id"):
            continue
        formats.append({
            "id": f["format_id"], "ext": f.get("ext"), "height": f.get("height"), "fps": f.get("fps"),
            "vcodec": short_codec(f.get("vcodec")) if has_v else None,
            "acodec": short_codec(f.get("acodec")) if has_a else None,
            "size": f.get("filesize") or f.get("filesize_approx"), "tbr": f.get("tbr"),
            "note": f.get("format_note"), "video": has_v, "audio": has_a,
        })
    formats.sort(key=lambda f: (f["height"] or 0, f["tbr"] or 0), reverse=True)
    heights = sorted({f["height"] for f in formats if f["height"] and f["video"]}, reverse=True)
    if not formats:
        raise ValueError("The site returned no downloadable streams for this video. "
                         + ("YouTube does this for cloud server addresses even when logged in; "
                            "use the app on your own computer for that video." if PUBLIC_MODE
                            else "The video may be blocked, private, or still processing."))

    def sub_name(lang, entries):
        return (entries[0].get("name") if entries else None) or lang
    subs = [{"lang": k, "name": sub_name(k, v), "auto": False} for k, v in (info.get("subtitles") or {}).items()]
    manual = {s["lang"] for s in subs}
    subs += [{"lang": k, "name": sub_name(k, v), "auto": True}
             for k, v in (info.get("automatic_captions") or {}).items() if k not in manual]
    subs.sort(key=lambda s: (s["auto"], s["lang"]))
    return {
        "type": "video",
        "title": info.get("title"), "thumbnail": thumb_of(info), "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"), "site": info.get("extractor_key"),
        "url": info.get("webpage_url") or url, "heights": heights, "formats": formats, "subs": subs,
        "chapters": len(info.get("chapters") or []),
        "in_playlist": bool(re.search(r"[?&]list=", url)) and info.get("extractor_key") == "Youtube",
    }


# ---------- job runner ----------

def update(job_id, **fields):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def collect_files(job_dir):
    return sorted(p for p in job_dir.iterdir()
                  if p.is_file() and not p.name.startswith("_")
                  and not p.name.lower().endswith(SKIP_SUFFIXES))


def run_job(job_id, job_dir, urls, base, o, playlist):
    state = {"item": 0, "total": None if playlist else len(urls), "failed": 0}

    def prefix():
        item, total = state["item"], state["total"]
        return f"Item {item} of {total} · " if total and total > 1 and item else ""

    def overall(pct):
        item, total = state["item"], state["total"]
        if total and item:
            return round(((item - 1) + pct / 100) / total * 100, 1)
        return pct

    def hook(d):
        status = d.get("status")
        info = d.get("info_dict") or {}
        if playlist:
            if info.get("playlist_autonumber"):
                state["item"] = info["playlist_autonumber"]
            if info.get("n_entries"):
                state["total"] = info["n_entries"]
        if status == "downloading":
            tb = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = round(done / tb * 100, 1) if tb else 0
            msg = f"Downloading… {pct}% of {human_size(tb)}"
            if d.get("speed"):
                msg += f" · {human_size(d['speed'])}/s"
            if d.get("eta"):
                msg += f" · {int(d['eta'])}s left"
            update(job_id, status="downloading", progress=overall(pct), message=prefix() + msg,
                   item=state["item"], total=state["total"])
        elif status == "finished":
            update(job_id, status="processing", progress=overall(100), message=prefix() + "Processing…",
                   item=state["item"], total=state["total"])
        elif status == "error":
            state["failed"] += 1

    def pp_hook(d):
        if d.get("status") in ("started", "processing"):
            name = PP_NAMES.get(d.get("postprocessor"), d.get("postprocessor") or "Processing")
            update(job_id, status="processing", message=f"{prefix()}{name}…")

    try:
        opts = ydl_options(job_dir, base, o, playlist)
        opts["progress_hooks"] = [hook]
        opts["postprocessor_hooks"] = [pp_hook]
        with yt_dlp.YoutubeDL(opts) as ydl:
            if playlist:
                ydl.download(urls)
            else:
                for i, u in enumerate(urls, 1):
                    state["item"] = i
                    update(job_id, item=i, total=state["total"], message=prefix() + "Starting…")
                    try:
                        ydl.download([u])
                    except Exception:
                        if len(urls) == 1:
                            raise
                        state["failed"] += 1
        files = collect_files(job_dir)
        if not files:
            extra = f" {state['failed']} item(s) failed." if state["failed"] else ""
            if MAX_FILESIZE_MB > 0:
                extra += f" Files above {MAX_FILESIZE_MB} MB are skipped on this server; try a lower quality."
            raise RuntimeError("Download finished but no file was produced." + extra)
        save_dir = validate_save_dir(o.get("save_dir"))
        if save_dir:
            for f in files:
                shutil.copy2(f, save_dir / f.name)
        msg = "Ready"
        if state["failed"]:
            msg += f" · {state['failed']} item(s) failed"
        if save_dir:
            msg += f" · copy saved to {save_dir}"
        update(job_id, status="done", progress=100, message=msg, files=[str(f) for f in files],
               item=state["item"], total=state["total"])
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


def get_job(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return None
    with jobs_lock:
        return dict(jobs[job_id]) if job_id in jobs else None


def file_list(job):
    out = []
    for i, f in enumerate(job.get("files") or []):
        p = Path(f)
        out.append({"index": i, "name": p.name, "size": p.stat().st_size if p.is_file() else None})
    return out


# ---------- routes ----------

@app.before_request
def require_password():
    if not APP_PASSWORD or request.path == "/healthz":
        return None
    auth = request.authorization
    if auth and auth.type == "basic" and hmac.compare_digest(auth.password or "", APP_PASSWORD):
        return None
    return Response("Password required.", 401, {"WWW-Authenticate": 'Basic realm="Video Downloader"'})


@app.after_request
def no_cache(resp):
    """Pages and API answers must never be cached, so a new deploy shows up on the next reload."""
    if resp.mimetype in ("text/html", "application/json"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/healthz")
def healthz():
    commit = os.environ.get("RENDER_GIT_COMMIT", "")[:7]  # set by Render, lets us see which build is live
    return f"ok {commit}".strip()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/sites")
def sites():
    return render_template("sites.html", sites=SITES, count=len(SITES))


@app.get("/api/settings")
def api_settings():
    return jsonify(ytdlp_version=yt_dlp.version.__version__, cookies_file=COOKIES_FILE.is_file(),
                   browsers=list(BROWSERS), sites=len(SITES), public=PUBLIC_MODE,
                   max_filesize_mb=MAX_FILESIZE_MB, max_playlist=MAX_PLAYLIST, max_jobs=MAX_JOBS,
                   ttl_minutes=JOB_TTL_SECONDS // 60)


@app.post("/api/update")
def api_update():
    if PUBLIC_MODE:
        return jsonify(error="Self-update is disabled on a hosted server. Redeploy to update yt-dlp."), 403
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
    text = str(data.get("url") or "").strip()
    if not text:
        return jsonify(error="Paste a link or type something to search."), 400
    work_dir = Path(tempfile.mkdtemp(prefix="_info-", dir=DOWNLOAD_DIR))
    try:
        return info_response(data, text, work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def info_response(data, text, work_dir):
    try:
        opts = connection_opts(data, work_dir)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    opts["skip_download"] = True
    urls = split_urls(text)
    if len(urls) > 1:
        return jsonify(type="batch", urls=urls[:MAX_BATCH])
    if urls:
        target = urls[0]
        opts.update({"noplaylist": not data.get("playlist"), "extract_flat": "in_playlist",
                     "playlistend": MAX_PLAYLIST})
    else:
        target = f"ytsearch12:{text}"
        opts["extract_flat"] = "in_playlist"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=clean_error(e)), 400
    if not info:
        return jsonify(error="Nothing found for that link."), 400
    if not urls:
        return jsonify(type="search", query=text, results=entries_of(info))
    if info.get("_type") == "playlist":
        entries = entries_of(info)
        return jsonify(type="playlist", title=info.get("title"),
                       uploader=info.get("uploader") or info.get("channel"), site=info.get("extractor_key"),
                       url=info.get("webpage_url") or target, entries=entries,
                       truncated=len(entries) >= MAX_PLAYLIST)
    try:
        return jsonify(video_payload(info, target))
    except ValueError as e:
        return jsonify(error=str(e)), 400


@app.post("/api/download")
def api_download():
    data = request.get_json(silent=True) or {}
    o = data.get("options") or {}
    urls = data.get("urls")
    if not isinstance(urls, list):
        urls = [data.get("url")]
    urls = [str(u or "").strip() for u in urls][:MAX_BATCH]
    if not urls or not all(valid_url(u) for u in urls):
        return jsonify(error="Please enter a valid http(s) link."), 400
    playlist = bool(data.get("playlist"))
    if playlist and len(urls) != 1:
        return jsonify(error="A playlist download takes exactly one link."), 400
    job_id = uuid.uuid4().hex
    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir()
    try:
        base = connection_opts(data, job_dir)
        validate_save_dir(o.get("save_dir"))
        ydl_options(job_dir, base, o, playlist)  # validate the options up front
    except ValueError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(error=str(e)), 400
    with jobs_lock:
        running = sum(1 for j in jobs.values() if j["status"] not in ("done", "error"))
        if MAX_JOBS and running >= MAX_JOBS:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify(error=f"{running} download(s) already running on this server. "
                                 "Please wait a minute and try again."), 429
        jobs[job_id] = {"status": "starting", "progress": 0, "message": "Starting…", "files": [],
                        "item": 0, "total": None, "created": time.time(),
                        "title": str(o.get("title") or urls[0])[:200]}
    threading.Thread(target=run_job, args=(job_id, job_dir, urls, base, o, playlist), daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/progress/<job_id>")
def api_progress(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify(error="Unknown download."), 404
    return jsonify(status=job["status"], progress=job["progress"], message=job["message"],
                   item=job.get("item"), total=job.get("total"), files=file_list(job))


@app.get("/api/file/<job_id>/<int:index>")
def api_file(job_id, index):
    job = get_job(job_id)
    if not job or job["status"] != "done" or index >= len(job["files"]):
        abort(404)
    path = Path(job["files"][index]).resolve()
    if not path.is_file() or DOWNLOAD_DIR.resolve() not in path.parents:
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.get("/api/zip/<job_id>")
def api_zip(job_id):
    job = get_job(job_id)
    if not job or job["status"] != "done" or not job["files"]:
        abort(404)
    zpath = DOWNLOAD_DIR / job_id / "_all.zip"
    with zip_lock:
        if not zpath.exists():
            tmp = zpath.with_name("_all.zip.part")
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
                for f in job["files"]:
                    if Path(f).is_file():
                        z.write(f, Path(f).name)
            tmp.rename(zpath)
    return send_file(zpath, as_attachment=True, download_name=safe_name(job["title"]) + ".zip")


@app.get("/api/history")
def api_history():
    with jobs_lock:
        done = [dict(v, job_id=k) for k, v in jobs.items() if v["status"] == "done"]
    done.sort(key=lambda j: j["created"], reverse=True)
    return jsonify(items=[{"job_id": j["job_id"], "title": j["title"], "created": j["created"],
                           "files": file_list(j)} for j in done[:MAX_HISTORY]])


if __name__ == "__main__":
    for leftover in DOWNLOAD_DIR.iterdir():
        if leftover.is_dir():
            shutil.rmtree(leftover, ignore_errors=True)
    threading.Thread(target=cleanup_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    mode = "public mode" if PUBLIC_MODE else "local mode"
    lock = "password required" if APP_PASSWORD else "no password"
    print(f"Video downloader running at http://{host}:{port}  (yt-dlp {yt_dlp.version.__version__}, "
          f"{mode}, {lock})", flush=True)
    server = make_server(host, port, app, threaded=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    if restart_requested.is_set():
        print("Restarting to load the new yt-dlp…", flush=True)
        os.chdir(BASE_DIR)
        os.execv(sys.executable, [sys.executable, str(BASE_DIR / "app.py")])
