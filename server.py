"""
ClipDropX - FINAL STABLE SERVER
TikTok + IG + Twitter + Reddit + Vimeo ✔
Mobile + Web FULL FIX
"""

from flask import Flask, request, send_file, render_template, jsonify
from flask_cors import CORS
import yt_dlp
import os
import tempfile
import re
import uuid
import time
from urllib.parse import urlparse

app = Flask(__name__)
CORS(app, origins="*")

TEMP_DIR = tempfile.gettempdir()
CLEANUP_AGE = 7200


# ===== VALIDATION =====
def validate_url(url: str) -> bool:
    allowed = [
        "tiktok.com",
        "instagram.com",
        "twitter.com", "x.com",
        "reddit.com",
        "vimeo.com",
        "youtube.com", "youtu.be"
    ]
    if not url.startswith(("http://", "https://")):
        return False
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(domain.endswith(a) for a in allowed)
    except:
        return False


def validate_id(rid: str) -> bool:
    return bool(re.match(r"^[a-f0-9]{8}$", rid))


# ===== UTIL =====
def cleanup():
    try:
        now = time.time()
        for f in os.listdir(TEMP_DIR):
            if f.startswith("clipdropx_"):
                p = os.path.join(TEMP_DIR, f)
                if now - os.path.getmtime(p) > CLEANUP_AGE:
                    os.remove(p)
    except:
        pass


def get_path(rid):
    return os.path.join(TEMP_DIR, f"clipdropx_{rid}.mp4")


# ===== FORMAT FIX =====
def get_format():
    """
    TEK PARÇA MP4 (EN KRİTİK)
    """
    return "best[ext=mp4]/best"


# ===== ROUTES =====
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def download():
    cleanup()

    try:
        data = request.get_json()
        url = data.get("url", "").strip()

        if not validate_url(url):
            return jsonify({"error": "bad url"}), 400

        rid = str(uuid.uuid4())[:8]
        path = get_path(rid)

        ydl_opts = {
            "format": get_format(),

            "outtmpl": path,
            "noplaylist": True,
            "quiet": True,

            # 🔥 EN KRİTİK FIX
            "postprocessors": [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }],

            # 🔥 MOBİL + TT FIX
            "postprocessor_args": [
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k"
            ],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(path):
            return jsonify({"error": "fail"}), 500

        return jsonify({"id": rid})

    except Exception as e:
        print("ERROR:", e)
        return jsonify({"error": "server"}), 500


@app.route("/file/<rid>")
def file(rid):
    if not validate_id(rid):
        return "bad", 400

    path = get_path(rid)

    if not os.path.exists(path):
        return "not found", 404

    # 🔥 MOBİL FIX
    return send_file(
        path,
        mimetype="video/mp4",
        as_attachment=True,
        download_name="video.mp4",
        conditional=False
    )


@app.route("/delete/<rid>", methods=["POST"])
def delete(rid):
    path = get_path(rid)
    try:
        if os.path.exists(path):
            os.remove(path)
    except:
        pass
    return jsonify({"ok": True})


# ===== START =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)