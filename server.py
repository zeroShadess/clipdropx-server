from flask import Flask, request, jsonify, send_file
from yt_dlp import YoutubeDL
import os
import uuid
import shutil

app = Flask(__name__)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# 🔥 Ana indirme endpointi
@app.route("/download", methods=["POST"])
def download_video():
    try:
        data = request.get_json()
        url = data.get("url")

        if not url:
            return jsonify({"error": "URL missing"}), 400

        file_id = str(uuid.uuid4())
        output_path = f"{DOWNLOAD_DIR}/{file_id}.%(ext)s"

        ydl_opts = {
            "format": "bv*+ba/b",
            "outtmpl": output_path,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "cookiefile": "cookies.txt",  # 🔥 YouTube fix
            "retries": 3,
            "fragment_retries": 3,
            "concurrent_fragment_downloads": 1,
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            filename = ydl.prepare_filename(info)
            final_file = filename.replace(".webm", ".mp4").replace(".mkv", ".mp4")

        return jsonify({
            "status": "success",
            "file_id": file_id,
            "file": final_file
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# 📥 Dosya alma endpointi
@app.route("/file/<file_id>", methods=["GET"])
def get_file(file_id):
    try:
        for f in os.listdir(DOWNLOAD_DIR):
            if file_id in f:
                return send_file(os.path.join(DOWNLOAD_DIR, f), as_attachment=True)

        return jsonify({"error": "file not found"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 🧹 Temizleme endpointi
@app.route("/delete", methods=["POST"])
def delete_files():
    try:
        shutil.rmtree(DOWNLOAD_DIR)
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

        return jsonify({"status": "deleted"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 🟢 Test endpoint
@app.route("/")
def home():
    return "Server running 🚀"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)