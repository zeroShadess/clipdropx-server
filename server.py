from flask import Flask, request, send_file
import yt_dlp
import os
import sys
import subprocess
import base64
import tempfile

app = Flask(__name__)
VIDEO_PATH = "video.mp4"


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
    except Exception as e:
        print(f"Cookie decode hatası: {e}")
        return None


@app.route("/download", methods=["POST"])
def download():
    global VIDEO_PATH

    data = request.json
    url = data.get("url")

    if not url:
        return {"error": "URL eksik"}, 400

    if os.path.exists(VIDEO_PATH):
        os.remove(VIDEO_PATH)

    is_youtube = "youtube.com" in url or "youtu.be" in url

    # YouTube için cookie kullanma, ios client ile çöz
    if is_youtube:
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'outtmpl': 'video.%(ext)s',
            'noplaylist': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios', 'android']
                }
            }
        }
    else:
        cookie_file = get_cookie_file()
        ydl_opts = {
            'format': 'bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[ext=mp4]',
            'merge_output_format': 'mp4',
            'outtmpl': 'video.%(ext)s',
            'noplaylist': True,
        }
        if cookie_file:
            ydl_opts['cookiefile'] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return {"status": "ok"}
    except Exception as e:
        print(f"İndirme hatası: {e}")
        return {"error": str(e)}, 500


@app.route("/file")
def file():
    if not os.path.exists(VIDEO_PATH):
        return {"error": "Dosya bulunamadı"}, 404
    return send_file(VIDEO_PATH, as_attachment=True)


@app.route("/delete", methods=["POST"])
def delete():
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
            return {"error": "Desteklenmeyen platform"}, 500
        return {"status": "ok"}
    except Exception as e:
        return {"error": str(e)}, 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)