"""
ClipDropX - FULL Stable Server
Render + Web + Mobile Compatible
"""

from flask import Flask, request, send_file, render_template, jsonify
from flask_cors import CORS
import yt_dlp
import os
import base64
import tempfile
import re
import uuid
from urllib.parse import urlparse
import time

app = Flask(__name__)

# ===== CORS =====
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "*"
)
CORS(app, origins=ALLOWED_ORIGINS)

# ===== CONFIG =====
TEMP_DIR = tempfile.gettempdir()
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
CLEANUP_AGE = 7200  # 2 saat

# ===== VALIDATION =====
def validate_url(url: str) -> bool:
    allowed = [
        "youtube.com", "youtu.be",
        "instagram.com",
        "tiktok.com",
        "twitter.com", "x.com",
        "vimeo.com",
        "dailymotion.com"
    ]
    if not url.startswith(("http://", "https://")):
        return False
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(domain.endswith(a) for a in allowed)
    except:
        return False


def validate_quality(q: str) -> bool:
    return q in ["2160", "1080", "720", "480", "best"]


def validate_id(rid: str) -> bool:
    return bool(re.match(r"^[a-f0-9]{8}$", rid))


# ===== UTIL =====
def cleanup_old_files():
    try:
        now = time.time()
        for f in os.listdir(TEMP_DIR):
            if f.startswith("clipdropx_") and f.endswith(".mp4"):
                path = os.path.join(TEMP_DIR, f)
                if now - os.path.getmtime(path) > CLEANUP_AGE:
                    os.remove(path)
    except:
        pass


def get_video_path(rid: str):
    return os.path.join(TEMP_DIR, f"clipdropx_{rid}.mp4")


def get_cookie_file():
    b64 = os.environ.get("COOKIES_B64")
    if not b64:
        return None
    try:
        decoded = base64.b64decode(b64)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="wb")
        tmp.write(decoded)
        tmp.close()
        return tmp.name
    except:
        return None


def get_format_string(q: str):
    if q == "best":
        return "bestvideo+bestaudio/best"

    return (
        f"bestvideo[height<={q}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={q}][ext=mp4]/best"
    )


# ===== ROUTES =====
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/download", methods=["POST"])
def download():
    cleanup_old_files()

    try:
        data = request.get_json() or {}
        url = data.get("url", "").strip()
        quality = data.get("quality", "best")

        if not validate_url(url):
            return jsonify({"error": "Invalid URL"}), 400

        if not validate_quality(quality):
            return jsonify({"error": "Invalid quality"}), 400

        rid = str(uuid.uuid4())[:8]
        video_path = get_video_path(rid)
        temp_path = video_path.replace(".mp4", "")

        cookie_file = get_cookie_file()

        ydl_opts = {
            "format": get_format_string(quality),
            "outtmpl": f"{temp_path}.%(ext)s",
            "merge_output_format": "mp4",

            # 🔥 EN KRİTİK FIX (codec dönüşüm)
            "postprocessors": [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }],

            "postprocessor_args": [
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k"
            ],

            # 🔥 platform fix
            "extractor_args": {
                "twitter": {"api_key": "client"}
            },

            "noplaylist": True,
            "quiet": False,
            "no_warnings": True,
        }

        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            print("DOWNLOAD ERROR:", e)
            return jsonify({"error": "Download failed"}), 500

        if not os.path.exists(video_path):
            return jsonify({"error": "Processing failed"}), 500

        return jsonify({"status": "ok", "id": rid})

    except Exception as e:
        print("SERVER ERROR:", e)
        return jsonify({"error": "Server error"}), 500


@app.route("/file/<rid>")
def file(rid):
    if not validate_id(rid):
        return jsonify({"error": "Invalid id"}), 400

    path = get_video_path(rid)

    if not os.path.exists(path):
        return jsonify({"error": "Not found"}), 404

    return send_file(
        path,
        mimetype="video/mp4",
        as_attachment=True,
        download_name="video.mp4"
    )


@app.route("/delete/<rid>", methods=["POST"])
def delete(rid):
    if not validate_id(rid):
        return jsonify({"error": "Invalid id"}), 400

    path = get_video_path(rid)

    try:
        if os.path.exists(path):
            os.remove(path)
    except:
        pass

    return jsonify({"status": "ok"})


@app.before_request
def cors_fix():
    if request.method == "OPTIONS":
        return "", 200


# ===== START =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)