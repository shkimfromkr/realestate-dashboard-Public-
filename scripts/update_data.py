# -*- coding: utf-8 -*-
"""
매일 실행되는 데이터 갱신 스크립트.
1) 국토부 실거래가 API로 최근 N개월 거래 수집 (관심 단지 · 평형 필터)
2) 네이버 뉴스 API로 규제 뉴스 수집
3) (선택) 네이버부동산 매물 수집 — 실패해도 나머지는 정상 동작
4) 직전 data.json과 비교해 알림 생성 후 data.json 저장
"""
import json, os, sys, datetime
import xml.etree.ElementTree as ET
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
DATA_PATH = os.path.join(ROOT, "data.json")

MOLIT_KEY = os.environ.get("MOLIT_API_KEY", "")
NAVER_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

MOLIT_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"


def month_list(n):
    today = datetime.date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def fetch_trades():
    """국토부 실거래가에서 관심 단지·평형 거래만 추린다."""
    trades = []
    for ymd in month_list(CONFIG.get("monthsBack", 6)):
        try:
            r = requests.get(MOLIT_URL, params={
                "serviceKey": MOLIT_KEY,
                "LAWD_CD": CONFIG["lawdCd"],
                "DEAL_YMD": ymd,
                "numOfRows": "2000",
                "pageNo": "1",
            }, timeout=30)
            root = ET.fromstring(r.content)
        except Exception as e:
            print(f"[molit] {ymd} 실패: {e}", file=sys.stderr)
            continue
        for item in root.iter("item"):
            g = lambda tag: (item.findtext(tag) or "").strip()
            name = g("aptNm")
            if CONFIG["aptName"] not in name:
                continue
            try:
                area = float(g("excluUseAr"))
            except ValueError:
                continue
            if int(area) not in CONFIG["areas"]:
                continue
            price = int(g("dealAmount").replace(",", ""))  # 만원
            trades.append({
                "date": f'{g("dealYear")}-{int(g("dealMonth")):02d}-{int(g("dealDay")):02d}',
                "area": area,
                "floor": g("floor"),
                "price": price,
            })
    trades.sort(key=lambda t: t["date"], reverse=True)
    return trades


def fetch_news():
    if not NAVER_ID:
        return []
    seen, items = set(), []
    for kw in CONFIG["newsKeywords"]:
        try:
            r = requests.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={"query": kw, "display": 5, "sort": "date"},
                headers={"X-Naver-Client-Id": NAVER_ID,
                         "X-Naver-Client-Secret": NAVER_SECRET},
                timeout=15,
            )
            for it in r.json().get("items", []):
                title = it["title"].replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&")
                if it["link"] in seen:
                    continue
                seen.add(it["link"])
                items.append({
                    "title": title,
                    "link": it["link"],
                    "date": it.get("pubDate", "")[:16],
                    "keyword": kw,
                })
        except Exception as e:
            print(f"[news] {kw} 실패: {e}", file=sys.stderr)
    return items[:12]


def fetch_listings():
    """매물 수집 (실험적). 실패는 전체를 막지 않는다."""
    try:
        from fetch_listings import get_listings
        return get_listings(CONFIG)
    except Exception as e:
        print(f"[listings] 건너뜀: {e}", file=sys.stderr)
        return {"available": False, "items": []}


def build_alerts(trades, listings, prev):
    alerts = []
    prev_trades = {(t["date"], t.get("floor"), t["price"]) for t in prev.get("trades", [])}
    new_trades = [t for t in trades if (t["date"], t.get("floor"), t["price"]) not in prev_trades]
    for t in new_trades[:3]:
        alerts.append({"kind": "up", "tag": "신규 거래",
                       "text": f'{t["date"]} {t["floor"]}층 {won(t["price"])} 실거래 등록'})
    # 6개월 평균 변동
    cur_avg = avg([t["price"] for t in trades])
    prev_avg = prev.get("avg6mo")
    if cur_avg and prev_avg and cur_avg != prev_avg:
        diff = cur_avg - prev_avg
        arrow = "▲" if diff > 0 else "▼"
        kind = "up" if diff > 0 else "down"
        alerts.append({"kind": kind, "tag": f"평균 {arrow}",
                       "text": f'6개월 평균 {won(prev_avg)} → {won(cur_avg)} ({diff:+,}만)'})
    # 매물 변동
    if listings.get("available"):
        prev_n = prev.get("listings", {}).get("count")
        cur_n = len(listings["items"])
        if prev_n is not None and cur_n != prev_n:
            arrow = "▲" if cur_n > prev_n else "▼"
            alerts.append({"kind": "note", "tag": "매물",
                           "text": f'등록 매물 {prev_n}건 → {cur_n}건 {arrow}'})
    return alerts


def avg(xs):
    return round(sum(xs) / len(xs)) if xs else None


def won(n):
    eok, man = divmod(int(n), 10000)
    return f"{eok}억 {man:,}만" if man else f"{eok}억"


def main():
    prev = {}
    if os.path.exists(DATA_PATH):
        prev = json.load(open(DATA_PATH, encoding="utf-8"))

    trades = fetch_trades()
    news = fetch_news()
    listings = fetch_listings()
    alerts = build_alerts(trades, listings, prev)

    data = {
        "asOf": datetime.date.today().isoformat(),
        "avg6mo": avg([t["price"] for t in trades]),
        "trades": trades[:20],
        "listings": {"available": listings.get("available", False),
                     "count": len(listings.get("items", [])),
                     "items": listings.get("items", [])[:20]},
        "news": news,
        "alerts": alerts if alerts else [
            {"kind": "note", "tag": "안정", "text": "지난 확인 이후 신규 거래·변동 없음"}],
    }
    json.dump(data, open(DATA_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"완료: 거래 {len(trades)}건, 뉴스 {len(news)}건, 알림 {len(alerts)}건")


if __name__ == "__main__":
    main()
