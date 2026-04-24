"""
ClipDropX - Production Ready Video Downloader
- Chunk-based streaming (no send_file)
- No re-encoding (fast remux only)
- Cookie support
- Auto cleanup
- Timeout & size limits
- Gunicorn compatible
"""

import os
import re
import time
import uuid
import tempfile
import shutil
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timedelta

import yt_dlp
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

# ========== KONFIGÜRASYON ==========
app = Flask(__name__)
CORS(app, origins="*")

# Ortam değişkenleri
PORT = int(os.environ.get("PORT", 5000))
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", 500))
CLEANUP_HOURS = int(os.environ.get("CLEANUP_HOURS", 2))
COOKIES_B64 = os.environ.get("CLIPDROPX_COOKIES_B64", "")  # Base64 encoded cookies
TEMP_DIR = tempfile.gettempdir()
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for streaming

# Geçici cookies dosyası (varsa)
COOKIE_FILE = None
if COOKIES_B64:
    import base64
    try:
        cookies_data = base64.b64decode(COOKIES_B64).decode('utf-8')
        COOKIE_FILE = os.path.join(TEMP_DIR, f"cookies_{uuid.uuid4().hex[:8]}.txt")
        with open(COOKIE_FILE, 'w') as f:
            f.write(cookies_data)
        print(f"[INFO] Cookie file created: {COOKIE_FILE}")
    except Exception as e:
        print(f"[WARN] Failed to load cookies: {e}")


# ========== YARDIMCI FONKSİYONLAR ==========
def is_valid_url(url: str) -> bool:
    """Desteklenen platformları kontrol et"""
    allowed_domains = [
        "tiktok.com", "instagram.com", "twitter.com", "x.com",
        "reddit.com", "vimeo.com", "youtube.com", "youtu.be"
    ]
    if not url.startswith(("http://", "https://")):
        return False
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(domain.endswith(d) for d in allowed_domains)
    except:
        return False


def is_valid_id(rid: str) -> bool:
    """ID format kontrolü (8 hex karakter)"""
    return bool(re.match(r"^[a-f0-9]{8}$", rid))


def get_file_path(file_id: str) -> Path:
    """Geçici dosya yolunu döndür"""
    return Path(TEMP_DIR) / f"clipdropx_{file_id}.mp4"


def cleanup_old_files():
    """Eski dosyaları temizle (thread-safe)"""
    try:
        now = time.time()
        max_age = CLEANUP_HOURS * 3600
        for file_path in Path(TEMP_DIR).glob("clipdropx_*.mp4"):
            if now - file_path.stat().st_mtime > max_age:
                file_path.unlink()
                print(f"[CLEANUP] Deleted {file_path.name}")
    except Exception as e:
        print(f"[CLEANUP ERROR] {e}")


def get_ytdlp_opts(output_path: str) -> dict:
    """
    Optimize edilmiş yt-dlp parametreleri
    - No re-encoding (sadece remux)
    - Hızlı fragment download
    - Zaman aşımı koruması
    """
    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": output_path,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "concurrent_fragment_downloads": 4,          # Hızlı indirme
        "retries": 5,                               # Ağ hatası durumunda tekrar
        "fragment_retries": 5,
        "socket_timeout": 30,                       # 30 saniye timeout
        "file_access_retries": 3,
        "postprocessors": [{
            "key": "FFmpegVideoRemuxer",            # Sadece container değiştirir, re-encode YOK!
            "preferedformat": "mp4",
        }],
    }

    # Cookie desteği
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE

    return opts


def download_video(url: str, file_id: str) -> tuple[bool, str, int]:
    """
    Videoyu indir ve geçici dosyaya kaydet.
    Return: (başarılı_mı?, hata_mesajı, dosya_boyutu_bytes)
    """
    output_path = str(get_file_path(file_id))
    ydl_opts = get_ytdlp_opts(output_path)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Önce video bilgilerini al (boyut kontrolü için)
            info = ydl.extract_info(url, download=False)
            
            # Dosya boyutunu tahmin et (eğer varsa)
            estimated_size = 0
            if "filesize" in info and info["filesize"]:
                estimated_size = info["filesize"]
            elif "filesize_approx" in info:
                estimated_size = info["filesize_approx"]
            
            # Maksimum boyut kontrolü
            max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
            if estimated_size > max_bytes:
                return False, f"Video exceeds {MAX_FILE_SIZE_MB}MB limit", 0
            
            # İndir
            ydl.download([url])
            
            # Dosyanın gerçek boyutunu kontrol et
            actual_size = os.path.getsize(output_path)
            if actual_size == 0:
                return False, "Downloaded file is empty", 0
            if actual_size > max_bytes:
                os.remove(output_path)
                return False, f"Downloaded file exceeds {MAX_FILE_SIZE_MB}MB limit", 0
            
            return True, "", actual_size
            
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "Private video" in error_msg:
            return False, "Video is private", 0
        if "Video unavailable" in error_msg:
            return False, "Video not found", 0
        return False, f"Download error: {error_msg[:100]}", 0
    except Exception as e:
        return False, f"Unexpected error: {str(e)[:100]}", 0


