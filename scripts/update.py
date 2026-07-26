#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OECD 한국 경기선행지수(CLI) 모니터링 + 텔레그램 알림"""

import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import requests

OECD_URL = ("https://sdmx.oecd.org/public/rest/data/"
            "OECD.SDD.STES,DSD_STES@DF_CLI/KOR.M.LI...AA...H")
DATA_FILE = pathlib.Path("docs/data/kor_cli.json")
KST = timezone(timedelta(hours=9))
EPS = 1e-6


def fetch_oecd():
    """OECD SDMX API에서 한국 CLI(진폭조정) 시계열을 가져온다."""
    r = requests.get(
        OECD_URL,
        params={"startPeriod": "1990-01",
                "dimensionAtObservation": "AllDimensions",
                "format": "jsondata"},
        headers={"Accept": "application/vnd.sdmx.data+json"},
        timeout=60,
    )
    r.raise_for_status()
    js = r.json()

    dims = js["data"]["structures"][0]["dimensions"]["observation"]
    tpos = next(i for i, d in enumerate(dims) if d["id"] == "TIME_PERIOD")
    periods = [v["id"] for v in dims[tpos]["values"]]

    series = {}
    for key, val in js["data"]["dataSets"][0]["observations"].items():
        if val[0] is None:
            continue
        series[periods[int(key.split(":")[tpos])]] = round(float(val[0]), 4)

    if not series:
        raise RuntimeError("OECD 응답에 데이터가 없습니다.")
    return dict(sorted(series.items())), "OECD"


def fetch_fred():
    """OECD가 실패했을 때 쓰는 예비 경로(FRED). 키가 없으면 건너뛴다."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return None
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": "KORLOLITOAASTSAM",
                "api_key": key, "file_type": "json"},
        timeout=60,
    )
    r.raise_for_status()
    series = {}
    for o in r.json()["observations"]:
        if o["value"] == ".":
            continue
        series[o["date"][:7]] = round(float(o["value"]), 4)
    if not series:
        return None
    return dict(sorted(series.items())), "FRED"


def get_series():
    try:
        return fetch_oecd()
    except Exception as e:
        print(f"[경고] OECD 수집 실패: {e}", file=sys.stderr)
        alt = fetch_fred()
        if alt:
            print("[정보] FRED 예비 경로로 대체했습니다.")
            return alt
        raise


def classify(series):
    """전월 대비 방향으로 4가지 국면을 판정한다."""
    ks = list(series.keys())
    cur, prev, prev2 = series[ks[-1]], series[ks[-2]], series[ks[-3]]
    d_now = round(cur - prev, 4)
    d_prev = round(prev - prev2, 4)

    if d_now > EPS and d_prev > EPS:
        state, icon = "상승 유지", "📈"
    elif d_now > EPS:
        state, icon = "상승 반전", "🔄📈"
    elif d_now < -EPS and d_prev < -EPS:
        state, icon = "하락 유지", "📉"
    elif d_now < -EPS:
        state, icon = "하락 반전", "🔄📉"
    else:
        state, icon = "보합", "➖"

    momentum = "가속" if abs(d_now) > abs(d_prev) + EPS else "둔화"
    zone = "기준선(100) 상회" if cur >= 100 else "기준선(100) 하회"

    return {"period": ks[-1], "value": cur, "prev": prev,
            "change": d_now, "prev_change": d_prev,
            "state": state, "icon": icon,
            "momentum": momentum, "zone": zone}


def find_revisions(old, new):
    """과거 수치가 소급 개정되었는지 찾는다."""
    out = []
    for p, v in old.items():
        if p in new and abs(new[p] - v) > 0.0005:
            out.append((p, v, new[p]))
    return sorted(out)[-6:]


def build_message(info, series, revisions, source):
    ks = list(series.keys())[-6:]
    y, m = info["period"].split("-")

    lines = [
        "📊 <b>OECD 한국 경기선행지수 신규 발표</b>",
        "",
        f"<b>기준월</b> : {y}년 {int(m)}월",
        f"<b>지수</b>   : {info['value']:.2f}  (전월 {info['prev']:.2f})",
        f"<b>전월비</b> : {info['change']:+.2f}p",
        "",
        f"<b>판정 : {info['state']} {info['icon']}</b>",
        f"모멘텀 : {info['momentum']} (전월 변화 {info['prev_change']:+.2f}p)",
        f"위치 : {info['zone']}",
        "",
        "<b>최근 6개월</b>",
        "<pre>",
    ]
    prev_v = None
    for p in ks:
        v = series[p]
        diff = "     -" if prev_v is None else f"{v - prev_v:+7.2f}"
        lines.append(f"{p}  {v:7.2f} {diff}")
        prev_v = v
    lines.append("</pre>")

    if revisions:
        lines.append("🔄 <b>과거치 개정</b>")
        for p, o, n in revisions:
            lines.append(f"· {p} : {o:.2f} → {n:.2f}")
        lines.append("")

    if source != "OECD":
        lines.append(f"⚠️ 출처: {source} (OECD 직접 수집 실패)")

    lines.append(f"<i>{datetime.now(KST):%Y-%m-%d %H:%M} KST</i>")
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text,
              "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"텔레그램 전송 실패: {r.status_code} {r.text}")
    print("[정보] 텔레그램 전송 완료")


def main():
    force = os.environ.get("FORCE_NOTIFY", "").lower() == "true"

    series, source = get_series()
    info = classify(series)
    print(f"[정보] 최신 {info['period']} = {info['value']} ({info['state']})")

    old_series, first_run = {}, True
    if DATA_FILE.exists():
        try:
            old_series = json.loads(DATA_FILE.read_text("utf-8")).get("series", {})
            first_run = not old_series
        except Exception:
            pass

    old_latest = max(old_series) if old_series else None
    is_new = info["period"] != old_latest
    revisions = find_revisions(old_series, series)

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps({
        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": source,
        "latest": info,
        "series": series,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    if first_run:
        send_telegram(
            "✅ <b>설치 완료</b>\n\nOECD 한국 경기선행지수 감시를 시작합니다.\n"
            f"현재 최신치는 {info['period']} 기준 {info['value']:.2f} "
            f"({info['state']} {info['icon']}) 입니다.\n"
            "새 발표가 나오면 자동으로 알려드립니다."
        )
    elif is_new or force:
        send_telegram(build_message(info, series, revisions, source))
    else:
        print(f"[정보] 신규 발표 없음 (최신 {info['period']} 유지). 알림 생략.")


if __name__ == "__main__":
    main()
