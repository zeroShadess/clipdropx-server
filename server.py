from flask import Flask, request, send_file
import yt_dlp
import os

app = Flask(__name__)

VIDEO_PATH = "video.mp4"

@app.route("/")
def home():
    return "Server çalışıyor"

@app.route("/download", methods=["POST"])
def download():
    global VIDEO_PATH

    data = request.json
    url = data.get("url")

    if os.path.exists(VIDEO_PATH):
        os.remove(VIDEO_PATH)

    ydl_opts = {
        'format': 'best[ext=mp4]',
        'outtmpl': VIDEO_PATH,
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
    if not os.path.exists(VIDEO_PATH):
        return {"error": "Dosya yok"}, 404
    return send_file(VIDEO_PATH, as_attachment=True)


@app.route("/delete", methods=["POST"])
def delete():
    if os.path.exists(VIDEO_PATH):
        os.remove(VIDEO_PATH)
    return {"status": "deleted"}