#!/usr/bin/env python3
"""
トヨタレンタカー 片道GO 自動監視 -> LINE通知
"""
import json, os, re, hashlib, requests
from datetime import date
from playwright.sync_api import sync_playwright

SITE_URL  = "https://toyota-rentacar.com/"
SEEN_FILE = "seen.json"
DEBUG     = os.environ.get("DEBUG", "false").lower() == "true"

LINE_TOKEN   = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

CONDITIONS = [
    {
        "id":    "cond_0703",
        "label": "7/3 20時 京都or大阪 → 中目黒山手通り",
        "target_date":        (7, 3),
        "departure_keywords": ["京都", "大阪"],
        "return_keywords":    ["中目黒山手通り", "中目黒", "山手通り"],
    },
    {
        "id":    "cond_0705",
        "label": "7/5 20時 トヨタモビリティor西東京 → 高野 or 枚方市駅前",
        "target_date":        (7, 5),
        "departure_keywords": ["トヨタモビリティ", "西東京"],
        "return_keywords":    ["高野", "枚方市駅前", "枚方市"],
    },
]


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def date_in_text(raw: str, month: int, day: int) -> bool:
    """「7月3日」の直接マッチ + 「2026年6月24日 ～ 7月3日」形式の範囲マッチ"""
    target = date(2026, month, day)
    if f"{month}月{day}日" in raw:
        return True
    # 年プレフィックスあり・なし両方に対応: 例) "2026年6月24日 ～ 6月30日"
    pattern = r"(?:\d{4}年)?(\d{1,2})月\s*(\d{1,2})日\s*[〜~～\-]\s*(?:\d{4}年)?(\d{1,2})月\s*(\d{1,2})日"
    for m in re.finditer(pattern, raw):
        try:
            start = date(2026, int(m.group(1)), int(m.group(2)))
            end   = date(2026, int(m.group(3)), int(m.group(4)))
            if start <= target <= end:
                return True
        except ValueError:
            continue
    return False


def _texts(el, selector: str) -> list:
    return [n.inner_text().strip() for n in el.query_selector_all(selector)]


def fetch_listings() -> list:
    listings = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        ))
        page.goto(SITE_URL, wait_until="networkidle", timeout=60_000)

        if DEBUG:
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] debug_page.html 保存済み")

        for item in page.query_selector_all("li.service-item"):
            # 各フィールドは <p class="label-sp"> の次の <p> に入っている
            dep_ps  = _texts(item, ".service-item__shop-start p")
            ret_ps  = _texts(item, ".service-item__shop-return p")
            date_ps = _texts(item, ".service-item__date p")
            car_ps  = _texts(item, ".service-item__info__car-type p")
            tel_el  = item.query_selector(".service-item__reserve-tel")

            departure = dep_ps[-1]  if dep_ps  else ""
            ret       = ret_ps[-1]  if ret_ps  else ""
            period    = date_ps[-1] if date_ps else ""
            car       = car_ps[-1]  if car_ps  else ""
            phone     = tel_el.inner_text().strip() if tel_el else ""

            raw = f"{departure} | {ret} | {period} | {car} | {phone}"
            listings.append({
                "raw": raw,
                "departure": departure,
                "return":    ret,
                "period":    period,
                "car":       car,
                "phone":     phone,
            })

        browser.close()

    print(f"  取得: {len(listings)} 件")
    return listings


def matches(listing: dict, cond: dict) -> bool:
    m, d = cond["target_date"]
    return (
        date_in_text(listing["period"], m, d)
        and any(k in listing["departure"] for k in cond["departure_keywords"])
        and any(k in listing["return"]    for k in cond["return_keywords"])
    )


def listing_id(listing: dict, cond_id: str) -> str:
    return hashlib.md5((cond_id + listing["raw"][:300]).encode()).hexdigest()


def send_line(message: str):
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]},
        timeout=15,
    )
    r.raise_for_status()
    print("  LINE送信OK")


def main():
    seen = load_seen()
    print(f"監視開始: {SITE_URL}")
    listings = fetch_listings()

    notified = 0
    for listing in listings:
        for cond in CONDITIONS:
            if matches(listing, cond):
                lid = listing_id(listing, cond["id"])
                if lid not in seen:
                    seen.add(lid)
                    notified += 1
                    msg = (
                        f"【片道GO 新着！】\n"
                        f"条件: {cond['label']}\n\n"
                        f"出発: {listing['departure']}\n"
                        f"返却: {listing['return']}\n"
                        f"期間: {listing['period']}\n"
                        f"車種: {listing['car']}\n"
                        f"予約TEL: {listing['phone']}\n\n"
                        f"-> 今すぐ電話予約:\n{SITE_URL}"
                    )
                    send_line(msg)
                    print(f"  通知: {cond['label']}")

    save_seen(seen)
    print(f"完了 (新着通知: {notified}件)")


if __name__ == "__main__":
    main()
