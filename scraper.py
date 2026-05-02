import re
import time
import json
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ⚠️ نام کاربری کانال‌ها را بدون @ وارد کنید
CHANNELS = [
    "oxnet_ir",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...",
    "Referer": "https://t.me/",
}
CUTOFF_DATE = datetime.now(timezone.utc) - timedelta(days=10)

def get_last_id(channel_folder):
    last_id_file = channel_folder / "last_id.txt"
    if last_id_file.exists():
        return int(last_id_file.read_text().strip())
    return 0

def save_last_id(channel_folder, msg_id):
    (channel_folder / "last_id.txt").write_text(str(msg_id))

def download_media(url, filepath):
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(r.content)
        print(f"    ✅ دانلود شد: {filepath.name}")
    except Exception as e:
        print(f"    ❌ خطا در دانلود {filepath.name}: {e}")

def process_message(msg_div):
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

    # متن
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

    # فایل (سند، صوت، هر چیز دیگر)
    doc_wrap = msg_div.find("a", class_="tgme_widget_message_document_wrap")
    if doc_wrap and doc_wrap.get("href"):
        media.append(("document", doc_wrap["href"]))

    return {
        "id": msg_id,
        "datetime": msg_datetime,
        "text": text,
        "media": media,
    }

def scrape_page(channel, offset_id=0):
    url = f"https://t.me/s/{channel}"
    if offset_id:
        url += f"?before={offset_id}"
    print(f"  📡 دریافت: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return soup.find_all("div", class_="tgme_widget_message")

def scrape_channel(channel):
    print(f"\n🎯 شروع کانال: {channel}")
    base_dir = Path("data") / channel
    photos_dir = base_dir / "photos"
    videos_dir = base_dir / "videos"
    files_dir  = base_dir / "files"     # ← هر فایل غیرعکس/غیرویدئو
    texts_dir  = base_dir / "texts"

    for d in [photos_dir, videos_dir, files_dir, texts_dir]:
        d.mkdir(parents=True, exist_ok=True)

    last_id = get_last_id(base_dir)
    print(f"  آخرین شناسه ذخیره‌شده: {last_id}")

    new_messages = []
    offset = 0
    stop = False

    while not stop:
        msgs = scrape_page(channel, offset)
        if not msgs:
            break

        for msg in msgs:
            parsed = process_message(msg)
            if parsed is None:
                continue

            if parsed["id"] <= last_id:
                stop = True
                break

            if parsed["datetime"] is not None and parsed["datetime"] < CUTOFF_DATE:
                print(f"  ⏳ پیام قدیمی‌تر از ۱۰ روز (ID {parsed['id']}) - توقف")
                stop = True
                break

            new_messages.append(parsed)

        if not stop and msgs:
            last_on_page = process_message(msgs[-1])
            if last_on_page:
                offset = last_on_page["id"]
            time.sleep(1.5)

    if not new_messages:
        print("  ✨ پیام جدیدی در ۱۰ روز اخیر نیست.")
        return

    # مرتب‌سازی از قدیمی به جدید
    new_messages.sort(key=lambda x: x["id"])
    print(f"  📩 تعداد پیام‌های جدید: {len(new_messages)}")

    for msg in new_messages:
        msg_id = msg["id"]
        # ذخیره متن پیام (همیشه، حتی اگر خالی باشد)
        text_file = texts_dir / f"{msg_id}.txt"
        text_file.write_text(msg["text"], encoding="utf-8")

        # ذخیره رسانه‌ها
        for med_type, med_url in msg["media"]:
            # تعیین پسوند
            try:
                ext = med_url.rsplit(".", 1)[-1].split("?")[0]
                if ext.lower() not in ("jpg", "jpeg", "png", "gif", "webp", "mp4", "webm", "avi", "mkv",
                                       "pdf", "zip", "rar", "7z", "apk", "ipa", "mp3", "ogg", "wav", "flac"):
                    ext = "dat"  # پسوند ناشناخته → dat
            except:
                ext = "dat"

            filename = f"{msg_id}_{med_type}.{ext}"

            if med_type == "photo":
                filepath = photos_dir / filename
            elif med_type == "video":
                filepath = videos_dir / filename
            else:  # document و هر نوع ناشناخته → پوشه files
                filepath = files_dir / filename

            download_media(med_url, filepath)

        # به‌روزرسانی شناسه
        save_last_id(base_dir, msg["id"])

    print(f"  ✅ کانال {channel} به‌روز شد.")

def main():
    for channel in CHANNELS:
        try:
            scrape_channel(channel)
        except Exception as e:
            print(f"  ❌ خطا در پردازش کانال {channel}: {e}")

if __name__ == "__main__":
    main()
