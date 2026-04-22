from flask import Flask, request, send_file, jsonify
import yt_dlp
import os

app = Flask(__name__)

VIDEO_PATH = "video.mp4"

# 🔐 Render ENV → cookies.txt oluştur
cookies_data = os.environ.get("COOKIES")

if cookies_data:
    with open("cookies.txt", "w", encoding="utf-8") as f:
        f.write(cookies_data)


@app.route("/")
def home():
    return "ClipDropX Server Aktif"


@app.route("/download", methods=["POST"])
def download():
    global VIDEO_PATH

    data = request.json
    url = data.get("url")

    if not url:
        return jsonify({"error": "URL yok"}), 400

    # eski dosyayı sil
    if os.path.exists(VIDEO_PATH):
        os.remove(VIDEO_PATH)

    ydl_opts = {
        # 🔥 MP4 garanti
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4',

        'outtmpl': VIDEO_PATH,
        'noplaylist': True,

        # 🔥 bot bypass
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        },

        # 🔐 cookies
        'cookiefile': 'cookies.txt',

        # 🔥 hata olursa devam et
        'quiet': False,
        'no_warnings': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(VIDEO_PATH):
            return jsonify({"error": "Video indirilemedi"}), 500

        return jsonify({"status": "ok"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/file", methods=["GET"])
def file():
    if not os.path.exists(VIDEO_PATH):
        return jsonify({"error": "Dosya yok"}), 404

    return send_file(VIDEO_PATH, as_attachment=True)


@app.route("/delete", methods=["POST"])
def delete():
    if os.path.exists(VIDEO_PATH):
        os.remove(VIDEO_PATH)

    return jsonify({"status": "deleted"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)