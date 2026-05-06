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
    # "iciou",   # اگر کانال معتبر نیست، کامنت کنید
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

# ---------- تغییرات کلیدی ----------
# دیگر یک CUTOFF_DATE سختگیرانه نداریم.
# فقط در صورت تمایل می‌توانید یک محدودیت بسیار بلندمدت بگذارید (مثلاً ۳۰ روز)
# که با کامنت کردن خط زیر غیرفعال می‌شود.
# USE_DATE_LIMIT = True
# LIMIT_DAYS = 30
# ------------------------------------


def sanitize_filename(name: str) -> str:
    """تبدیل نام فایل غیر ASCII به یک نام تصادفی امن یا پاکسازی کاراکترهای غیرمجاز"""
    if not name.isascii():
        ext = Path(name).suffix
        random_name = uuid.uuid4().hex[:8] + ext
        print(f"    🌐 Renaming non-ASCII: {name} -> {random_name}")
        return random_name
    else:
        clean = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
        clean = re.sub(r'__+', '_', clean)
        if clean != name:
            print(f"    📝 Cleaning filename: {name} -> {clean}")
        return clean


def get_filename_from_response(response, fallback_name: str) -> str:
    """استخراج نام فایل از هدر HTTP یا URL نهایی + پاکسازی نام"""
    content_disp = response.headers.get("Content-Disposition", "")
    match = re.search(r'filename\s*=\s*["\']?([^"\';]+)["\']?', content_disp, re.IGNORECASE)
    if match:
        name = match.group(1).strip()
        if name:
            print(f"    📄 نام فایل از هدر: {name}")
            return sanitize_filename(name)
    match = re.search(r"filename\*\s*=\s*UTF-8''(.+)", content_disp, re.IGNORECASE)
    if match:
        from urllib.parse import unquote
        name = unquote(match.group(1)).strip()
        if name:
            print(f"    📄 نام فایل از هدر (UTF-8): {name}")
            return sanitize_filename(name)
    final_url = response.url
    path_part = final_url.split("?")[0]
    url_name = path_part.rsplit("/", 1)[-1]
    if "." in url_name and len(url_name) < 200:
        print(f"    📄 نام فایل از URL نهایی: {url_name}")
        return sanitize_filename(url_name)
    return sanitize_filename(fallback_name)


def split_large_file(filepath: Path, threshold_mb: int = 90):
    """اگر حجم فایل بیش از حد مجاز باشد، به قطعه‌های zip تقسیم می‌کند"""
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
        filepath.unlink()
        print(f"    ✅ فایل تقسیم شد و فایل اصلی حذف گردید.")
    except Exception as e:
        print(f"    ❌ خطا در تقسیم فایل: {e}")


