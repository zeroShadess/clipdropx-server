from flask import Flask, request, send_file
import yt_dlp
import os
import sys
import subprocess

app = Flask(__name__)

VIDEO_PATH = "video.mp4"

@app.route("/download", methods=["POST"])
def download():
    global VIDEO_PATH

    data = request.json
    url = data.get("url")

    # eski dosyayı sil
    if os.path.exists(VIDEO_PATH):
        os.remove(VIDEO_PATH)

    # 🔥 EN UYUMLU FORMAT (ANDROID FIX)
    ydl_opts = {
        'format': 'bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[ext=mp4]',
        'merge_output_format': 'mp4',
        'outtmpl': 'video.%(ext)s',
        'noplaylist': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        return {"status": "ok"}

    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/file")
def file():
    return send_file(VIDEO_PATH, as_attachment=True)


@app.route("/delete", methods=["POST"])
def delete():
    global VIDEO_PATH

    if os.path.exists(VIDEO_PATH):
        os.remove(VIDEO_PATH)

    return {"status": "deleted"}


@app.route("/shutdown", methods=["POST"])
def shutdown():
    try:
        if os.name == "nt":
            subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
        elif sys.platform.startswith("linux"):
            subprocess.run(["shutdown", "-h", "now"], check=True)
        elif sys.platform.startswith("darwin"):
            subprocess.run(["sudo", "shutdown", "-h", "now"], check=True)
        else:
            return {"error": "Unsupported platform"}, 500

        return {"status": "ok"}
    except Exception as e:
        return {"error": str(e)}, 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)