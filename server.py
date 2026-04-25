"""
ClipDropX - Production Server (Platform-Aware Quality Fix)
"""

import os
import re
import time
import glob
import uuid
import json
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import (
    Flask, request, jsonify, Response,
    stream_with_context, send_from_directory, after_this_request
)
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins="*")

PORT          = int(os.environ.get("PORT", 5000))
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE_MB", 500)) * 1024 * 1024
CLEANUP_HOURS = int(os.environ.get("CLEANUP_HOURS", 2))
TEMP_DIR      = tempfile.gettempdir()
CHUNK_SIZE    = 512 * 1024

progress_store: dict = {}
store_lock = threading.Lock()

ALLOWED_DOMAINS = [
    "youtube.com", "youtu.be",
    "tiktok.com",
    "instagram.com",
    "twitter.com", "x.com",
    "reddit.com",
    "vimeo.com",
    "dailymotion.com",
    "facebook.com",
    "twitch.tv",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_valid_url(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
        return any(netloc == d or netloc.endswith("." + d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False

def is_valid_id(rid: str) -> bool:
    return bool(re.match(r"^[a-f0-9]{8}$", rid))

def detect_platform(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return "generic"
    if "tiktok.com" in netloc or "vm.tiktok.com" in netloc or "vt.tiktok.com" in netloc:
        return "tiktok"
    if "instagram.com" in netloc:
        return "instagram"
    if "youtube.com" in netloc or "youtu.be" in netloc:
        return "youtube"
    if "twitter.com" in netloc or "x.com" in netloc:
        return "twitter"
    if "reddit.com" in netloc:
        return "reddit"
    if "vimeo.com" in netloc:
        return "vimeo"
    return "generic"

def get_height(quality: str) -> int:
    q = quality.lower().strip()
    mapping = {"4k": 2160, "2160": 2160, "1080": 1080, "720": 720, "480": 480, "360": 360}
    return mapping.get(q, 1080)

def build_format_opts(platform: str, quality: str) -> dict:
    """
    Platform-aware format selection with broad fallback chains.
    Platforms that don't support advanced selectors (TikTok, Reddit, Twitter)
    always use format_sort instead, so they never throw "format not available".
    """
    q = quality.lower().strip()
    h = get_height(quality)

    # ── YouTube ──────────────────────────────────────────────────────────────
    if platform == "youtube":
        if q == "best":
            return {
                "format": "bestvideo+bestaudio/best",
                "format_sort": ["res", "br", "fps"],
            }
        fmt = (
            f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={h}]+bestaudio/"
            f"best[height<={h}]/"
            f"best"
        )
        return {
            "format": fmt,
            "format_sort": [f"res:{h}", "br", "fps"],
        }

    # ── Instagram ────────────────────────────────────────────────────────────
    if platform == "instagram":
        if q == "best":
            return {
                "format": "bestvideo+bestaudio/best",
                "format_sort": ["res", "br", "fps"],
            }
        fmt = (
            f"bestvideo[height<={h}]+bestaudio/"
            f"best[height<={h}]/"
            f"best"
        )
        return {
            "format": fmt,
            "format_sort": [f"res:{h}", "br", "fps"],
        }

    # ── Reddit ───────────────────────────────────────────────────────────────
    # v.redd.it separates video and audio as DASH streams.
    # MUST use bestvideo+bestaudio or the file will have no audio.
    if platform == "reddit":
        if q == "best":
            fmt = "bestvideo+bestaudio/best"
        else:
            fmt = (
                f"bestvideo[height<={h}]+bestaudio/"
                f"bestvideo+bestaudio/"
                f"best[height<={h}]/"
                f"best"
            )
        return {
            "format": fmt,
            "format_sort": ["res" if q == "best" else f"res:{h}", "br", "fps"],
        }

    # ── TikTok, Twitter/X, Vimeo, generic ────────────────────────────────────
    # These don't reliably support advanced selectors — use format_sort instead.
    sort_res = "res" if q == "best" else f"res:{h}"
    return {
        "format": "best",
        "format_sort": [sort_res, "br", "fps"],
    }

def find_output_file(file_id: str):
    pattern = os.path.join(TEMP_DIR, f"clipdropx_{file_id}.*")
    matches = [
        Path(f) for f in glob.glob(pattern)
        if not f.endswith(".part") and not f.endswith(".ytdl")
    ]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_size)

def cleanup_old_files():
    try:
        now = time.time()
        for f in Path(TEMP_DIR).glob("clipdropx_*"):
            try:
                if now - f.stat().st_mtime > CLEANUP_HOURS * 3600:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass

def make_progress_hook(file_id: str):
    def hook(d: dict):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
            downloaded = d.get("downloaded_bytes", 0)
            pct = min(round((downloaded / total) * 100, 1), 99)
            with store_lock:
                progress_store[file_id].update({
                    "percent": pct,
                    "speed":   d.get("speed") or 0,
                    "eta":     d.get("eta") or 0,
                    "status":  "downloading",
                })
        elif d["status"] == "finished":
            with store_lock:
                progress_store[file_id].update({
                    "percent": 99,
                    "status":  "processing",
                })
    return hook

# ---------------------------------------------------------------------------
# Download thread
# ---------------------------------------------------------------------------

def download_thread(url: str, file_id: str, quality: str):
    outtmpl  = os.path.join(TEMP_DIR, f"clipdropx_{file_id}.%(ext)s")
    platform = detect_platform(url)
    fmt_opts = build_format_opts(platform, quality)

    print(f"[INFO] {file_id} | platform={platform} | quality={quality} | format={fmt_opts['format']}")

    ydl_opts = {
        "format":        fmt_opts["format"],
        "format_sort":   fmt_opts.get("format_sort", ["res", "br", "fps"]),
        "outtmpl":       outtmpl,
        "noplaylist":    True,
        "quiet":         False,
        "no_warnings":   False,
        "retries":       5,
        "fragment_retries": 5,
        "socket_timeout":   30,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [make_progress_hook(file_id)],
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        output_file = None
        for _ in range(20):
            output_file = find_output_file(file_id)
            if output_file is not None:
                break
            time.sleep(0.5)

        if output_file is None:
            raise FileNotFoundError(f"Dosya diskte bulunamadi: clipdropx_{file_id}.*")

        file_size = output_file.stat().st_size

        if file_size > MAX_FILE_SIZE:
            output_file.unlink(missing_ok=True)
            raise ValueError(f"Dosya cok buyuk: {file_size // (1024*1024)} MB")

        with store_lock:
            progress_store[file_id].update({
                "percent":   100,
                "status":    "complete",
                "file_path": str(output_file),
                "file_size": file_size,
            })

        print(f"[OK] {file_id} -> {output_file.name} ({file_size // 1024} KB)")

    except Exception as e:
        for leftover in glob.glob(os.path.join(TEMP_DIR, f"clipdropx_{file_id}.*")):
            try:
                Path(leftover).unlink(missing_ok=True)
            except Exception:
                pass
        friendly = _friendly_error(str(e), quality)
        with store_lock:
            progress_store[file_id] = {"status": "error", "error": friendly}
        print(f"[ERROR] {file_id}: {e}")


def _friendly_error(raw: str, quality: str) -> str:
    """Converts raw yt-dlp errors into readable Turkish messages."""
    r = raw.lower()
    if "requested format is not available" in r or "format is not available" in r:
        q_label = quality if quality.lower() != "best" else "en iyi"
        return (
            f"Bu video '{q_label}' kalitesini desteklemiyor. "
            "Lütfen farklı bir kalite seçin."
        )
    if "private video" in r or ("private" in r and "video" in r):
        return "Bu video gizli (private). İndirilemiyor."
    if "login" in r or "sign in" in r or "authentication" in r:
        return "Bu içerik giriş gerektiriyor. İndirilemiyor."
    if "copyright" in r:
        return "Bu video telif hakkı nedeniyle kullanılamıyor."
    if "geo" in r or "not available in your country" in r:
        return "Bu video bulunduğunuz ülkede kullanılamıyor."
    if "unable to extract" in r or "no video formats found" in r:
        return "Video bilgisi alınamadı. Bağlantıyı kontrol edin veya daha sonra tekrar deneyin."
    if "urlopen error" in r or "connection" in r or "network" in r:
        return "Ağ bağlantı hatası. Lütfen tekrar deneyin."
    if "404" in r:
        return "Video bulunamadı (404). Bağlantı geçersiz ya da video silinmiş olabilir."
    if "too large" in r or "cok buyuk" in r:
        return raw  # already Turkish from our own code
    return "İndirme başarısız. Bağlantıyı veya kalite seçimini kontrol edin."


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    try:
        return send_from_directory(".", "index.html")
    except Exception:
        return jsonify({"service": "ClipDropX API", "status": "running"})

@app.route("/health")
def health():
    cleanup_old_files()
    return jsonify({"status": "healthy", "temp_dir": TEMP_DIR})

@app.route("/download", methods=["POST"])
def start_download():
    cleanup_old_files()
    data = request.get_json(silent=True)
    if not data or "url" not in data:
        return jsonify({"error": "url alani gerekli"}), 400
    url = str(data["url"]).strip()
    if not url:
        return jsonify({"error": "url bos olamaz"}), 400
    if not is_valid_url(url):
        return jsonify({"error": "Desteklenmeyen URL"}), 400
    quality = str(data.get("quality", "1080")).strip()
    file_id = uuid.uuid4().hex[:8]
    with store_lock:
        progress_store[file_id] = {"percent": 0, "status": "starting"}
    t = threading.Thread(target=download_thread, args=(url, file_id, quality), daemon=True)
    t.start()
    return jsonify({"id": file_id})

@app.route("/progress/<file_id>")
def progress_stream(file_id):
    if not is_valid_id(file_id):
        return jsonify({"error": "Gecersiz ID"}), 400

    def generate():
        last_payload = None
        timeout_at = time.time() + 600
        while time.time() < timeout_at:
            with store_lock:
                prog = dict(progress_store.get(file_id, {"status": "starting", "percent": 0}))
            status = prog.get("status", "starting")
            payload = {
                "percent": prog.get("percent", 0),
                "status":  status,
                "speed":   prog.get("speed", 0),
                "eta":     prog.get("eta", 0),
            }
            if status == "complete":
                payload["percent"] = 100
                yield f"data: {json.dumps(payload)}\n\n"
                break
            elif status == "error":
                payload["error"] = prog.get("error", "Bilinmeyen hata")
                yield f"data: {json.dumps(payload)}\n\n"
                break
            else:
                if payload != last_payload:
                    yield f"data: {json.dumps(payload)}\n\n"
                    last_payload = payload
                time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.route("/file/<file_id>")
def serve_file(file_id):
    if not is_valid_id(file_id):
        return jsonify({"error": "Gecersiz ID"}), 400

    deadline = time.time() + 60
    while time.time() < deadline:
        with store_lock:
            prog = dict(progress_store.get(file_id, {}))
        status = prog.get("status")
        if status == "complete":
            break
        elif status == "error":
            return jsonify({"error": prog.get("error", "Indirme basarisiz")}), 500
        elif not status:
            return jsonify({"error": "Session bulunamadi"}), 404
        time.sleep(0.5)
    else:
        return jsonify({"error": "Zaman asimi"}), 504

    with store_lock:
        fp_str = progress_store.get(file_id, {}).get("file_path")

    fp = Path(fp_str) if fp_str else find_output_file(file_id)

    if fp is None or not fp.exists():
        return jsonify({"error": "Dosya bulunamadi"}), 404

    file_size = fp.stat().st_size
    filename  = f"clipdropx_{file_id}.mp4"

    @after_this_request
    def delete_after_send(response):
        def _cleanup():
            time.sleep(3)
            try:
                fp.unlink(missing_ok=True)
            except Exception:
                pass
            with store_lock:
                progress_store.pop(file_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()
        return response

    def generate():
        try:
            with open(fp, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
        except Exception as e:
            print(f"[STREAM ERROR] {file_id}: {e}")

    return Response(
        generate(),
        mimetype="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length":      str(file_size),
            "Accept-Ranges":       "bytes",
        }
    )

@app.route("/delete/<file_id>", methods=["POST", "DELETE"])
def delete_file(file_id):
    if not is_valid_id(file_id):
        return jsonify({"error": "Gecersiz ID"}), 400
    deleted = []
    for leftover in glob.glob(os.path.join(TEMP_DIR, f"clipdropx_{file_id}.*")):
        try:
            Path(leftover).unlink(missing_ok=True)
            deleted.append(leftover)
        except Exception:
            pass
    with store_lock:
        progress_store.pop(file_id, None)
    return jsonify({"success": True, "deleted": deleted})

@app.route("/robots.txt")
def robots():
    return "User-agent: *\nAllow: /\n", 200, {"Content-Type": "text/plain"}

@app.route("/sitemap.xml")
def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://clipdropx-server.onrender.com/</loc><priority>1.0</priority></url>
</urlset>"""
    return xml, 200, {"Content-Type": "application/xml"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)