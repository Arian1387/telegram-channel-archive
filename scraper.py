import re
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ⚠️ نام کاربری کانال‌ها را بدون @ وارد کنید
CHANNELS = [
    "oxnet_ir",
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
CUTOFF_DATE = datetime.now(timezone.utc) - timedelta(days=10)


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
            return name

    # فرمت دوم: filename*=UTF-8''name.ext
    match = re.search(r"filename\*\s*=\s*UTF-8''(.+)", content_disp, re.IGNORECASE)
    if match:
        from urllib.parse import unquote
        name = unquote(match.group(1)).strip()
        if name:
            print(f"    📄 نام فایل از هدر (UTF-8): {name}")
            return name

    # تلاش از URL نهایی (بعد از ریدایرکت‌ها)
    final_url = response.url
    path_part = final_url.split("?")[0]
    url_name = path_part.rsplit("/", 1)[-1]
    if "." in url_name and len(url_name) < 200:
        print(f"    📄 نام فایل از URL نهایی: {url_name}")
        return url_name

    return fallback_name


def download_media(url: str, filepath: Path):
    """دانلود یک فایل رسانه‌ای با تشخیص نام واقعی از هدر"""
    try:
        # اول یک HEAD request برای گرفتن هدر بدون دانلود کامل
        # بعد GET request اصلی با stream=True برای تشخیص نام نهایی
        resp = requests.get(url, headers=HEADERS, timeout=90, stream=True)
        resp.raise_for_status()

        # تشخیص نام واقعی فایل
        real_name = get_filename_from_response(resp, filepath.name)

        # تعیین مسیر نهایی با نام واقعی
        final_dir = filepath.parent
        final_path = final_dir / real_name
        final_path.parent.mkdir(parents=True, exist_ok=True)

        # ذخیره محتوا
        content = resp.content
        final_path.write_bytes(content)
        print(f"    ✅ دانلود شد: {real_name} ({len(content)} بایت)")
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


def scrape_channel(channel: str):
    """پردازش کامل یک کانال"""
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
                print(f"  ⏳ پیام قدیمی‌تر از ۱۰ روز (ID: {parsed['id']}) - توقف")
                stop_scraping = True
                break

            new_messages.append(parsed)

        if not stop_scraping and msgs:
            last_on_page = process_message(msgs[-1])
            if last_on_page:
                offset = last_on_page["id"]
            time.sleep(1.5)

    if not new_messages:
        print("  ✨ پیام جدیدی در ۱۰ روز اخیر نیست.")
        return

    new_messages.sort(key=lambda x: x["id"])
    print(f"  📩 تعداد پیام‌های جدید: {len(new_messages)}")

    for msg in new_messages:
        msg_id = msg["id"]

        # ذخیره متن پیام
        text_file = texts_dir / f"{msg_id}.txt"
        text_file.write_text(msg["text"], encoding="utf-8")

        # دانلود رسانه‌ها
        for med_type, med_url in msg["media"]:
            # نام موقت با پسوند dat که بعداً با نام واقعی جایگزین می‌شود
            temp_name = f"{msg_id}_{med_type}.dat"

            if med_type == "photo":
                filepath = photos_dir / temp_name
            elif med_type == "video":
                filepath = videos_dir / temp_name
            else:
                filepath = files_dir / temp_name

            download_media(med_url, filepath)

        save_last_id(base_dir, msg["id"])

    print(f"  ✅ کانال {channel} به‌روزرسانی شد.")


def main():
    print(f"🕒 زمان شروع: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"📋 کانال‌ها: {', '.join(CHANNELS)}")

    for channel in CHANNELS:
        try:
            scrape_channel(channel)
        except Exception as e:
            print(f"  ❌ خطا در پردازش کانال {channel}: {e}")

    print(f"\n🕒 زمان پایان: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")


if __name__ == "__main__":
    main()
