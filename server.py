from flask import Flask, request, send_file, render_template, jsonify
from flask_cors import CORS
import yt_dlp
import os
import tempfile
import uuid
import time

app = Flask(__name__)
CORS(app)  # full açık bırak (debug için en sağlam)

TEMP_DIR = tempfile.gettempdir()


# ===== HELPERS =====
def get_video_path(rid):
    return os.path.join(TEMP_DIR, f"clipdropx_{rid}.mp4")


def cleanup():
    now = time.time()
    for f in os.listdir(TEMP_DIR):
        if f.startswith("clipdropx_"):
            path = os.path.join(TEMP_DIR, f)
            if now - os.path.getmtime(path) > 3600:
                try:
                    os.remove(path)
                except:
                    pass


def get_format(quality):
    if quality == "best":
        return "best[ext=mp4]/best"

    return (
        f"best[height<={quality}][ext=mp4]/"
        f"bestvideo[height<={quality}]+bestaudio/"
        f"best"
    )


# ===== ROUTES =====
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/download", methods=["POST"])
def download():
    cleanup()

    try:
        data = request.get_json(force=True)
        url = data.get("url")
        quality = data.get("quality", "best")

        if not url:
            return {"error": "URL yok"}, 400

        rid = str(uuid.uuid4())[:8]
        temp_base = os.path.join(TEMP_DIR, f"clipdropx_{rid}")

        ydl_opts = {
            "format": get_format(quality),
            "outtmpl": temp_base + ".%(ext)s",
            "merge_output_format": "mp4",
            "quiet": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0"
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # 🔥 EXTENSION FIX (en kritik)
        final_path = get_video_path(rid)
        for f in os.listdir(TEMP_DIR):
            if f.startswith(f"clipdropx_{rid}"):
                os.rename(os.path.join(TEMP_DIR, f), final_path)
                break

        if not os.path.exists(final_path):
            return {"error": "Dosya oluşmadı"}, 500

        return {"status": "ok", "id": rid}

    except Exception as e:
        print("HATA:", e)
        return {"error": "Download failed"}, 500


@app.route("/file/<rid>")
def file(rid):
    path = get_video_path(rid)

    if not os.path.exists(path):
        return {"error": "Yok"}, 404

    return send_file(path, as_attachment=True, download_name="video.mp4")


@app.route("/delete/<rid>", methods=["POST"])
def delete(rid):
    path = get_video_path(rid)
    try:
        if os.path.exists(path):
            os.remove(path)
    except:
        pass
    return {"status": "ok"}


# ===== RUN =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)