# ========== STREAMING GENERATOR ==========
def generate_video_stream(file_path: Path, chunk_size: int = CHUNK_SIZE):
    """
    Dosyayı chunk chunk okuyan generator.
    send_file yerine kullanılır.
    """
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    except Exception as e:
        print(f"[STREAM ERROR] {e}")
        yield b""


# ========== FLASK ENDPOINTS ==========
@app.route("/")
def home():
    """Ana sayfa - basit bir mesaj"""
    return jsonify({
        "service": "ClipDropX Downloader",
        "status": "running",
        "version": "3.0",
        "endpoints": ["/download", "/file/<id>", "/delete/<id>", "/health"]
    })


@app.route("/health", methods=["GET"])
def health():
    """Sağlık kontrolü"""
    cleanup_old_files()  # Periyodik temizlik
    return jsonify({"status": "healthy", "timestamp": time.time()})


@app.route("/download", methods=["POST"])
def download():
    """
    Video indirme isteği
    Request: {"url": "https://..."}
    Response: {"id": "abcd1234"}
    """
    cleanup_old_files()  # Her istekte temizlik kontrolü
    
    try:
        data = request.get_json()
        if not data or "url" not in data:
            return jsonify({"error": "Missing 'url' field"}), 400
        
        url = data["url"].strip()
        if not is_valid_url(url):
            return jsonify({"error": "Unsupported URL"}), 400
        
        # Benzersiz ID oluştur
        file_id = uuid.uuid4().hex[:8]
        
        # Videoyu indir
        success, error_msg, file_size = download_video(url, file_id)
        
        if not success:
            return jsonify({"error": error_msg}), 400
        
        return jsonify({
            "id": file_id,
            "size_bytes": file_size,
            "size_mb": round(file_size / (1024 * 1024), 2)
        })
        
    except Exception as e:
        print(f"[DOWNLOAD ERROR] {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/file/<file_id>", methods=["GET"])
def stream_video(file_id):
    """
    Videoyu chunked streaming ile döndür.
    send_file KULLANILMAZ -> generator + stream_with_context
    """
    if not is_valid_id(file_id):
        return jsonify({"error": "Invalid ID format"}), 400
    
    file_path = get_file_path(file_id)
    
    if not file_path.exists():
        return jsonify({"error": "File not found or expired"}), 404
    
    # Dosya boyutunu al (Content-Length için)
    try:
        file_size = file_path.stat().st_size
    except:
        file_size = None
    
    # Streaming generator ile yanıt
    def generate():
        yield from generate_video_stream(file_path)
    
    response = Response(
        stream_with_context(generate()),
        mimetype="video/mp4",
        headers={
            "Content-Disposition": "attachment; filename=video.mp4",
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
            **({"Content-Length": str(file_size)} if file_size else {})
        }
    )
    return response


@app.route("/delete/<file_id>", methods=["POST", "DELETE"])
def delete_video(file_id):
    """
    Geçici dosyayı sil (isteğe bağlı)
    """
    if not is_valid_id(file_id):
        return jsonify({"error": "Invalid ID format"}), 400
    
    file_path = get_file_path(file_id)
    
    try:
        if file_path.exists():
            file_path.unlink()
            return jsonify({"success": True, "message": "File deleted"})
        else:
            return jsonify({"success": False, "message": "File not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ========== CLEANUP SCHEDULER (background thread) ==========
import threading
import atexit

def cleanup_scheduler():
    """Arka planda periyodik temizlik yapan thread"""
    while True:
        time.sleep(3600)  # Her saat başı
        cleanup_old_files()

# Scheduler'ı başlat (sadece main'de değil, gunicorn için de çalışsın)
_cleanup_thread = threading.Thread(target=cleanup_scheduler, daemon=True)
_cleanup_thread.start()

atexit.register(lambda: print("[INFO] Shutting down..."))


# ========== ENTRY POINT ==========
if __name__ == "__main__":
    print(f"[INFO] Starting ClipDropX on port {PORT}")
    print(f"[INFO] Temp dir: {TEMP_DIR}")
    print(f"[INFO] Max file size: {MAX_FILE_SIZE_MB}MB")
    print(f"[INFO] Cookies loaded: {bool(COOKIE_FILE)}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)