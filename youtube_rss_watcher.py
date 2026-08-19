"""
YouTube RSS Watcher
--------------------
channels.json dosyasındaki YouTube kanallarını periyodik olarak kontrol eder,
yeni yüklenen videoları tespit edip ekrana yazdırır ve new_videos.log dosyasına kaydeder.

Kurulum:
    pip install feedparser

Çalıştırma:
    python3 youtube_rss_watcher.py

Arka planda sürekli çalıştırmak için (Linux/Mac):
    nohup python3 youtube_rss_watcher.py > watcher_output.log 2>&1 &

Durdurmak için:
    CTRL+C (ön planda çalışıyorsa) ya da process'i kill edin.
"""

import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

import feedparser

# ---------- Ayarlar ----------
CHECK_INTERVAL_SECONDS = 5 * 60          # 5 dakika
CHANNELS_FILE = Path("channels.json")
SEEN_VIDEOS_FILE = Path("seen_videos.json")
NEW_VIDEOS_LOG = Path("new_videos.log")
YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("youtube_rss_watcher")


def load_channels() -> list[dict]:
    """channels.json dosyasından kanal listesini okur."""
    if not CHANNELS_FILE.exists():
        logger.error(f"{CHANNELS_FILE} bulunamadı. Önce kanal listesini oluşturun.")
        return []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("channels", [])


def load_seen_videos() -> set:
    """Daha önce görülmüş video ID'lerini diskten yükler."""
    if not SEEN_VIDEOS_FILE.exists():
        return set()
    with open(SEEN_VIDEOS_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_seen_videos(seen: set) -> None:
    """Görülmüş video ID'lerini diske kaydeder."""
    with open(SEEN_VIDEOS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def log_new_video(channel_name: str, entry) -> None:
    """Yeni bulunan videoyu log dosyasına ve ekrana yazar."""
    title = getattr(entry, "title", "Başlık yok")
    link = getattr(entry, "link", "")
    published = getattr(entry, "published", "")

    line = f"[{datetime.now(timezone.utc).isoformat()}] {channel_name} -> {title} | {link} | Yayın: {published}"
    logger.info(f"YENİ VİDEO: {channel_name} - {title}")

    with open(NEW_VIDEOS_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check_channel(channel: dict, seen: set) -> int:
    """Tek bir kanalı kontrol eder, yeni videoları işler. Bulunan yeni video sayısını döner."""
    channel_id = channel.get("channel_id")
    channel_name = channel.get("name", channel_id)

    if not channel_id:
        logger.warning(f"Kanal ID eksik: {channel}")
        return 0

    url = YOUTUBE_FEED_URL.format(channel_id=channel_id)

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error(f"{channel_name} için feed çekilemedi: {e}")
        return 0

    if feed.bozo and not feed.entries:
        logger.warning(f"{channel_name} feed'i okunamadı ya da boş (channel_id doğru mu?)")
        return 0

    new_count = 0
    for entry in feed.entries:
        video_id = getattr(entry, "yt_videoid", None) or getattr(entry, "id", None)
        if not video_id:
            continue
        if video_id not in seen:
            log_new_video(channel_name, entry)
            seen.add(video_id)
            new_count += 1

    return new_count


def run_once() -> int:
    """Tüm kanalları bir kez kontrol eder. Bulunan yeni video sayısını döner.
    GitHub Actions gibi zamanlanmış (cron) ortamlarda kullanılır."""
    seen = load_seen_videos()
    channels = load_channels()

    if not channels:
        logger.warning("Kontrol edilecek kanal yok, channels.json dosyasını kontrol edin.")
        return 0

    logger.info(f"{len(channels)} kanal kontrol ediliyor...")
    total_new = 0
    for channel in channels:
        total_new += check_channel(channel, seen)

    if total_new > 0:
        save_seen_videos(seen)
        logger.info(f"Toplam {total_new} yeni video bulundu ve kaydedildi.")
    else:
        logger.info("Yeni video yok.")

    return total_new


def run_forever():
    """Script'i sürekli açık tutup 5 dakikada bir kontrol eder.
    Kendi sunucunuzda / bilgisayarınızda çalıştırmak için kullanılır."""
    logger.info("YouTube RSS Watcher başlatıldı (sürekli mod).")
    while True:
        run_once()
        logger.info(f"{CHECK_INTERVAL_SECONDS} saniye bekleniyor...")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:
        # GitHub Actions gibi zamanlanmış ortamlar için: tek kontrol yapıp çık.
        run_once()
    else:
        try:
            run_forever()
        except KeyboardInterrupt:
            logger.info("Watcher durduruldu (CTRL+C).")
