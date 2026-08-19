"""
YouTube RSS Watcher
--------------------
channels.json dosyasındaki YouTube kanallarını periyodik olarak kontrol eder,
yeni yüklenen videoları tespit eder ve şu çıktıları üretir:

  - videos_store.json  : En güncel videoların (meta veriyle birlikte) veri deposu
  - feed.xml            : GitHub Pages üzerinden servis edilecek RSS 2.0 feed'i
  - index.html           : Tarayıcıda görüntülenebilir, insan-okur özet sayfası
  - new_videos.log       : Her çalıştırmada bulunan yeni videoların kaydı

Kurulum:
    pip install -r requirements.txt

Çalıştırma (tek seferlik, ör. GitHub Actions içinde):
    python3 youtube_rss_watcher.py --once

Arka planda sürekli çalıştırmak için (Linux/Mac, kendi sunucunuzda):
    nohup python3 youtube_rss_watcher.py > watcher_output.log 2>&1 &

Durdurmak için:
    CTRL+C (ön planda çalışıyorsa) ya da process'i kill edin.
"""

import json
import time
import logging
import calendar
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import feedparser

# Sitede gösterilen saatler için (RSS feed.xml standart gereği UTC kalır)
TURKEY_TZ = ZoneInfo("Europe/Istanbul")

# ---------- Ayarlar ----------
CHECK_INTERVAL_SECONDS = 5 * 60          # 5 dakika
CHANNELS_FILE = Path("channels.json")
STORE_FILE = Path("videos_store.json")   # tüm bilinen videoların meta verisi
NEW_VIDEOS_LOG = Path("new_videos.log")
FEED_FILE = Path("feed.xml")
INDEX_FILE = Path("index.html")

YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

# Sitenizin gerçek adresini buraya yazın (RSS <link>/<atom:link> için önemli)
SITE_URL = "https://tucanarat.github.io/youtube-haber-takip/"
FEED_TITLE = "YouTube Haber Takip"
FEED_DESCRIPTION = "Takip edilen YouTube haber kanallarındaki yeni videolar"

MAX_ITEMS = 50  # feed/sitede tutulacak en fazla video sayısı

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("youtube_rss_watcher")


# ---------------------------------------------------------------------------
# Kanal / depo okuma-yazma
# ---------------------------------------------------------------------------

def load_channels() -> list[dict]:
    """channels.json dosyasından kanal listesini okur."""
    if not CHANNELS_FILE.exists():
        logger.error(f"{CHANNELS_FILE} bulunamadı. Önce kanal listesini oluşturun.")
        return []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("channels", [])


