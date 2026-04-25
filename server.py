"""
ClipDropX - Fixed Production Server
Düzeltmeler:
  1. yt-dlp çıktı dosyasını ext bağımsız bulur (FileNotFoundError fix)
  2. Format string sadeleştirildi, kalite seçimi güvenilir
  3. İndirme tamamlanmadan /file isteğine cevap verilmiyor
  4. Progress store'da gerçek dosya yolu saklanıyor
  5. Temizlik race condition'ı giderildi
"""

import os
import re
import time
import glob
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

PORT          = int(os.environ.get("PORT", 5000))
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE_MB", 500)) * 1024 * 1024
CLEANUP_HOURS = int(os.environ.get("CLEANUP_HOURS", 2))
TEMP_DIR      = tempfile.gettempdir()
CHUNK_SIZE    = 512 * 1024   # 512 KB chunk - daha stabil streaming

progress_store: dict = {}
store_lock = threading.Lock()

# ─────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ─────────────────────────────────────────────

ALLOWED_DOMAINS = [
    "youtube.com", "youtu.be",
    "tiktok.com",
    "instagram.com",
    "twitter.com", "x.com",
    "reddit.com",
    "vimeo.com",
    "dailymotion.com",
    "facebook.com",
    "twitch.tv",
]

def is_valid_url(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
        return any(netloc == d or netloc.endswith("." + d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False

def is_valid_id(rid: str) -> bool:
    return bool(re.match(r"^[a-f0-9]{8}$", rid))

def find_output_file(file_id: str) -> Path | None:
    """
    yt-dlp bazen .mp4 dışında uzantı kullanır.
    Prefix ile eşleşen ilk dosyayı döndürür.
    .part dosyalarını atla (henüz tamamlanmamış).
    """
    pattern = os.path.join(TEMP_DIR, f"clipdropx_{file_id}.*")
    matches = [
        Path(f) for f in glob.glob(pattern)
        if not f.endswith(".part") and not f.endswith(".ytdl")
    ]
    if not matches:
        return None
    # En büyük dosyayı tercih et (birleştirilmiş çıktı)
    return max(matches, key=lambda p: p.stat().st_size)

def cleanup_old_files():
    """2 saatten eski ClipDropX geçici dosyalarını sil."""
    try:
        now = time.time()
        for f in Path(TEMP_DIR).glob("clipdropx_*"):
            try:
                if now - f.stat().st_mtime > CLEANUP_HOURS * 3600:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass

def quality_to_format(quality: str) -> str:
    """
    Güvenilir format seçimi.
    Önce mp4+m4a (ffmpeg merge gerektirmez veya hızlı merge),
    fallback olarak any video+audio, son olarak best.
    """
    q = quality.lower().strip()

    height_map = {
        "2160": 2160, "4k": 2160,
        "1080": 1080,
        "720":  720,
        "480":  480,
        "360":  360,
    }

    if q == "best":
        # Mevcut en iyi kalite
        return (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best"
        )

    h = height_map.get(q, 1080)

    return (
        # 1. Tercih: mp4 video + m4a ses (hızlı merge / no-remux)
        f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
        # 2. Tercih: Herhangi video + m4a ses
        f"bestvideo[height<={h}]+bestaudio[ext=m4a]/"
        # 3. Tercih: Herhangi video + herhangi ses
        f"bestvideo[height<={h}]+bestaudio/"
        # 4. Fallback: tek dosya o yükseklikte
        f"best[height<={h}]/"
        # 5. Son çare: en iyi mevcut
        "best"
    )

# ─────────────────────────────────────────────
# PROGRESS HOOK
# ─────────────────────────────────────────────

def make_progress_hook(file_id: str):
    def hook(d: dict):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
            downloaded = d.get("downloaded_bytes", 0)
            pct = min(round((downloaded / total) * 100, 1), 99)  # 100'ü thread verir
            with store_lock:
                progress_store[file_id].update({
                    "percent": pct,
                    "speed":   d.get("speed") or 0,
                    "eta":     d.get("eta") or 0,
                    "status":  "downloading",
                })
        elif d["status"] == "finished":
            # yt-dlp download bitti ama postprocess (merge) henüz olabilir
            with store_lock:
                progress_store[file_id].update({
                    "percent": 99,
                    "status":  "processing",
                })
    return hook

# ─────────────────────────────────────────────
# İNDİRME THREAD'İ
# ─────────────────────────────────────────────

def download_thread(url: str, file_id: str, quality: str):
    # %(ext)s kullan — yt-dlp gerçek uzantıyı yazar
    outtmpl = os.path.join(TEMP_DIR, f"clipdropx_{file_id}.%(ext)s")

    ydl_opts = {
        "format":      quality_to_format(quality),
        "outtmpl":     outtmpl,
        "noplaylist":  True,
        "quiet":       False,           # Render loglarında görmek için
        "no_warnings": False,
        "retries":     5,
        "fragment_retries": 5,
        "socket_timeout":   30,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [make_progress_hook(file_id)],
        # Merge çıktısını mp4'e zorla (mkv/webm karışıklığını önler)
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # ── Dosyayı bul ──────────────────────────────────────
        # Merge sonrası gerçek dosya adını tespit et
        output_file = find_output_file(file_id)

        if output_file is None:
            raise FileNotFoundError(
                f"Download tamamlandı ama dosya bulunamadı: "
                f"clipdropx_{file_id}.*"
            )

        file_size = output_file.stat().st_size

        if file_size > MAX_FILE_SIZE:
            output_file.unlink(missing_ok=True)
            raise ValueError(
                f"Dosya boyutu limitin üzerinde: "
                f"{file_size // (1024*1024)}MB > {MAX_FILE_SIZE // (1024*1024)}MB"
            )

        with store_lock:
            progress_store[file_id].update({
                "percent":   100,
                "status":    "complete",
                "file_path": str(output_file),   # ← GERÇEk DOSYA YOLU
                "file_size": file_size,
            })

    except Exception as e:
        # Kalan dosyaları temizle
        for leftover in glob.glob(os.path.join(TEMP_DIR, f"clipdropx_{file_id}.*")):
            try:
                Path(leftover).unlink(missing_ok=True)
            except Exception:
                pass

        with store_lock:
            progress_store[file_id] = {
                "status": "error",
                "error":  str(e),
            }
        print(f"[ERROR] {file_id}: {e}")

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.route("/")
def home():
    try:
        return send_from_directory(".", "index.html")
    except Exception:
        return jsonify({"service": "ClipDropX API", "status": "running"})

@app.route("/health")
def health():
    cleanup_old_files()
    return jsonify({"status": "healthy", "temp_dir": TEMP_DIR})


@app.route("/download", methods=["POST"])
def start_download():
    cleanup_old_files()

    data = request.get_json(silent=True)
    if not data or "url" not in data:
        return jsonify({"error": "url alanı gerekli"}), 400

    url = str(data["url"]).strip()
    if not url:
        return jsonify({"error": "url boş olamaz"}), 400
    if not is_valid_url(url):
        return jsonify({"error": "Desteklenmeyen URL"}), 400

    quality = str(data.get("quality", "1080")).strip()
    file_id = uuid.uuid4().hex[:8]

    with store_lock:
        progress_store[file_id] = {"percent": 0, "status": "starting"}

    t = threading.Thread(target=download_thread, args=(url, file_id, quality), daemon=True)
    t.start()

    return jsonify({"id": file_id})


@app.route("/progress/<file_id>")
def progress_stream(file_id):
    """Server-Sent Events ile gerçek zamanlı ilerleme."""
    if not is_valid_id(file_id):
        return jsonify({"error": "Geçersiz ID"}), 400

    def generate():
        last = None
        timeout_at = time.time() + 600   # max 10 dakika bekle

        while time.time() < timeout_at:
            with store_lock:
                prog = dict(progress_store.get(file_id, {"status": "starting", "percent": 0}))

            status = prog.get("status", "starting")
            payload = {
                "percent": prog.get("percent", 0),
                "status":  status,
                "speed":   prog.get("speed", 0),
                "eta":     prog.get("eta", 0),
            }

            if status == "complete":
                payload["percent"] = 100
                yield f"data: {json.dumps(payload)}\n\n"
                break
            elif status == "error":
                payload["error"] = prog.get("error", "Bilinmeyen hata")
                yield f"data: {json.dumps(payload)}\n\n"
                break
            else:
                if payload != last:
                    yield f"data: {json.dumps(payload)}\n\n"
                    last = payload
                time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Nginx / Render proxy buffer devre dışı
        }
    )


@app.route("/file/<file_id>")
def serve_file(file_id):
    """
    İndirilen dosyayı tarayıcıya stream et.
    Dosya gönderildikten sonra otomatik sil.
    """
    if not is_valid_id(file_id):
        return jsonify({"error": "Geçersiz ID"}), 400

    # İndirme tamamlanana kadar bekle (max 60 saniye)
    deadline = time.time() + 60
    while time.time() < deadline:
        with store_lock:
            prog = dict(progress_store.get(file_id, {}))

        status = prog.get("status")

        if status == "complete":
            break
        elif status == "error":
            return jsonify({"error": prog.get("error", "İndirme başarısız")}), 500
        elif not status:
            return jsonify({"error": "Bilinmeyen session"}), 404

        time.sleep(0.5)
    else:
        return jsonify({"error": "Zaman aşımı: download tamamlanamadı"}), 504

    # Gerçek dosya yolunu progress_store'dan al
    with store_lock:
        fp_str = progress_store.get(file_id, {}).get("file_path")

    if not fp_str:
        # Fallback: diskten ara
        fp = find_output_file(file_id)
    else:
        fp = Path(fp_str)

    if fp is None or not fp.exists():
        return jsonify({"error": "Dosya bulunamadı"}), 404

    file_size = fp.stat().st_size
    filename  = f"clipdropx_{file_id}.mp4"

    @after_this_request
    def delete_after_send(response):
        """Response tamamlandıktan sonra dosyayı ve kaydı sil."""
        def _cleanup():
            time.sleep(2)  # Stream'in gerçekten bitmesi için küçük bekleme
            try:
                fp.unlink(missing_ok=True)
            except Exception:
                pass
            with store_lock:
                progress_store.pop(file_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()
        return response

    def generate():
        try:
            with open(fp, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
        except Exception as e:
            print(f"[STREAM ERROR] {file_id}: {e}")

    return Response(
        generate(),
        mimetype="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length":      str(file_size),
            "Accept-Ranges":       "bytes",
        }
    )


@app.route("/delete/<file_id>", methods=["POST", "DELETE"])
def delete_file(file_id):
    if not is_valid_id(file_id):
        return jsonify({"error": "Geçersiz ID"}), 400

    deleted = []
    for leftover in glob.glob(os.path.join(TEMP_DIR, f"clipdropx_{file_id}.*")):
        try:
            Path(leftover).unlink(missing_ok=True)
            deleted.append(leftover)
        except Exception:
            pass

    with store_lock:
        progress_store.pop(file_id, None)

    return jsonify({"success": True, "deleted": deleted})


@app.route("/robots.txt")
def robots():
    content = "User-agent: *\nAllow: /\n"
    return content, 200, {"Content-Type": "text/plain"}

@app.route("/sitemap.xml")
def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://clipdropx-server.onrender.com/</loc><priority>1.0</priority></url>
</urlset>"""
    return xml, 200, {"Content-Type": "application/xml"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)