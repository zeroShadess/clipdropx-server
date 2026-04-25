# 🚀 ClipDropX Server

Modern video downloader API (YouTube, TikTok, Instagram)

## ⚡ Features

* 🎬 Multi-platform support
* ⚡ Fast downloading (yt-dlp powered)
* 🔒 Temporary file system (auto delete)
* 📱 Web + Mobile compatible

## 🧠 Tech Stack

* Python (Flask)
* yt-dlp
* FFmpeg

## 🚀 API Usage

### Download

POST `/download`

```json
{
  "url": "VIDEO_URL",
  "quality": "1080"
}
```

### Get File

GET `/file/{id}`

### Delete File

POST `/delete/{id}`

## 🌐 Live Demo

https://clipdropx-server.onrender.com/

## ⚠️ Disclaimer

For educational purposes only. Respect copyright laws.
