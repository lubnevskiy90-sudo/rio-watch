#!/usr/bin/env python3
"""Мониторинг Kia Rio 3 (2011-2017, Минск, до 7500 $, без газа) на av.by и Куфар.
Новые объявления шлёт в Telegram. Запуск: python rio_watch.py [--debug]
Переменные окружения: TG_TOKEN, TG_CHAT_ID
"""
import html
import json
import os
import re
import sys
import time

import requests

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")

MAX_PRICE = 7500
YEAR_MIN, YEAR_MAX = 2011, 2017
GAS_RE = re.compile(r"пропан|метан|\bгбо\b|\bгаз\b|\bgas\b|\blpg\b", re.I)
MINSK_RE = re.compile(r"^\s*минск\b", re.I)  # не ловит "Минская обл."
DEBUG = "--debug" in sys.argv

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def log(*a):
    if DEBUG:
        print(*a)


def find_adverts(node, out):
    """Рекурсивно ищет в JSON словари, похожие на объявление av.by."""
    if isinstance(node, dict):
        if "publicUrl" in node and "price" in node:
            out.append(node)
        else:
            for v in node.values():
                find_adverts(v, out)
    elif isinstance(node, list):
        for v in node:
            find_adverts(v, out)


def fetch_avby():
    url = "https://cars.av.by/kia/rio"
    params = {
        "price_usd[max]": MAX_PRICE,
        "year[min]": YEAR_MIN,
        "year[max]": YEAR_MAX,
    }
    h = dict(HEADERS)
    h.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://av.by/",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-site",
    })
    s = requests.Session()
    s.get("https://av.by/", headers=h, timeout=30)  # получаем куки как браузер
    r = s.get(url, params=params, headers=h, timeout=30)
    log(f"av.by: статус {r.status_code}, {r.url}")
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        raise RuntimeError("av.by: не нашёл __NEXT_DATA__ (изменилась вёрстка?)")
    ads = []
    find_adverts(json.loads(m.group(1)), ads)
    log(f"av.by: найдено в JSON {len(ads)}")

    result = []
    for a in ads:
        try:
            price = (a.get("price") or {}).get("usd", {}).get("amount")
            year = a.get("year")
            loc = a.get("shortLocationName") or a.get("locationName") or ""
            url_ = a["publicUrl"]
            props = " ".join(
                str(p.get("value", "")) for p in a.get("properties", []) if isinstance(p, dict)
            )
            text = f"{props} {a.get('description', '')}"
            if "/kia/rio/" not in url_:
                continue
            if price is None or float(price) > MAX_PRICE:
                continue
            if year is None or not (YEAR_MIN <= int(year) <= YEAR_MAX):
                continue
            if not MINSK_RE.match(loc):
                continue
            if GAS_RE.search(text):
                continue
            mileage = next(
                (p.get("value") for p in a.get("properties", [])
                 if isinstance(p, dict) and p.get("name") == "mileage_km"),
                "",
            )
            result.append({
                "id": f"avby:{a.get('id', url_)}",
                "title": f"Kia Rio {year}",
                "price": f"{int(float(price))} $",
                "extra": f"{mileage} км" if mileage else "",
                "url": url_,
            })
        except Exception as e:  # одно кривое объявление не должно ломать всё
            log("av.by: пропуск объявления:", e)
    return result


def get_kufar(url, params):
    """GET с повтором при 429 (слишком частые запросы)."""
    for attempt in range(3):
        r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        log(f"Куфар: статус {r.status_code}, {r.url}")
        if r.status_code == 429 and attempt < 2:
            time.sleep(20 * (attempt + 1))
            continue
        r.raise_for_status()
        return r


def fetch_kufar():
    """Читает страницы поколений Rio III и Rio III Рестайлинг на auto.kufar.by."""
    result = []
    seen_ids = set()
    for slug in ("kia-rio-iii", "kia-rio-iii-restajling"):
        url = f"https://auto.kufar.by/l/cars/{slug}"
        params = {
            "cur": "USD",
            "prc": f"r:0,{MAX_PRICE}",
            "rgd": f"r:{YEAR_MIN},{YEAR_MAX}",
        }
        if slug != "kia-rio-iii":
            time.sleep(5)  # пауза между запросами, иначе Куфар отвечает 429
        try:
            r = get_kufar(url, params)
        except Exception as e:
            print(f"Куфар ({slug}): {e}", file=sys.stderr)
            continue  # не теряем то, что уже нашли на других страницах
        anchors = re.findall(
            r'<a\b[^>]*href="([^"]*?/vi/cars/(\d+)[^"]*)"[^>]*>(.*?)</a>',
            r.text, re.S,
        )
        log(f"Куфар ({slug}): найдено ссылок на объявления {len(anchors)}")
        if not anchors:
            log("Куфар: начало ответа:", r.text[:400])

        for href, ad_id, inner in anchors:
            if ad_id in seen_ids:
                continue
            seen_ids.add(ad_id)
            try:
                text = html.unescape(re.sub(r"<[^>]+>", " ", inner))
                text = re.sub(r"\s+", " ", text).strip()
                m = re.search(r"^(.*?)\s(\d[\d ]*) р\.\s*(.*)$", text)
                if not m:
                    log("Куфар: не разобрал:", text[:120])
                    continue
                title, price, rest = m.group(1), m.group(2).strip(), m.group(3)
                m2 = re.search(r"^(?:VIN\s+)?(.*?)\s*(\d{4}) г\.\s*(.*)$", rest)
                if not m2:
                    log("Куфар: не разобрал хвост:", rest[:120])
                    continue
                loc, year, tail = m2.group(1).strip(), int(m2.group(2)), m2.group(3)
                if not re.search(r"Rio III", title):
                    continue
                if not (YEAR_MIN <= year <= YEAR_MAX):
                    continue
                if not re.match(r"Минск(,|$)", loc):
                    continue
                if GAS_RE.search(text):
                    continue
                mm = re.search(r"(\d[\d ]*) км", tail)
                link = html.unescape(href).split("?")[0]
                if link.startswith("/"):
                    link = "https://auto.kufar.by" + link
                result.append({
                    "id": f"kufar:{ad_id}",
                    "title": f"{title.replace('есть видео', '').strip()} {year}, {loc}",
                    "price": f"{price} руб.",
                    "extra": f"{mm.group(1)} км" if mm else "",
                    "url": link,
                })
            except Exception as e:
                log("Куфар: пропуск объявления:", e)
    log(f"Куфар: подходит {len(result)}")
    return result


def load_seen():
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False)


def send_tg(text):
    if DEBUG and not TG_TOKEN:
        print("[TG]", text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": False},
        timeout=30,
    )
    r.raise_for_status()


def main():
    seen = load_seen()
    new = []
    errors = []
    for name, fn in (("av.by", fetch_avby), ("Куфар", fetch_kufar)):
        try:
            for ad in fn():
                if ad["id"] not in seen:
                    new.append(ad)
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"Ошибка {name}: {e}", file=sys.stderr)

    for ad in new:
        line2 = " · ".join(x for x in (ad["price"], ad["extra"]) if x)
        send_tg(f"🚗 {ad['title']}\n{line2}\n{ad['url']}")
        seen.add(ad["id"])
    save_seen(seen)

    if errors and not new:
        # Сообщаем об ошибках, чтобы не думать, что просто нет новых объявлений
        if os.environ.get("NOTIFY_ERRORS") == "1":
            send_tg("⚠️ rio_watch:\n" + "\n".join(errors))
    print(f"Новых объявлений: {len(new)}")


if __name__ == "__main__":
    main()
