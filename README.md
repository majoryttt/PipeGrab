# 🤖 PipeGrab — Telegram Media Downloader Bot

An asynchronous Telegram bot for instantly downloading high-quality videos, audio, photos, and albums from popular video hosting platforms and social networks.

*Read this in other languages: [Русский](README_RU.md)*

## 🚀 Supported Platforms
- **YouTube**: Regular videos, Shorts, audio (MP3), and playlists (with interactive video vs MP3 selection).
- **TikTok**: Watermark-free videos (direct CDN fast-path), as well as **photo slideshows (Photo Mode)** sent as a Telegram media group with background music as a separate audio file.
- **Instagram**: Reels, video posts, **single photos**, **photo carousels and albums**, and **Stories** (via cookies).
- **Twitter / X**: Videos and clips.
- **Pinterest**: Original quality photos, albums/carousels (Idea Pins), animated GIFs, videos, and boards.

---

## ✨ Features
- **Maximum Speed (Fast-Path)**:
  - Single-pass processing: the bot begins downloading immediately without redundant metadata pre-fetches.
  - Direct watermark-free TikTok streaming straight from CDN in fractions of a second with zero FFmpeg overhead.
  - Parallel downloading of albums, carousels, and stories via `asyncio.gather`.
  - Persistent HTTP connection pool with Keep-Alive and DNS caching.
- **Smart Link Detection**: Simply send a link to the chat (shortened URLs like `vt.tiktok.com`, `pin.it`, `youtu.be` are fully supported).
- **Auto-Delete Source Link for Chat Cleanliness**:
  - Automatically deletes the user's message with the link and the bot's status message after successful delivery (in a single batch call with zero delay).
  - If the download fails, the link message is preserved for easy editing.
  - Configurable via `DELETE_SOURCE_MESSAGE` in `.env`.
- **Full TikTok Support**:
  - Original quality video downloads without watermarks.
  - Photo post support (`/photo/`): extracts all original high-resolution photos into a Telegram media group and attaches the audio track separately.
- **Full Instagram Support**:
  - Downloads videos and Reels in original quality with audio.
  - High-resolution single photos and carousel albums.
  - Stories support when cookies are provided.
- **Full Pinterest Support**:
  - Photos downloaded at maximum original resolution (no preview compression).
  - Albums and carousels (Idea Pins) sent as a neat Telegram media group (up to 10 photos per group).
  - Animated GIFs delivered as native looped Telegram animations.
  - High-quality videos downloaded with sound.
- **Format Selection**: Interactive selection between video and audio (MP3) downloads for YouTube.
- **Playlist and Board Support**: Select the number of items (5, 10, 20, or all) with sequential, safe rate-limited delivery.
- **Progress Tracking**: Real-time progress bar (`[████░░░░░░] 45%`), download speed, and ETA, along with native Telegram upload actions.
- **Seamless Cookie Management**:
  - Send a `cookies.txt` file directly to the bot in Telegram — it validates the format and applies it instantly without requiring a restart!
  - Admin-only protection: only the bot owner (`ADMIN_ID`) can upload cookies.
  - Compatible with Local Bot API server through automatic path mapping.
- **Support for Files up to 2 GB**: Works with standard Telegram Bot API (up to 50 MB) and Local Telegram Bot API Server (up to 2000 MB).
- **Overload Protection**: Task queue, concurrent download semaphore, and a limit of 1 active download task per user.
- **Automatic Cleanup**: Downloaded temporary files are reliably deleted after sending. A background cleaner prevents Local Bot API binlog buildup.

---

## 📋 Bot Commands

| Command | Description |
| :--- | :--- |
| `/start` | Welcome message and list of supported services |
| `/help` | Detailed guide on how to use the bot |
| `/cookies` | Status of active cookies and detected authentications |
| `/status` | System status (API mode, file limits, free disk space, active downloads) |

---

## 🛠️ Local Quick Start

### 1. Clone and Set Up Virtual Environment
```bash
git clone https://github.com/majoryttt/PipeGrab
cd PipeGrab

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy the example configuration:
```bash
cp .env.example .env
```
Open `.env` and fill in your bot token from [@BotFather](https://t.me/BotFather):
```env
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_ID=123456789             # Your Telegram user ID to restrict cookie uploads
DELETE_SOURCE_MESSAGE=true     # Auto-delete source link message after download
```

### 3. Ensure FFmpeg is Installed
`ffmpeg` must be installed on your system:
```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y ffmpeg

# Arch Linux
sudo pacman -S ffmpeg

# macOS
brew install ffmpeg
```

### 4. Run the Bot
```bash
python3 -m bot.main
```

---

## 🐳 Deployment with Docker Compose

This is the recommended way to run PipeGrab 24/7. All system dependencies (`ffmpeg`, `python`) are pre-packaged in the container.

### 1. Set Up on Server
Clone the repository to your Linux server:
```bash
git clone https://github.com/majoryttt/PipeGrab
cd PipeGrab
cp .env.example .env
nano .env  # Enter your BOT_TOKEN and ADMIN_ID
```

### 2. Start Containers
```bash
docker compose up -d --build
```

### 3. Useful Commands
- View live logs:
  ```bash
  docker compose logs -f bot
  ```
- Restart the bot:
  ```bash
  docker compose restart bot
  ```
- Stop containers:
  ```bash
  docker compose down
  ```

---

## ⚡ Sending Large Files up to 2 GB (Local Bot API)

The standard Telegram Bot API restricts bot uploads to 50 MB.
If you want to send large videos (up to 2 GB), run the built-in Local Bot API server:

1. Go to [my.telegram.org](https://my.telegram.org), log in, and create an application (under *App configuration*) to obtain your `api_id` and `api_hash`.
2. Open `docker-compose.yml` and uncomment the `telegram-bot-api` service block and the `depends_on` directive.
3. In your `.env` file, specify:
   ```env
   LOCAL_BOT_API_URL=http://telegram-bot-api:8081
   MAX_FILE_SIZE_MB=2000
   TELEGRAM_API_ID=your_api_id
   TELEGRAM_API_HASH=your_api_hash
   ```
4. Restart the stack:
   ```bash
   docker compose up -d --build
   ```

---

## 🍪 Configuring Cookies (for Instagram Stories & YouTube)

Instagram restricts viewing Stories and certain posts without authentication. Cookies also help prevent bot verification challenges on YouTube.

**The easiest method:**
1. Install a browser extension for Chrome or Firefox (e.g., *Get cookies.txt LOCALLY* or *Cookie-Editor*).
2. Log into Instagram or YouTube in your browser.
3. Export cookies in **Netscape** format (saving as `cookies.txt`).
4. **Simply drag and drop or send the `cookies.txt` file directly to the bot in Telegram as a document!**
   - The bot validates the file, saves it, and enables access to Stories immediately without restarting.
   - You can check the cookie status at any time with `/cookies`.

**Alternative method:**
Place `cookies.txt` manually on your server at `data/cookies/cookies.txt`.