def load_store() -> dict:
    """Daha önce kaydedilmiş video meta verilerini diskten yükler.
    Yapı: {video_id: {"id", "title", "link", "published", "published_ts", "channel_name"}}"""
    if not STORE_FILE.exists():
        return {}
    with open(STORE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_store(store: dict) -> None:
    """Video meta veri deposunu diske kaydeder."""
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def log_new_video(channel_name: str, video: dict) -> None:
    """Yeni bulunan videoyu log dosyasına ve ekrana yazar."""
    line = (
        f"[{datetime.now(timezone.utc).isoformat()}] "
        f"{channel_name} -> {video['title']} | {video['link']} | Yayın: {video['published']}"
    )
    logger.info(f"YENİ VİDEO: {channel_name} - {video['title']}")

    with open(NEW_VIDEOS_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Kanal kontrolü
# ---------------------------------------------------------------------------

def check_channel(channel: dict, store: dict) -> int:
    """Tek bir kanalı kontrol eder, store'u günceller.
    Bulunan yeni video sayısını döner."""
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

        # YouTube tarafından verilen published_parsed (UTC struct_time) -> unix timestamp
        if getattr(entry, "published_parsed", None):
            published_ts = calendar.timegm(entry.published_parsed)
        else:
            published_ts = int(datetime.now(timezone.utc).timestamp())

        published_str = getattr(entry, "published", "")
        title = getattr(entry, "title", "Başlık yok")
        link = getattr(entry, "link", "")

        is_new = video_id not in store

        store[video_id] = {
            "id": video_id,
            "title": title,
            "link": link,
            "published": published_str,
            "published_ts": published_ts,
            "channel_name": channel_name,
        }

        if is_new:
            log_new_video(channel_name, store[video_id])
            new_count += 1

    return new_count


# ---------------------------------------------------------------------------
# feed.xml (RSS) ve index.html üretimi
# ---------------------------------------------------------------------------

def build_feed_xml(videos: list[dict]) -> str:
    """Video listesinden geçerli bir RSS 2.0 feed'i üretir."""
    now_rfc822 = format_datetime(datetime.now(timezone.utc))

    items_xml = []
    for v in videos:
        pub_dt = datetime.fromtimestamp(v["published_ts"], tz=timezone.utc)
        items_xml.append(
            "    <item>\n"
            f"      <title>{escape(v['channel_name'])}: {escape(v['title'])}</title>\n"
            f"      <link>{escape(v['link'])}</link>\n"
            f"      <guid isPermaLink=\"false\">{escape(v['id'])}</guid>\n"
            f"      <pubDate>{format_datetime(pub_dt)}</pubDate>\n"
            f"      <description>{escape(v['channel_name'])} kanalından yeni video: {escape(v['title'])}</description>\n"
            "    </item>"
        )

    items_block = "\n".join(items_xml)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{escape(FEED_TITLE)}</title>\n"
        f"    <link>{escape(SITE_URL)}</link>\n"
        f"    <atom:link href=\"{escape(SITE_URL)}feed.xml\" rel=\"self\" type=\"application/rss+xml\" />\n"
        f"    <description>{escape(FEED_DESCRIPTION)}</description>\n"
        "    <language>tr</language>\n"
        f"    <lastBuildDate>{now_rfc822}</lastBuildDate>\n"
        f"{items_block}\n"
        "  </channel>\n"
        "</rss>\n"
    )


def build_index_html(videos: list[dict]) -> str:
    """Video listesinden basit, okunabilir bir HTML sayfası üretir."""
    now_str = datetime.now(TURKEY_TZ).strftime("%Y-%m-%d %H:%M") + " (TSİ)"

    rows = []
    for v in videos:
        pub_dt = datetime.fromtimestamp(v["published_ts"], tz=TURKEY_TZ)
        pub_str = pub_dt.strftime("%Y-%m-%d %H:%M") + " (TSİ)"
        rows.append(
            "      <li class=\"video\">\n"
            f"        <span class=\"channel\">{escape(v['channel_name'])}</span>\n"
            f"        <a class=\"title\" href=\"{escape(v['link'])}\" target=\"_blank\" rel=\"noopener\">{escape(v['title'])}</a>\n"
            f"        <span class=\"date\">{pub_str}</span>\n"
            "      </li>"
        )

    rows_block = "\n".join(rows) if rows else "      <li class=\"empty\">Henüz video bulunamadı.</li>"

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(FEED_TITLE)}</title>
  <link rel="alternate" type="application/rss+xml" title="{escape(FEED_TITLE)}" href="feed.xml">
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0f1115;
      --card: #1a1d24;
      --text: #eef0f3;
      --muted: #9aa3b2;
      --accent: #ff4d4d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      padding: 32px 20px 16px;
      max-width: 780px;
      margin: 0 auto;
    }}
    header h1 {{
      margin: 0 0 6px;
      font-size: 1.6rem;
    }}
    header p {{
      margin: 0;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    main {{
      max-width: 780px;
      margin: 0 auto;
      padding: 0 20px 40px;
    }}
    ul.videos {{
      list-style: none;
      margin: 0;
      padding: 0;
    }}
    li.video {{
      background: var(--card);
      border-radius: 10px;
      padding: 14px 16px;
      margin-bottom: 10px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    li.video .channel {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--accent);
      font-weight: 600;
    }}
    li.video .title {{
      color: var(--text);
      text-decoration: none;
      font-size: 1rem;
      font-weight: 500;
    }}
    li.video .title:hover {{
      text-decoration: underline;
    }}
    li.video .date {{
      color: var(--muted);
      font-size: 0.78rem;
    }}
    li.empty {{
      color: var(--muted);
      text-align: center;
      padding: 20px;
    }}
    footer {{
      text-align: center;
      color: var(--muted);
      font-size: 0.75rem;
      padding: 20px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(FEED_TITLE)}</h1>
    <p>{escape(FEED_DESCRIPTION)} · Son güncelleme: {now_str}</p>
  </header>
  <main>
    <ul class="videos">
{rows_block}
    </ul>
  </main>
  <footer>
    Her 5 dakikada bir GitHub Actions ile otomatik güncellenir.
  </footer>
</body>
</html>
"""


def write_outputs(store: dict) -> None:
    """Depodaki videoları en yeniden en eskiye sıralayıp feed.xml ve index.html üretir."""
    videos = sorted(store.values(), key=lambda v: v["published_ts"], reverse=True)[:MAX_ITEMS]

    with open(FEED_FILE, "w", encoding="utf-8") as f:
        f.write(build_feed_xml(videos))

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(build_index_html(videos))

    logger.info(f"{FEED_FILE} ve {INDEX_FILE} güncellendi ({len(videos)} video).")


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------

def run_once() -> int:
    """Tüm kanalları bir kez kontrol eder, çıktı dosyalarını üretir.
    Bulunan yeni video sayısını döner.
    GitHub Actions gibi zamanlanmış (cron) ortamlarda kullanılır."""
    store = load_store()
    channels = load_channels()

    if not channels:
        logger.warning("Kontrol edilecek kanal yok, channels.json dosyasını kontrol edin.")
        return 0

    logger.info(f"{len(channels)} kanal kontrol ediliyor...")
    total_new = 0
    for channel in channels:
        total_new += check_channel(channel, store)

    save_store(store)
    write_outputs(store)

    if total_new > 0:
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
