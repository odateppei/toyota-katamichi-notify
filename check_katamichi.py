#!/usr/bin/env python3
"""
トヨタレンタカー 片道GO 自動監視 -> LINE通知
全国・日程問わず新着リストをすべて通知する
"""
import json, os, hashlib, requests
from playwright.sync_api import sync_playwright

SITE_URL  = "https://toyota-rentacar.com/"
SEEN_FILE = "seen.json"
DEBUG     = os.environ.get("DEBUG", "false").lower() == "true"
SEED      = os.environ.get("SEED",  "false").lower() == "true"

LINE_TOKEN   = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

# ============================================================


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8-sig") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


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
                "raw":       raw,
                "departure": departure,
                "return":    ret,
                "period":    period,
                "car":       car,
                "phone":     phone,
            })

        browser.close()

    print(f"  取得: {len(listings)} 件")
    return listings


KINKI_KANTO_PREFS = {
    "大阪", "京都", "兵庫", "奈良", "滋賀", "和歌山",
    "東京", "神奈川", "埼玉", "千葉", "茨城", "栃木", "群馬",
}


def is_target_region(listing: dict) -> bool:
    return any(pref in listing["departure"] for pref in KINKI_KANTO_PREFS)


def listing_id(listing: dict) -> str:
    return hashlib.md5(listing["raw"][:300].encode()).hexdigest()


def send_line(message: str) -> str:
    """'ok' | 'quota' | 'error' を返す"""
    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {LINE_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]},
            timeout=15,
        )
        if r.status_code == 200:
            print("  LINE送信OK")
            return "ok"
        elif r.status_code == 429:
            print("  LINE送信エラー: 月次上限到達 → 既読にスキップ")
            return "quota"
        else:
            print(f"  LINE送信エラー: {r.status_code} / {r.text}")
            return "error"
    except Exception as e:
        print(f"  LINE送信例外: {e}")
        return "error"


def main():
    seen = load_seen()
    print(f"{'[シードモード] ' if SEED else ''}監視開始: {SITE_URL}")
    listings = fetch_listings()

    if SEED:
        # 現在の全リストを既読にセット（通知しない）
        for listing in listings:
            seen.add(listing_id(listing))
        save_seen(seen)
        print(f"シード完了: {len(listings)} 件を既読に設定（通知なし）")
        return

    # 通常モード: 未通知の新着だけ通知
    notified = 0
    failed = 0
    quota_skip = 0
    already_seen = 0
    skipped_region = 0
    for listing in listings:
        if not is_target_region(listing):
            skipped_region += 1
            continue
        lid = listing_id(listing)
        if lid not in seen:
            msg = (
                f"【片道GO 新着！】\n\n"
                f"出発: {listing['departure']}\n"
                f"返却: {listing['return']}\n"
                f"期間: {listing['period']}\n"
                f"車種: {listing['car']}\n"
                f"予約TEL: {listing['phone']}\n\n"
                f"-> 今すぐ電話予約:\n{SITE_URL}"
            )
            result = send_line(msg)
            if result == "ok":
                seen.add(lid)
                notified += 1
                print(f"  通知: {listing['departure'][:40]}")
            elif result == "quota":
                seen.add(lid)   # 月次上限 → 既読にして無限ループを防ぐ
                quota_skip += 1
            else:
                failed += 1     # 一時的エラー → 次回リトライ
        else:
            already_seen += 1

    save_seen(seen)
    print(f"完了 / 対象外地域スキップ: {skipped_region}件 / 既読スキップ: {already_seen}件 / 新着通知: {notified}件 / 月次上限スキップ: {quota_skip}件 / 送信失敗: {failed}件")


if __name__ == "__main__":
    main()
