"""
checker.py — چکر خودکار
این فایل رو GitHub Actions هر ۳۰ دقیقه اجرا می‌کنه.
watchlist.json رو میخونه، همه محصولات رو چک میکنه،
اگه سایز موردنظر موجود شد پیام تلگرام میفرسته.
"""

import os, json, requests, time, logging
from bs4 import BeautifulSoup
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API     = f"https://api.telegram.org/bot{TOKEN}"
DB_FILE = "watchlist.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
}


def load_db() -> dict:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_db(db: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def send_telegram(chat_id: str, text: str):
    try:
        r = requests.post(f"{API}/sendMessage", json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=10)
        r.raise_for_status()
        log.info(f"  ✅ پیام به {chat_id} ارسال شد")
    except Exception as e:
        log.error(f"  ❌ تلگرام error: {e}")


def get_available_sizes(url: str) -> list | None:
    """سایزهای موجود رو از صفحه محصول برمیگردونه"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning(f"  ⚠ شبکه: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    available = []

    for sel_el in soup.find_all("select"):
        nm = (sel_el.get("name") or "").lower()
        if any(k in nm for k in ["size", "سایز", "attribute_pa"]):
            for opt in sel_el.find_all("option"):
                val = opt.get("value", "").strip()
                if not val or val.lower() in ("", "انتخاب کنید", "choose an option"):
                    continue
                if not opt.get("disabled"):
                    available.append(val.upper())

    if not available:
        for item in soup.find_all(
            ["li", "span", "a"],
            class_=lambda c: c and any(k in c for k in
                ["swatch", "swatches", "tawcss", "variation-selector", "size"])
        ):
            cls  = " ".join(item.get("class", []))
            text = item.get_text(strip=True)
            if not text or len(text) > 7: continue
            if any(k in cls for k in ["disabled", "out-of-stock", "soldout"]): continue
            available.append(text.upper())

    # "ANY" mode: اگه کلاً صفحه لود بشه و out-of-stock نباشه = موجود
    if not available:
        oos = soup.find(class_=lambda c: c and "out-of-stock" in c)
        if not oos:
            available = ["ANY"]

    return list(dict.fromkeys(available))


def run_checks():
    if not TOKEN:
        log.error("TELEGRAM_BOT_TOKEN تنظیم نشده!")
        return

    db      = load_db()
    changed = False
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")

    total_items = sum(len(v) for v in db.values())
    log.info(f"🔍 شروع چک — {now} — {len(db)} کاربر، {total_items} آیتم")

    for chat_id, items in db.items():
        for item in items:
            url      = item.get("url", "")
            name     = item.get("name", "محصول")
            watching = [s.upper() for s in item.get("watching", [])]

            if not url or not watching:
                continue

            log.info(f"  📦 {name} ({', '.join(watching)})")
            avail = get_available_sizes(url)

            if avail is None:
                log.info("     خطای شبکه — رد")
                time.sleep(2)
                continue

            log.info(f"     موجود: {avail}")

            # تطابق: اگه "ANY" باشه هر موجودی کافیه
            if "ANY" in watching:
                matched = avail if avail else []
            else:
                matched = [s for s in watching if s in avail]

            prev_matched = [s.upper() for s in item.get("prev_matched", [])]
            newly        = [s for s in matched if s not in prev_matched]

            if newly:
                size_text = "موجود شد" if "ANY" in watching else f"سایز {', '.join(newly)} موجود شد"
                msg = (
                    f"🎉 <b>{name}</b>\n\n"
                    f"✅ {size_text}!\n\n"
                    f"🔗 <a href='{url}'>برو بخر!</a>\n\n"
                    f"⏰ {now}"
                )
                send_telegram(chat_id, msg)
                item["notified"]    = True
                item["prev_matched"] = matched
                changed = True
                log.info(f"     🔔 پیام فرستاده شد: {newly}")
            else:
                # ریست کن اگه دوباره ناموجود شد (برای دفعه بعد)
                if not matched and item.get("prev_matched"):
                    item["prev_matched"] = []
                    changed = True
                log.info("     💤 تغییری نیست")

            item["last_checked"] = now
            changed = True
            time.sleep(3)

    if changed:
        save_db(db)
        log.info("✔ watchlist.json آپدیت شد")


if __name__ == "__main__":
    run_checks()
