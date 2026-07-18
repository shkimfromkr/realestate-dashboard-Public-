# -*- coding: utf-8 -*-
"""
매일 실행되는 데이터 갱신 스크립트.
1) 국토부 실거래가 API로 최근 N개월 거래 수집 (관심 단지의 전체 평형)
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

MOLIT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"


def norm(s):
    """띄어쓰기·괄호 차이를 무시하고 비교하기 위한 정규화"""
    return s.replace(" ", "").replace("(", "").replace(")", "")


def month_list(n):
    today = datetime.date.today()
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def fetch_trades():
    """관심 단지의 모든 평형 거래를 수집. 진단 정보도 함께 반환."""
    trades, all_names = [], set()
    target = norm(CONFIG["aptName"])
    diag = []
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
            diag.append(f"{ymd} 요청 실패: {e}")
            continue
        code = root.findtext(".//resultCode") or ""
        msg = root.findtext(".//resultMsg") or ""
        if code and code not in ("00", "000"):
            diag.append(f"{ymd} API 오류 [{code}] {msg}")
            continue
        for item in root.iter("item"):
            g = lambda tag: (item.findtext(tag) or "").strip()
            name = g("aptNm")
            all_names.add(name)
            if target not in norm(name):
                continue
            try:
                area = float(g("excluUseAr"))
                price = int(g("dealAmount").replace(",", ""))  # 만원
            except ValueError:
                continue
            trades.append({
                "date": f'{g("dealYear")}-{int(g("dealMonth")):02d}-{int(g("dealDay")):02d}',
                "area": area,
                "floor": g("floor"),
                "price": price,
            })
    trades.sort(key=lambda t: t["date"], reverse=True)

    # ---- 진단 로그 ----
    for d in diag:
        print(f"[molit] {d}", file=sys.stderr)
    if not trades:
        print(f"[molit] '{CONFIG['aptName']}' 매칭 0건!", file=sys.stderr)
        hint = sorted(n for n in all_names if "더샵" in n or "샵" in n)
        print(f"[molit] 참고 - 이 지역 '샵' 포함 단지명: {hint[:20]}", file=sys.stderr)
        print(f"[molit] 전체 수신 단지 수: {len(all_names)}", file=sys.stderr)
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
                title = (it["title"].replace("<b>", "").replace("</b>", "")
                         .replace("&quot;", '"').replace("&amp;", "&"))
                if it["link"] in seen:
                    continue
                seen.add(it["link"])
                items.append({"title": title, "link": it["link"],
                              "date": it.get("pubDate", "")[:16], "keyword": kw})
        except Exception as e:
            print(f"[news] {kw} 실패: {e}", file=sys.stderr)
    return items[:12]


def fetch_listings():
    try:
        from fetch_listings import get_listings
        return get_listings(CONFIG)
    except Exception as e:
        print(f"[listings] 건너뜀: {e}", file=sys.stderr)
        return {"available": False, "items": []}


def avg(xs):
    return round(sum(xs) / len(xs)) if xs else None


def won(n):
    eok, man = divmod(int(n), 10000)
    return f"{eok}억 {man:,}만" if man else f"{eok}억"


def build_alerts(trades, listings, prev):
    """알림은 alertAreas에 지정한 평형 기준으로 생성"""
    alerts = []
    watch = set(CONFIG.get("alertAreas", []))
    wt = [t for t in trades if int(t["area"]) in watch] if watch else trades

    prev_keys = {(t["date"], t.get("floor"), t["price"]) for t in prev.get("trades", [])}
    for t in [x for x in wt if (x["date"], x.get("floor"), x["price"]) not in prev_keys][:3]:
        alerts.append({"kind": "up", "tag": "신규 거래",
                       "text": f'{t["date"]} {int(t["area"])}㎡ {t["floor"]}층 {won(t["price"])} 실거래 등록'})

    cur_avg, prev_avg = avg([t["price"] for t in wt]), prev.get("avg6mo")
    if cur_avg and prev_avg and cur_avg != prev_avg:
        diff = cur_avg - prev_avg
        alerts.append({"kind": "up" if diff > 0 else "down",
                       "tag": f'평균 {"▲" if diff > 0 else "▼"}',
                       "text": f'{"·".join(map(str, sorted(watch)))}㎡ 6개월 평균 {won(prev_avg)} → {won(cur_avg)} ({diff:+,}만)'})

    if listings.get("available"):
        prev_n = prev.get("listings", {}).get("count")
        cur_n = len(listings["items"])
        if prev_n is not None and cur_n != prev_n:
            alerts.append({"kind": "note", "tag": "매물",
                           "text": f'등록 매물 {prev_n}건 → {cur_n}건 {"▲" if cur_n > prev_n else "▼"}'})
    return alerts, avg([t["price"] for t in wt])


def main():
    prev = {}
    if os.path.exists(DATA_PATH):
        prev = json.load(open(DATA_PATH, encoding="utf-8"))

    trades = fetch_trades()
    news = fetch_news()
    listings = fetch_listings()
    alerts, watch_avg = build_alerts(trades, listings, prev)

    data = {
        "asOf": datetime.date.today().isoformat(),
        "avg6mo": watch_avg,               # alertAreas 평형 기준 (알림 비교용)
        "trades": trades[:100],            # 전체 평형 포함 — 화면에서 평형별 선택
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
