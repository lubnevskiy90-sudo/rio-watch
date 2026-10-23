#!/usr/bin/env python3
"""Мониторинг Kia Rio III на Куфаре (все объявления, без фильтров).
Присылает в Telegram новые объявления и изменения в уже известных
(цена, пробег, год, место, характеристики).
Запуск: python rio_watch.py [--debug]
Переменные окружения: TG_TOKEN, TG_CHAT_ID
"""
import html
import json
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
DEBUG = "--debug" in sys.argv

BASE = "https://auto.kufar.by"
SLUGS = ("kia-rio-iii", "kia-rio-iii-restajling")  # поколение III и III Рестайлинг
PAGES_PER_SLUG = 2  # сколько страниц (по ~29 объявлений) смотреть, новые сверху
FIELDS = {
    "price": "цена",
    "mileage": "пробег",
    "year": "год",
    "loc": "место",
    "specs": "характеристики",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

AD_RE = re.compile(r'<a\b[^>]*href="([^"]*?/vi/cars/(\d+)[^"]*)"[^>]*>(.*?)</a>', re.S)
CURSOR_RE = re.compile(r'href="([^"]*[?&;]cursor=[^"]+)"')


def log(*a):
    if DEBUG:
        print(*a)


def get_kufar(url, params=None):
    """GET с повтором при 429 (слишком частые запросы)."""
    for attempt in range(3):
        r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        log(f"Куфар: статус {r.status_code}, {r.url}")
        if r.status_code == 429 and attempt < 2:
            time.sleep(20 * (attempt + 1))
            continue
        r.raise_for_status()
        return r


def parse_ad(href, ad_id, inner):
    text = html.unescape(re.sub(r"<[^>]+>", " ", inner))
    text = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"^(.*?)\s(?:(\d[\d ]*) р\.|(Договорная))\s*(.*)$", text, re.I)
    if not m:
        log("Куфар: не разобрал:", text[:120])
        return None
    title = m.group(1)
    price = m.group(2).strip() if m.group(2) else "Договорная"
    rest = m.group(4)
    if "Rio III" not in title:
        return None
    m2 = re.search(r"^(?:VIN\s+)?(.*?)\s*(\d{4}) г\.\s*(.*)$", rest)
    if not m2:
        log("Куфар: не разобрал хвост:", rest[:120])
        return None
    loc, year, tail = m2.group(1).strip(), m2.group(2), m2.group(3).strip()
    mm = re.search(r"(\d[\d ]*) км", tail)
    if mm:
        mileage = mm.group(1).strip() + " км"
        specs = tail[:mm.start()].strip()
    else:
        mileage, specs = "", tail
    link = html.unescape(href).split("?")[0]
    if link.startswith("/"):
        link = BASE + link
    return {
        "id": ad_id,
        "title": title.replace("есть видео", "").strip(),
        "price": price,
        "mileage": mileage,
        "year": year,
        "loc": loc,
        "specs": specs,
        "url": link,
    }


def fetch_all():
    """Возвращает {id: объявление}, новые сверху."""
    ads = {}
    first = True
    for slug in SLUGS:
        url, params = f"{BASE}/l/cars/{slug}", None
        for page in range(PAGES_PER_SLUG):
            if not first:
                time.sleep(4)  # пауза между запросами, иначе Куфар отвечает 429
            first = False
            try:
                r = get_kufar(url, params)
            except Exception as e:
                print(f"Куфар ({slug}, стр. {page + 1}): {e}", file=sys.stderr)
                break
            anchors = AD_RE.findall(r.text)
            log(f"Куфар ({slug}, стр. {page + 1}): ссылок на объявления {len(anchors)}")
            if not anchors:
                log("Куфар: начало ответа:", r.text[:400])
            for href, ad_id, inner in anchors:
                if ad_id in ads:
                    continue
                try:
                    ad = parse_ad(href, ad_id, inner)
                except Exception as e:
                    log("Куфар: пропуск объявления:", e)
                    continue
                if ad:
                    ads[ad_id] = ad
            m = CURSOR_RE.search(r.text)
            if not m:
                break
            url, params = urljoin(r.url, html.unescape(m.group(1))), None
    log(f"Куфар: всего объявлений Rio III {len(ads)}")
    return ads


def load_state():
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and data.get("_v") == 2:
        return data
    return None  # старый формат (список id) - начинаем заново


def save_state(state):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def send_tg(text):
    if DEBUG and not TG_TOKEN:
        print("[TG]", text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT_ID, "text": text},
        timeout=30,
    )
    r.raise_for_status()


def snapshot(ad):
    return {k: ad[k] for k in FIELDS}


def describe(ad):
    head = f"{ad['title']} {ad['year']}, {ad['loc']}"
    price = ad["price"] if ad["price"] == "Договорная" else f"{ad['price']} руб."
    line2 = " · ".join(x for x in (price, ad["mileage"]) if x)
    return "\n".join(x for x in (head, line2, ad["specs"], ad["url"]) if x)


def main():
    ads = fetch_all()
    if not ads:
        print("Куфар: данных нет, состояние не меняю")
        return

    state = load_state()
    if state is None:
        state = {"_v": 2, "ads": {i: snapshot(a) for i, a in ads.items()}}
        send_tg(f"✅ Мониторинг Kia Rio III запущен: запомнил {len(ads)} текущих "
                "объявлений. Дальше буду присылать новые и изменённые.")
        save_state(state)
        print(f"Базовый список: {len(ads)}")
        return

    known = state["ads"]
    sent = 0
    for ad_id, ad in reversed(list(ads.items())):  # старые первыми, новые внизу чата
        snap = snapshot(ad)
        old = known.get(ad_id)
        msg = None
        if old is None:
            msg = "🆕 " + describe(ad)
        else:
            changes = [
                f"{name}: {old.get(k, '—')} → {snap[k]}"
                for k, name in FIELDS.items()
                if old.get(k) != snap[k]
            ]
            if changes:
                msg = "✏️ Изменилось:\n" + "\n".join(changes) + "\n\n" + describe(ad)
        if msg:
            try:
                send_tg(msg)
                sent += 1
                time.sleep(1)
            except Exception as e:
                print(f"Telegram: {e}", file=sys.stderr)
                continue  # не запоминаем, попробуем в следующий раз
        known[ad_id] = snap
    save_state(state)
    print(f"Отправлено сообщений: {sent}")


if __name__ == "__main__":
    main()
