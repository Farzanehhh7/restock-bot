"""
bot.py — ربات تلگرام ردیاب موجودی
حالت: Long Polling (برای اجرای دستی / تست)
برای GitHub Actions از checker.py استفاده کن
"""

import os, json, time, logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API      = f"https://api.telegram.org/bot{TOKEN}"
DB_FILE  = "watchlist.json"   # { chat_id: [ {url, name, sizes, thumb} ] }


# ══════════════════════════════════════════
#  DB helpers
# ══════════════════════════════════════════

def load_db() -> dict:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_db(db: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_user_list(chat_id: str) -> list:
    return load_db().get(str(chat_id), [])

def save_user_list(chat_id: str, items: list):
    db = load_db()
    db[str(chat_id)] = items
    save_db(db)


# ══════════════════════════════════════════
#  Scraper
# ══════════════════════════════════════════

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
}

def scrape_product(url: str) -> dict | None:
    """
    اطلاعات کامل محصول رو از WooCommerce میگیره:
    نام، عکس، سایزهای موجود، سایزهای ناموجود
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning(f"scrape error: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # ── نام محصول ──
    name = ""
    for sel in ["h1.product_title", "h1.entry-title", "h1"]:
        el = soup.select_one(sel)
        if el:
            name = el.get_text(strip=True)
            break

    # ── عکس محصول ──
    thumb = ""
    img = soup.select_one(".woocommerce-product-gallery__image img, .product img")
    if img:
        thumb = img.get("src") or img.get("data-src") or ""

    # ── سایزها ──
    available_sizes   = []
    unavailable_sizes = []

    # روش ۱: select dropdown
    for sel_el in soup.find_all("select"):
        nm = (sel_el.get("name") or "").lower()
        if any(k in nm for k in ["size", "سایز", "attribute_pa"]):
            for opt in sel_el.find_all("option"):
                val = opt.get("value", "").strip()
                if not val or val.lower() in ("", "انتخاب کنید", "choose an option"):
                    continue
                label = opt.get_text(strip=True) or val
                if opt.get("disabled"):
                    unavailable_sizes.append(label.upper())
                else:
                    available_sizes.append(label.upper())

    # روش ۲: swatch li/span
    if not available_sizes and not unavailable_sizes:
        for item in soup.find_all(
            ["li", "span", "a"],
            class_=lambda c: c and any(k in c for k in
                ["swatch", "swatches", "tawcss", "variation-selector", "size"])
        ):
            cls = " ".join(item.get("class", []))
            text = item.get_text(strip=True)
            if not text or len(text) > 7:
                continue
            if any(k in cls for k in ["disabled", "out-of-stock", "soldout"]):
                unavailable_sizes.append(text.upper())
            else:
                available_sizes.append(text.upper())

    # dedup
    available_sizes   = list(dict.fromkeys(available_sizes))
    unavailable_sizes = list(dict.fromkeys(
        s for s in unavailable_sizes if s not in available_sizes
    ))

    return {
        "name":      name or "محصول",
        "thumb":     thumb,
        "available": available_sizes,
        "unavailable": unavailable_sizes,
        "all":       available_sizes + unavailable_sizes,
    }


# ══════════════════════════════════════════
#  Telegram API helpers
# ══════════════════════════════════════════

def tg(method: str, **kwargs) -> dict:
    try:
        r = requests.post(f"{API}/{method}", json=kwargs, timeout=15)
        return r.json()
    except Exception as e:
        log.error(f"tg {method} error: {e}")
        return {}

def send(chat_id, text, **kwargs):
    return tg("sendMessage", chat_id=chat_id, text=text,
               parse_mode="HTML", **kwargs)

def edit(chat_id, msg_id, text, **kwargs):
    return tg("editMessageText", chat_id=chat_id, message_id=msg_id,
               text=text, parse_mode="HTML", **kwargs)

def answer_cb(cb_id, text=""):
    tg("answerCallbackQuery", callback_query_id=cb_id, text=text)


# ══════════════════════════════════════════
#  پیام‌های ربات
# ══════════════════════════════════════════

WELCOME = (
    "👋 سلام! من ردیاب موجودی پوشاک هستم.\n\n"
    "لینک صفحه محصول رو برام بفرست تا سایزهای موجودش رو نشونت بدم "
    "و هر موقع سایز دلخواهت اومد خبرت کنم.\n\n"
    "📌 دستورها:\n"
    "/list — لیست محصولاتی که دنبال می‌کنی\n"
    "/clear — پاک کردن همه\n"
)

def fmt_watchlist(items: list) -> str:
    if not items:
        return "📋 هنوز چیزی دنبال نمی‌کنی.\n\nیه لینک محصول بفرست تا شروع کنیم!"
    lines = ["📋 <b>لیست دنبال‌شده‌ها:</b>\n"]
    for i, it in enumerate(items, 1):
        sizes_str = "، ".join(it.get("watching", []))
        status = "✅" if it.get("notified") else "⏳"
        lines.append(f"{status} {i}. <b>{it['name']}</b>\n"
                      f"   سایز: {sizes_str}\n"
                      f"   <a href='{it['url']}'>لینک</a>")
    return "\n".join(lines)


# ══════════════════════════════════════════
#  State موقت (انتظار انتخاب سایز)
# ══════════════════════════════════════════
# { chat_id: { "url": ..., "product": {...} } }
pending = {}


# ══════════════════════════════════════════
#  هندلرها
# ══════════════════════════════════════════

def handle_message(msg: dict):
    chat_id = str(msg["chat"]["id"])
    text    = msg.get("text", "").strip()

    if not text:
        return

    # دستورها
    if text in ("/start", "/help"):
        send(chat_id, WELCOME)
        return

    if text == "/list":
        items = get_user_list(chat_id)
        keyboard = None
        if items:
            keyboard = {"inline_keyboard": [
                [{"text": f"🗑 حذف {it['name'][:20]}", "callback_data": f"del:{i}"}]
                for i, it in enumerate(items)
            ]}
        send(chat_id, fmt_watchlist(items),
             reply_markup=keyboard or {})
        return

    if text == "/clear":
        save_user_list(chat_id, [])
        send(chat_id, "✅ همه محصولات پاک شدن.")
        return

    # اگه URL فرستاد
    if text.startswith("http"):
        url = text.split()[0]
        send(chat_id, "⏳ دارم سایت رو بررسی می‌کنم...")

        product = scrape_product(url)

        if product is None:
            send(chat_id, "❌ نتونستم صفحه رو بخونم. مطمئنی لینک درسته؟")
            return

        all_sizes = product["all"]

        if not all_sizes:
            # سایز پیدا نشد — شاید یه‌سایزه، بپرسیم
            pending[chat_id] = {"url": url, "product": product, "manual": True}
            send(chat_id,
                f"📦 <b>{product['name']}</b>\n\n"
                "سایزبندی مشخصی پیدا نشد (شاید یه‌سایزه).\n"
                "می‌خوای وقتی دوباره موجود شد خبرت بدم؟",
                reply_markup={"inline_keyboard": [[
                    {"text": "✅ آره، خبرم کن", "callback_data": "add_nosizecheck"},
                    {"text": "❌ نه",            "callback_data": "cancel"},
                ]]}
            )
            return

        pending[chat_id] = {"url": url, "product": product}

        # ساخت دکمه‌های سایز
        buttons = []
        row = []
        for s in all_sizes:
            is_avail = s in product["available"]
            label = f"✅ {s}" if is_avail else f"❌ {s}"
            row.append({"text": label, "callback_data": f"size:{s}"})
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([{"text": "🚫 لغو", "callback_data": "cancel"}])

        msg_text = (
            f"📦 <b>{product['name']}</b>\n\n"
            f"✅ موجود: {', '.join(product['available']) or '—'}\n"
            f"❌ ناموجود: {', '.join(product['unavailable']) or '—'}\n\n"
            "کدوم سایز رو می‌خوای؟ (می‌تونی چند تا انتخاب کنی)"
        )
        send(chat_id, msg_text,
             reply_markup={"inline_keyboard": buttons})
        return

    send(chat_id, "لینک محصول رو بفرست 👆\n(مثلاً: https://bassu.ir/product/...)")


def handle_callback(cb: dict):
    chat_id  = str(cb["message"]["chat"]["id"])
    msg_id   = cb["message"]["message_id"]
    data     = cb.get("data", "")
    cb_id    = cb["id"]

    answer_cb(cb_id)

    if data == "cancel":
        pending.pop(chat_id, None)
        edit(chat_id, msg_id, "👌 لغو شد.")
        return

    if data == "add_nosizecheck":
        info = pending.pop(chat_id, None)
        if not info:
            edit(chat_id, msg_id, "❌ خطا — دوباره امتحان کن")
            return
        product = info["product"]
        items   = get_user_list(chat_id)
        if any(it["url"] == info["url"] for it in items):
            edit(chat_id, msg_id, "⚠️ این محصول قبلاً اضافه شده بود.")
            return
        items.append({
            "url":      info["url"],
            "name":     product["name"],
            "thumb":    product.get("thumb", ""),
            "watching": ["ANY"],
            "notified": False,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        save_user_list(chat_id, items)
        edit(chat_id, msg_id,
             f"✅ <b>{product['name']}</b> اضافه شد.\n"
             "هروقت دوباره موجود بشه خبرت می‌کنم!")
        return

    if data.startswith("size:"):
        size = data[5:]
        info = pending.get(chat_id)
        if not info:
            edit(chat_id, msg_id, "❌ session منقضی شد — لینک رو دوباره بفرست")
            return

        product  = info["product"]
        watching = info.setdefault("selected_sizes", [])

        if size in watching:
            watching.remove(size)
        else:
            watching.append(size)

        # آپدیت دکمه‌ها
        all_sizes = product["all"]
        buttons = []
        row = []
        for s in all_sizes:
            is_avail  = s in product["available"]
            is_picked = s in watching
            if is_picked:
                label = f"🔵 {s} ✓"
            elif is_avail:
                label = f"✅ {s}"
            else:
                label = f"❌ {s}"
            row.append({"text": label, "callback_data": f"size:{s}"})
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        confirm_row = []
        if watching:
            confirm_row.append({"text": f"💾 ذخیره ({', '.join(watching)})", "callback_data": "confirm"})
        confirm_row.append({"text": "🚫 لغو", "callback_data": "cancel"})
        buttons.append(confirm_row)

        edit(chat_id, msg_id,
             f"📦 <b>{product['name']}</b>\n\n"
             f"✅ موجود: {', '.join(product['available']) or '—'}\n"
             f"❌ ناموجود: {', '.join(product['unavailable']) or '—'}\n\n"
             f"انتخاب‌شده: <b>{', '.join(watching) or '—'}</b>\n"
             "سایزهای موردنظرت رو انتخاب کن، بعد ذخیره بزن.",
             reply_markup={"inline_keyboard": buttons})
        return

    if data == "confirm":
        info = pending.pop(chat_id, None)
        if not info or not info.get("selected_sizes"):
            edit(chat_id, msg_id, "❌ هیچ سایزی انتخاب نکردی.")
            return

        product  = info["product"]
        watching = info["selected_sizes"]
        items    = get_user_list(chat_id)

        # جلوگیری از تکراری
        for it in items:
            if it["url"] == info["url"]:
                it["watching"] = list(set(it["watching"] + watching))
                save_user_list(chat_id, items)
                edit(chat_id, msg_id,
                     f"✅ سایزهای <b>{', '.join(watching)}</b> به لیست اضافه شد.")
                return

        items.append({
            "url":      info["url"],
            "name":     product["name"],
            "thumb":    product.get("thumb", ""),
            "watching": watching,
            "notified": False,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        save_user_list(chat_id, items)

        already = [s for s in watching if s in product["available"]]
        not_yet = [s for s in watching if s not in product["available"]]

        msg_parts = [f"✅ <b>{product['name']}</b> ذخیره شد!\n"]
        if already:
            msg_parts.append(f"🎉 سایز {', '.join(already)} الان موجوده — زودتر بخر!")
        if not_yet:
            msg_parts.append(f"⏳ وقتی سایز {', '.join(not_yet)} موجود بشه خبرت می‌کنم.")

        edit(chat_id, msg_id, "\n".join(msg_parts))
        return

    if data.startswith("del:"):
        idx   = int(data[4:])
        items = get_user_list(chat_id)
        if 0 <= idx < len(items):
            name = items[idx]["name"]
            items.pop(idx)
            save_user_list(chat_id, items)
            edit(chat_id, msg_id, f"🗑 «{name}» از لیست حذف شد.")
        return


# ══════════════════════════════════════════
#  Polling loop
# ══════════════════════════════════════════

def run_polling():
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN تنظیم نشده!")
        return

    log.info("ربات شروع کرد (long polling)...")
    offset = 0

    while True:
        try:
            data = tg("getUpdates", offset=offset, timeout=30)
            updates = data.get("result", [])

            for upd in updates:
                offset = upd["update_id"] + 1
                try:
                    if "message" in upd:
                        handle_message(upd["message"])
                    elif "callback_query" in upd:
                        handle_callback(upd["callback_query"])
                except Exception as e:
                    log.error(f"handler error: {e}", exc_info=True)

        except KeyboardInterrupt:
            log.info("متوقف شد.")
            break
        except Exception as e:
            log.error(f"polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_polling()
