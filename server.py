"""
ClipDropX - Final Optimized (H.264 priority + Auto-delete after download)
"""

import os
import re
import time
import uuid
import json
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory, after_this_request
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins="*")

# ========== Konfigürasyon ==========
PORT = int(os.environ.get("PORT", 5000))
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", 500))
CLEANUP_HOURS = int(os.environ.get("CLEANUP_HOURS", 2))
TEMP_DIR = tempfile.gettempdir()
CHUNK_SIZE = 1024 * 1024

progress_store = {}

def quality_to_format(quality: str) -> str:
    """
    Kalite seçeneğine göre yt-dlp format string'i.
    Öncelik H.264 (avc1), sonra diğer codec'ler.
    """
    q = quality.lower()
    if q == "2160" or q == "4k":
        video_filter = "bestvideo[height<=2160][ext=mp4]"
    elif q == "1080":
        video_filter = "bestvideo[height<=1080][ext=mp4]"
    elif q == "720":
        video_filter = "bestvideo[height<=720][ext=mp4]"
    elif q == "480":
        video_filter = "bestvideo[height<=480][ext=mp4]"
    else:  # best
        video_filter = "bestvideo[ext=mp4]"
    
    # H.264 öncelikli: video_filter içinde vcodec^=avc1 olan varsa onu al
    return f"{video_filter}[vcodec^=avc1]+bestaudio[ext=m4a]/{video_filter}+bestaudio[ext=m4a]/{video_filter}/best"

def is_valid_url(url: str) -> bool:
    allowed = ["tiktok.com", "instagram.com", "twitter.com", "x.com", "reddit.com", "vimeo.com", "youtube.com", "youtu.be"]
    if not url.startswith(("http://", "https://")):
        return False
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(domain.endswith(d) for d in allowed)
    except:
        return False

def is_valid_id(rid: str) -> bool:
    return bool(re.match(r"^[a-f0-9]{8}$", rid))

def get_file_path(file_id: str) -> Path:
    return Path(TEMP_DIR) / f"clipdropx_{file_id}.mp4"

def cleanup_old_files():
    try:
        now = time.time()
        for f in Path(TEMP_DIR).glob("clipdropx_*.mp4"):
            if now - f.stat().st_mtime > CLEANUP_HOURS * 3600:
                f.unlink()
    except:
        pass

def progress_hook(download_id: str):
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
            downloaded = d.get('downloaded_bytes', 0)
            percent = (downloaded / total) * 100 if total > 0 else 0
            progress_store[download_id] = {
                'percent': round(percent, 1),
                'speed': d.get('speed', 0),
                'eta': d.get('eta', 0),
                'status': 'downloading'
            }
        elif d['status'] == 'finished':
            progress_store[download_id] = {'percent': 100, 'status': 'finished'}
        elif d['status'] == 'error':
            progress_store[download_id] = {'status': 'error', 'error': str(d.get('error', ''))}
    return hook

def download_video_thread(url: str, file_id: str, quality: str):
    output_path = str(get_file_path(file_id))
    format_str = quality_to_format(quality)
    ydl_opts = {
        'format': format_str,
        'outtmpl': output_path,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'concurrent_fragment_downloads': 4,
        'retries': 5,
        'fragment_retries': 5,
        'socket_timeout': 30,
        'progress_hooks': [progress_hook(file_id)],
        # Sadece remux - re-encode yok!
        'postprocessors': [{
            'key': 'FFmpegVideoRemuxer',
            'preferedformat': 'mp4',
        }],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        size = os.path.getsize(output_path)
        max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
        if size > max_bytes:
            os.remove(output_path)
            progress_store[file_id] = {'status': 'error', 'error': f'File exceeds {MAX_FILE_SIZE_MB}MB'}
        else:
            progress_store[file_id]['file_size'] = size
            progress_store[file_id]['status'] = 'complete'
    except Exception as e:
        progress_store[file_id] = {'status': 'error', 'error': str(e)}
        if os.path.exists(output_path):
            os.remove(output_path)

# ========== Endpointler ==========
@app.route("/")
def home():
    try:
        return send_from_directory('.', 'index.html')
    except:
        return jsonify({"service": "ClipDropX API", "status": "running", "endpoints": ["/download", "/progress/<id>", "/file/<id>", "/delete/<id>"]})

@app.route("/health")
def health():
    cleanup_old_files()
    return jsonify({"status": "healthy"})

@app.route("/download", methods=["POST"])
def download():
    cleanup_old_files()
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": "Missing url"}), 400
    url = data["url"].strip()
    if not is_valid_url(url):
        return jsonify({"error": "Unsupported URL"}), 400

    quality = data.get("quality", "1080")
    file_id = uuid.uuid4().hex[:8]
    progress_store[file_id] = {'percent': 0, 'status': 'starting'}
    
    thread = threading.Thread(target=download_video_thread, args=(url, file_id, quality))
    thread.daemon = True
    thread.start()
    
    return jsonify({"id": file_id})

@app.route("/progress/<file_id>")
def progress_stream(file_id):
    if not is_valid_id(file_id):
        return jsonify({"error": "Invalid ID"}), 400
    def generate():
        last_percent = -1
        while True:
            prog = progress_store.get(file_id, {})
            status = prog.get('status', 'starting')
            percent = prog.get('percent', 0)
            if status == 'complete':
                yield f"data: {json.dumps({'percent': 100, 'status': 'complete', 'file_id': file_id})}\n\n"
                break
            elif status == 'error':
                yield f"data: {json.dumps({'status': 'error', 'error': prog.get('error', 'Unknown error')})}\n\n"
                break
            elif percent != last_percent:
                last_percent = percent
                yield f"data: {json.dumps({'percent': percent, 'status': 'downloading'})}\n\n"
            time.sleep(0.5)
    return Response(stream_with_context(generate()), mimetype="text/event-stream")

@app.route("/file/<file_id>")
def stream_video(file_id):
    if not is_valid_id(file_id):
        return jsonify({"error": "Invalid ID"}), 400
    fp = get_file_path(file_id)
    if not fp.exists():
        return jsonify({"error": "File not found"}), 404
    
    # İndirme tamamlanmamışsa bekle
    for _ in range(60):
        prog = progress_store.get(file_id, {})
        if prog.get('status') == 'complete':
            break
        if prog.get('status') == 'error':
            return jsonify({"error": "Download failed"}), 500
        time.sleep(0.5)
    
    # Stream sonrası dosyayı sil
    @after_this_request
    def remove_file(response):
        try:
            if fp.exists():
                fp.unlink()
            if file_id in progress_store:
                del progress_store[file_id]
        except:
            pass
        return response
    
    def generate():
        with open(fp, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                yield chunk
    
    return Response(generate(), mimetype="video/mp4", headers={"Content-Disposition": "attachment; filename=video.mp4"})

@app.route("/delete/<file_id>", methods=["POST", "DELETE"])
def delete_video(file_id):
    fp = get_file_path(file_id)
    try:
        if fp.exists():
            fp.unlink()
        if file_id in progress_store:
            del progress_store[file_id]
        return jsonify({"success": True})
    except:
        return jsonify({"error": "Delete failed"}), 500

@app.route("/robots.txt")
def robots():
    return """User-agent: *
Allow: /
Sitemap: https://clipdropx-server.onrender.com/sitemap.xml
""", 200, {'Content-Type': 'text/plain'}

@app.route("/sitemap.xml")
def sitemap():
    return """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://clipdropx-server.onrender.com/</loc>
    <priority>1.0</priority>
  </url>
</urlset>
""", 200, {'Content-Type': 'application/xml'}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)