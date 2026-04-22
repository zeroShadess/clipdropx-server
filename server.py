from flask import Flask, request, send_file, jsonify
from yt_dlp import YoutubeDL
import os

app = Flask(__name__)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

@app.route("/download", methods=["POST"])
def download_video():
    try:
        url = request.json["url"]

        ydl_opts = {
            "format": "bv*+ba/b",
            "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        return jsonify({
            "status": "ok",
            "file": filename
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route("/file")
def get_file():
    files = os.listdir(DOWNLOAD_DIR)
    if not files:
        return "No file", 404

    latest = sorted(files)[-1]
    return send_file(os.path.join(DOWNLOAD_DIR, latest), as_attachment=True)


@app.route("/")
def home():
    return "Server running"

if __name__ == "__main__":
    app.run()