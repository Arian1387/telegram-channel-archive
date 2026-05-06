import re
import time
import uuid
import argparse
import subprocess
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ⚠️ نام کاربری کانال‌ها را بدون @ وارد کنید
CHANNELS = [
    "oxnet_ir",
    "Do1rcci",
    "iciou"
    # هر کانال دیگر را اینجا اضافه کنید
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://t.me/",
}
CUTOFF_DATE = datetime.now(timezone.utc) - timedelta(days=1)


def sanitize_filename(name: str) -> str:
    """
    اگر نام فایل غیر ASCII داشته باشد با یک رشته تصادفی جایگزین می‌کند؛
    در غیر این صورت کاراکترهای غیرمجاز را پاک‌سازی می‌کند.
    """
    # اگر نام فایل شامل کاراکتر غیر انگلیسی باشد
    if not name.isascii():
        ext = Path(name).suffix
        random_name = uuid.uuid4().hex[:8] + ext
        print(f"    🌐 Renaming non-ASCII: {name} -> {random_name}")
        return random_name
    else:
        # حذف کاراکترهای غیرمجاز برای نام فایل
        clean = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
        clean = re.sub(r'__+', '_', clean)  # فشرده‌سازی زیرخط‌ها
        if clean != name:
            print(f"    📝 Cleaning filename: {name} -> {clean}")
        return clean


def get_last_id(channel_folder: Path) -> int:
    """خواندن آخرین شناسه ذخیره‌شده برای یک کانال"""
    last_id_file = channel_folder / "last_id.txt"
    if last_id_file.exists():
        try:
            return int(last_id_file.read_text().strip())
        except ValueError:
            return 0
    return 0


def save_last_id(channel_folder: Path, msg_id: int):
    """ذخیره آخرین شناسه پردازش‌شده"""
    (channel_folder / "last_id.txt").write_text(str(msg_id))


def get_filename_from_response(response, fallback_name: str) -> str:
    """
    سعی می‌کند نام فایل را از Content-Disposition هدر دریافت کند.
    اگر پیدا نشد، نام فایل را از URL نهایی (بعد از redirect) استخراج می‌کند.
    اگر باز هم پیدا نشد، fallback را برمی‌گرداند.
    """
    content_disp = response.headers.get("Content-Disposition", "")

    # فرمت اول: attachment; filename="name.ext"
    match = re.search(r'filename\s*=\s*["\']?([^"\';]+)["\']?', content_disp, re.IGNORECASE)
    if match:
        name = match.group(1).strip()
        if name:
            print(f"    📄 نام فایل از هدر: {name}")
            return sanitize_filename(name)

    # فرمت دوم: filename*=UTF-8''name.ext
    match = re.search(r"filename\*\s*=\s*UTF-8''(.+)", content_disp, re.IGNORECASE)
    if match:
        from urllib.parse import unquote
        name = unquote(match.group(1)).strip()
        if name:
            print(f"    📄 نام فایل از هدر (UTF-8): {name}")
            return sanitize_filename(name)

    # تلاش از URL نهایی (بعد از ریدایرکت‌ها)
    final_url = response.url
    path_part = final_url.split("?")[0]
    url_name = path_part.rsplit("/", 1)[-1]
    if "." in url_name and len(url_name) < 200:
        print(f"    📄 نام فایل از URL نهایی: {url_name}")
        return sanitize_filename(url_name)

    return sanitize_filename(fallback_name)


def split_large_file(filepath: Path, threshold_mb: int = 90):
    """اگر حجم فایل بزرگتر از threshold_mb مگابایت باشد، آن را با zip چندبخشی می‌کند"""
    size_mb = filepath.stat().st_size / (1024 * 1024)
    if size_mb <= threshold_mb:
        return

    print(f"    ✂️ حجم فایل {size_mb:.1f}MB - تقسیم به قطعه‌های {threshold_mb}MB با zip")
    try:
        subprocess.run(
            ["zip", "-s", f"{threshold_mb}m", f"{filepath.name}.zip", filepath.name],
            cwd=filepath.parent,
            check=True,
            timeout=600
        )
        filepath.unlink()  # فایل اصلی را حذف کن
        print(f"    ✅ فایل تقسیم شد و فایل اصلی حذف گردید.")
    except Exception as e:
        print(f"    ❌ خطا در تقسیم فایل: {e}")