def download_media(url: str, filepath: Path, threshold_mb: int = 90):
    """دانلود یک فایل رسانه‌ای با تشخیص نام واقعی از هدر و پشتیبانی از تقسیم فایل حجیم"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=90, stream=True)
        resp.raise_for_status()
        real_name = get_filename_from_response(resp, filepath.name)
        final_path = filepath.parent / real_name
        final_path.parent.mkdir(parents=True, exist_ok=True)
        content = resp.content
        final_path.write_bytes(content)
        print(f"    ✅ دانلود شد: {real_name} ({len(content)} بایت)")
        split_large_file(final_path, threshold_mb)
    except Exception as e:
        print(f"    ❌ خطا در دانلود {filepath.name}: {e}")


def process_message(msg_div):
    """پردازش یک div پیام و استخراج شناسه، تاریخ، متن و رسانه‌ها"""
    data_post = msg_div.get("data-post")
    if not data_post:
        return None
    msg_id = int(data_post.split("/")[-1])

    time_tag = msg_div.find("time")
    msg_datetime = None
    if time_tag and time_tag.get("datetime"):
        try:
            msg_datetime = datetime.fromisoformat(time_tag["datetime"])
        except:
            pass

    text_div = msg_div.find("div", class_="tgme_widget_message_text")
    text = text_div.get_text(strip=True) if text_div else ""

    media = []

    photo_wrap = msg_div.find("a", class_="tgme_widget_message_photo_wrap")
    if photo_wrap:
        style = photo_wrap.get("style", "")
        url_match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
        if url_match:
            media.append(("photo", url_match.group(1)))

    video = msg_div.find("video")
    if video and video.get("src"):
        media.append(("video", video["src"]))

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


def message_already_saved(channel_dir: Path, msg_id: int) -> bool:
    """بررسی می‌کند که آیا پیام با این شناسه قبلاً دانلود شده است یا نه"""
    # اگر یک فایل text برای این شناسه وجود داشته باشد، آن را ذخیره‌شده در نظر می‌گیریم
    texts_dir = channel_dir / "texts"
    if (texts_dir / f"{msg_id}.txt").exists():
        return True

    # اگر متن وجود ندارد، شاید پیام فقط فایل بوده؛
    # هر فایلی که نامش با msg_id شروع شود (در هر زیرپوشه) نشانۀ ذخیره‌شدن است.
    for subdir in ["photos", "videos", "files", "texts"]:
        d = channel_dir / subdir
        if d.exists():
            if any(f.name.startswith(f"{msg_id}_") or f.name.startswith(f"{msg_id}.") for f in d.iterdir()):
                return True

    return False


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
    """پردازش کامل یک کانال (اسکرول تا رسیدن به پیام تکراری)"""
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

    new_messages = []
    offset = 0
    stop_scraping = False
    # .:: حذف محدودیت CUTOFF_DATE سختگیرانه ::.
    # اگر خواستید یک محدودیت خیلی بلندمدت داشته باشید، خط زیر را فعال کنید:
    # cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)

    while not stop_scraping:
        msgs = scrape_page(channel, offset)
        if not msgs:
            print("  📭 صفحه خالی - توقف")
            break

        for msg in msgs:
            parsed = process_message(msg)
            if parsed is None:
                continue

            # بررسی تکراری بودن با وجود فیزیکی فایل
            if message_already_saved(base_dir, parsed["id"]):
                print(f"  ⏹ پیام تکراری یافت شد (ID: {parsed['id']}) - توقف")
                stop_scraping = True
                break

            # (اختیاری) محدودیت تاریخ بسیار بلندمدت
            # if parsed["datetime"] is not None and 'cutoff_date' in locals() and parsed["datetime"] < cutoff_date:
            #     print(f"  ⏳ پیام قدیمی‌تر از حد مجاز (ID: {parsed['id']}) - توقف")
            #     stop_scraping = True
            #     break

            new_messages.append(parsed)

        if not stop_scraping and msgs:
            last_on_page = process_message(msgs[-1])
            if last_on_page:
                offset = last_on_page["id"]
            time.sleep(1.5)

    if not new_messages:
        print("  ✨ پیام جدیدی برای دانلود وجود ندارد.")
        return

    new_messages.sort(key=lambda x: x["id"])
    print(f"  📩 تعداد پیام‌های جدید: {len(new_messages)}")

    for msg in new_messages:
        msg_id = msg["id"]

        # ذخیره متن (حتی اگر خالی باشد، برای ردگیری بهتر)
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

    # (اختیاری) ذخیره آخرین شناسه برای کارهای دیگر - دیگر ضروری نیست ولی می‌توانید نگه دارید
    # با این حال ما این کار را برای سازگاری با اجراهای قبلی انجام می‌دهیم
    if new_messages:
        (base_dir / "last_id.txt").write_text(str(new_messages[-1]["id"]))
    print(f"  ✅ کانال {channel} به‌روزرسانی شد.")


def process_single_link(link: str, threshold_mb: int = 90):
    """دانلود یک پست منفرد با لینک مستقیم"""
    print(f"\n{'='*50}")
    print(f"🔗 پردازش لینک: {link}")
    print(f"{'='*50}")

    match = re.search(r"https?://t\.me/([^/]+)/(\d+)", link)
    if not match:
        print("❌ لینک نامعتبر است. فرمت باید https://t.me/channel/123 باشد.")
        return
    channel, msg_id_str = match.groups()
    msg_id = int(msg_id_str)

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

    downloads_dir = Path("downloads")
    downloads_dir.mkdir(exist_ok=True)

    if parsed["text"]:
        text_filename = f"{msg_id}_text.txt"
        text_path = downloads_dir / text_filename
        text_path.write_text(parsed["text"], encoding="utf-8")
        print(f"  💾 متن ذخیره شد: {text_filename}")

    if not parsed["media"]:
        print("  ℹ️ این پست رسانه‌ای ندارد.")
        return

    for i, (med_type, med_url) in enumerate(parsed["media"]):
        temp_name = f"{msg_id}_{med_type}_{i}.dat"
        filepath = downloads_dir / temp_name
        download_media(med_url, filepath, threshold_mb)

    print(f"  ✅ دانلود از لینک به پایان رسید.")


def main():
    parser = argparse.ArgumentParser(description="Telegram scraper / single link downloader")
    parser.add_argument("--link", type=str, help="لینک مستقیم پست تلگرام")
    parser.add_argument("--threshold", type=int, default=90, help="حداکثر حجم هر قطعه فایل به مگابایت")
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
