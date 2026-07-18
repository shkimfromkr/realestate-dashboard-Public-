# -*- coding: utf-8 -*-
"""
네이버부동산 매물 수집 (실험적, 비공식).

주의:
- 공식 API가 아니라서 네이버가 구조를 바꾸면 언제든 동작이 멈출 수 있어요.
- 개인이 관심 단지 1~2곳을 하루 1회 조회하는 용도로만 쓰세요.
- config.json의 naverComplexNo를 채워야 동작합니다.
  단지 번호 찾는 법: 네이버부동산에서 단지 페이지를 열면 주소가
  new.land.naver.com/complexes/숫자 형태인데, 그 숫자가 단지 번호예요.
"""
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.land.naver.com/",
}


def get_listings(config):
    complex_no = config.get("naverComplexNo", "").strip()
    if not complex_no:
        return {"available": False, "items": []}

    url = f"https://m.land.naver.com/complex/getComplexArticleList"
    items, page = [], 1
    while page <= 3:  # 최대 3페이지만 — 과도한 요청 방지
        r = requests.get(url, params={
            "hscpNo": complex_no,
            "tradTpCd": "A1",      # A1 = 매매
            "order": "date_",
            "showR0": "N",
            "page": page,
        }, headers=HEADERS, timeout=15)
        r.raise_for_status()
        body = r.json().get("result", {})
        arts = body.get("list", [])
        if not arts:
            break
        for a in arts:
            spc = str(a.get("spc2", ""))  # 전용면적
            try:
                area_ok = int(float(spc)) in config["areas"]
            except ValueError:
                area_ok = True
            if not area_ok:
                continue
            items.append({
                "type": a.get("atclFetrDesc", "") or a.get("bildNm", ""),
                "areaName": a.get("atclNm", ""),
                "spec": f'{a.get("spc1","")}/{spc}㎡ {a.get("flrInfo","")}층',
                "price": a.get("prcInfo", ""),
                "date": a.get("cfmYmd", ""),
            })
        if not body.get("moreDataYn") == "Y":
            break
        page += 1
    return {"available": True, "items": items}