def download_media(url: str, filepath: Path, threshold_mb: int = 90):
    """دانلود یک فایل رسانه‌ای با تشخیص نام واقعی از هدر و پشتیبانی از تقسیم فایل حجیم"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=90, stream=True)
        resp.raise_for_status()

        # تشخیص نام واقعی فایل (همراه با پاک‌سازی)
        real_name = get_filename_from_response(resp, filepath.name)

        final_path = filepath.parent / real_name
        final_path.parent.mkdir(parents=True, exist_ok=True)

        content = resp.content
        final_path.write_bytes(content)
        print(f"    ✅ دانلود شد: {real_name} ({len(content)} بایت)")

        # اگر فایل بزرگ است، تقسیم کن
        split_large_file(final_path, threshold_mb)

    except Exception as e:
        print(f"    ❌ خطا در دانلود {filepath.name}: {e}")


def process_message(msg_div):
    """پردازش یک div پیام و استخراج شناسه، تاریخ، متن و لینک رسانه‌ها"""
    data_post = msg_div.get("data-post")
    if not data_post:
        return None
    msg_id = int(data_post.split("/")[-1])

    # تاریخ پیام
    time_tag = msg_div.find("time")
    msg_datetime = None
    if time_tag and time_tag.get("datetime"):
        try:
            msg_datetime = datetime.fromisoformat(time_tag["datetime"])
        except:
            pass

    # متن پیام
    text_div = msg_div.find("div", class_="tgme_widget_message_text")
    text = text_div.get_text(strip=True) if text_div else ""

    # رسانه‌ها
    media = []

    # عکس
    photo_wrap = msg_div.find("a", class_="tgme_widget_message_photo_wrap")
    if photo_wrap:
        style = photo_wrap.get("style", "")
        url_match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
        if url_match:
            media.append(("photo", url_match.group(1)))

    # ویدئو
    video = msg_div.find("video")
    if video and video.get("src"):
        media.append(("video", video["src"]))

    # فایل/سند (هر نوع فایلی)
    doc_wrap = msg_div.find("a", class_="tgme_widget_message_document_wrap")
    if doc_wrap and doc_wrap.get("href"):
        doc_url = doc_wrap["href"]
        if doc_url.startswith("/"):
            doc_url = f"https://t.me{doc_url}"
        media.append(("document", doc_url))

    return {
        "id": msg_id,
        "datetime": msg_datetime,
        "text": text,
        "media": media,
    }


def scrape_page(channel: str, offset_id: int = 0):
    """دریافت یک صفحه از t.me/s"""
    url = f"https://t.me/s/{channel}"
    if offset_id:
        url += f"?before={offset_id}"
    print(f"  📡 دریافت: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return soup.find_all("div", class_="tgme_widget_message")


def scrape_channel(channel: str, threshold_mb: int = 90):
    """پردازش کامل یک کانال (اسکرول تا ۱۰ روز قبل)"""
    print(f"\n{'='*50}")
    print(f"🎯 شروع کانال: {channel}")
    print(f"{'='*50}")

    base_dir = Path("data") / channel
    photos_dir = base_dir / "photos"
    videos_dir = base_dir / "videos"
    files_dir  = base_dir / "files"
    texts_dir  = base_dir / "texts"

    for d in [photos_dir, videos_dir, files_dir, texts_dir]:
        d.mkdir(parents=True, exist_ok=True)

    last_id = get_last_id(base_dir)
    print(f"  🆔 آخرین شناسه ذخیره‌شده: {last_id}")

    new_messages = []
    offset = 0
    stop_scraping = False

    while not stop_scraping:
        msgs = scrape_page(channel, offset)
        if not msgs:
            print("  📭 صفحه خالی - توقف")
            break

        for msg in msgs:
            parsed = process_message(msg)
            if parsed is None:
                continue

            if parsed["id"] <= last_id:
                print(f"  ⏹ پیام ذخیره‌شده (ID: {parsed['id']}) - توقف")
                stop_scraping = True
                break

            if parsed["datetime"] is not None and parsed["datetime"] < CUTOFF_DATE:
                print(f"  ⏳ پیام قدیمی‌تر از ۱ روز (ID: {parsed['id']}) - توقف")
                stop_scraping = True
                break

            new_messages.append(parsed)

        if not stop_scraping and msgs:
            last_on_page = process_message(msgs[-1])
            if last_on_page:
                offset = last_on_page["id"]
            time.sleep(1.5)

    if not new_messages:
        print("  ✨ پیام جدیدی در ۱ روز اخیر نیست.")
        return

    new_messages.sort(key=lambda x: x["id"])
    print(f"  📩 تعداد پیام‌های جدید: {len(new_messages)}")

    for msg in new_messages:
        msg_id = msg["id"]

        # ذخیره متن
        text_file = texts_dir / f"{msg_id}.txt"
        text_file.write_text(msg["text"], encoding="utf-8")

        # دانلود رسانه‌ها
        for med_type, med_url in msg["media"]:
            temp_name = f"{msg_id}_{med_type}.dat"
            if med_type == "photo":
                filepath = photos_dir / temp_name
            elif med_type == "video":
                filepath = videos_dir / temp_name
            else:
                filepath = files_dir / temp_name

            download_media(med_url, filepath, threshold_mb)

        save_last_id(base_dir, msg["id"])

    print(f"  ✅ کانال {channel} به‌روزرسانی شد.")


def process_single_link(link: str, threshold_mb: int = 90):
    """پردازش یک لینک پست تلگرام و دانلود متن + رسانه‌ها"""
    print(f"\n{'='*50}")
    print(f"🔗 پردازش لینک: {link}")
    print(f"{'='*50}")

    # استخراج channel و msg_id از لینک
    match = re.search(r"https?://t\.me/([^/]+)/(\d+)", link)
    if not match:
        print("❌ لینک نامعتبر است. فرمت باید https://t.me/channel/123 باشد.")
        return
    channel, msg_id_str = match.groups()
    msg_id = int(msg_id_str)

    # ساختن URL نسخۀ embed برای دریافت پیام
    embed_url = f"https://t.me/s/{channel}/{msg_id}?embed=1&mode=compact"
    print(f"  📡 دریافت: {embed_url}")

    try:
        resp = requests.get(embed_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ خطا در دریافت صفحه: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    msg_div = soup.find("div", class_="tgme_widget_message")
    if not msg_div:
        print("❌ پیام در صفحه پیدا نشد.")
        return

    parsed = process_message(msg_div)
    if not parsed:
        print("❌ پردازش پیام با مشکل مواجه شد.")
        return

    print(f"  📄 شناسه پیام: {parsed['id']} | تاریخ: {parsed['datetime']}")
    if parsed["text"]:
        print(f"  📝 متن: {parsed['text'][:100]}{'...' if len(parsed['text'])>100 else ''}")

    # ساخت پوشه downloads
    downloads_dir = Path("downloads")
    downloads_dir.mkdir(exist_ok=True)

    # ذخیره متن
    if parsed["text"]:
        text_filename = f"{msg_id}_text.txt"
        text_path = downloads_dir / text_filename
        text_path.write_text(parsed["text"], encoding="utf-8")
        print(f"  💾 متن ذخیره شد: {text_filename}")

    # دانلود رسانه‌ها
    if not parsed["media"]:
        print("  ℹ️ این پست رسانه‌ای ندارد.")
        return

    for i, (med_type, med_url) in enumerate(parsed["media"]):
        # نام موقت (دانلود با نام واقعی به‌روز می‌شود)
        temp_name = f"{msg_id}_{med_type}_{i}.dat"
        filepath = downloads_dir / temp_name
        download_media(med_url, filepath, threshold_mb)

    print(f"  ✅ دانلود از لینک به پایان رسید.")


def main():
    parser = argparse.ArgumentParser(description="Telegram scraper / single link downloader")
    parser.add_argument("--link", type=str, help="لینک مستقیم پست تلگرام (مثلاً https://t.me/channel/123)")
    parser.add_argument("--threshold", type=int, default=90, help="حداکثر حجم هر قطعه فایل به مگابایت (پیش‌فرض 90)")
    args = parser.parse_args()

    if args.link:
        process_single_link(args.link, args.threshold)
    else:
        print(f"🕒 زمان شروع: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"📋 کانال‌ها: {', '.join(CHANNELS)}")
        for channel in CHANNELS:
            try:
                scrape_channel(channel, args.threshold)
            except Exception as e:
                print(f"  ❌ خطا در پردازش کانال {channel}: {e}")
        print(f"\n🕒 زمان پایان: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")


if __name__ == "__main__":
    main()
