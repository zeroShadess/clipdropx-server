from flask import Flask, request, send_file
import yt_dlp
import os

app = Flask(__name__)

VIDEO_PATH = "video.mp4"


@app.route("/")
def home():
    return "ClipDropX Server Aktif"


@app.route("/download", methods=["POST"])
def download():
    global VIDEO_PATH

    data = request.json
    url = data.get("url")

    if not url:
        return {"error": "URL yok"}, 400

    # 🔥 ENV'den cookies al
    cookies_data = os.environ.get("COOKIES")

    if cookies_data:
        with open("cookies.txt", "w", encoding="utf-8") as f:
            f.write(cookies_data)

    # eski video sil
    if os.path.exists(VIDEO_PATH):
        os.remove(VIDEO_PATH)

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4',
        'outtmpl': VIDEO_PATH,
        'noplaylist': True,

        'http_headers': {
            'User-Agent': 'Mozilla/5.0',
            'Accept-Language': 'en-US,en;q=0.9'
        },

        'cookiefile': 'cookies.txt',

        'nocheckcertificate': True,
        'ignoreerrors': False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        return {"status": "ok"}

    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/file")
def file():
    if os.path.exists(VIDEO_PATH):
        return send_file(VIDEO_PATH, as_attachment=True)
    return {"error": "Video yok"}, 404


@app.route("/delete", methods=["POST"])
def delete():
    if os.path.exists(VIDEO_PATH):
        os.remove(VIDEO_PATH)
    return {"status": "deleted"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)