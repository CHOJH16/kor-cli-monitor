#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OECD 한국 경기선행지수(CLI) 모니터링 + 텔레그램 알림"""

import json
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

OECD_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.SDD.STES,DSD_STES@DF_CLI/KOR.M.LI...AA...H"
)
DATA_FILE = pathlib.Path("docs/data/kor_cli.json")
KST = timezone(timedelta(hours=9))
EPS = 1e-6
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
# 미세 변동 감지 기준 (소수점 둘째 자리 반올림 표시에 영향을 주는 0.005 이상 변동 시 감지)
REVISION_THRESHOLD = 0.005


def fetch_oecd():
    """OECD SDMX API에서 한국 CLI(진폭조정) 시계열을 가져온다."""
    headers = {
        "Accept": "application/vnd.sdmx.data+json",
        "User-Agent": USER_AGENT,
    }
    params = {
        "startPeriod": "1990-01",
        "dimensionAtObservation": "AllDimensions",
        "format": "jsondata",
    }

    last_err = None
    for attempt in range(1, 4):
        try:
            r = requests.get(OECD_URL, params=params, headers=headers, timeout=60)
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
                raise RuntimeError("OECD 응답에 관측 데이터가 없습니다.")

            return dict(sorted(series.items())), "OECD"
        except Exception as e:
            last_err = e
            print(f"[경고] OECD 시도 {attempt}/3 실패: {e}", file=sys.stderr)
            time.sleep(3 * attempt)

    raise RuntimeError(f"OECD 수집 최종 실패: {last_err}")


def fetch_fred():
    """OECD가 실패했을 때 쓰는 예비 경로(FRED). 키가 없으면 건너뛴다."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        print("[정보] FRED_API_KEY가 없어 FRED 조회를 건너뜁니다.")
        return None

    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "KORLOLITOAASTSAM",
                "api_key": key,
                "file_type": "json",
            },
            headers=headers,
            timeout=60,
        )
        r.raise_for_status()
        series = {}
        for o in r.json().get("observations", []):
            if o.get("value") == "." or not o.get("value"):
                continue
            series[o["date"][:7]] = round(float(o["value"]), 4)

        if not series:
            return None
        return dict(sorted(series.items())), "FRED"
    except Exception as e:
        print(f"[경고] FRED 수집 실패: {e}", file=sys.stderr)
        return None


def get_series():
    try:
        return fetch_oecd()
    except Exception as e:
        print(f"[경고] OECD 수집 실패: {e}", file=sys.stderr)
        alt = fetch_fred()
        if alt:
            print("[정보] FRED 예비 경로로 대체 수집 성공했습니다.")
            return alt
        raise RuntimeError("OECD 및 FRED 예비 경로 모두 수집에 실패했습니다.")


def classify(series):
    """전월 대비 방향으로 4가지 국면을 판정한다."""
    ks = list(series.keys())
    if len(ks) < 3:
        raise ValueError("시계열 데이터가 부족하여 국면을 판정할 수 없습니다.")

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

    return {
        "period": ks[-1],
        "value": cur,
        "prev": prev,
        "change": d_now,
        "prev_change": d_prev,
        "state": state,
        "icon": icon,
        "momentum": momentum,
        "zone": zone,
    }


def find_revisions(old, new, threshold=REVISION_THRESHOLD):
    """과거 수치가 소급 개정되었는지 찾는다."""
    out = []
    for p, v in old.items():
        if p in new and abs(new[p] - v) >= threshold:
            diff = round(new[p] - v, 4)
            out.append((p, v, new[p], diff))
    return sorted(out)[-8:]


def build_message(info, series, revisions, source, msg_type="new"):
    ks = list(series.keys())[-6:]
    y, m = info["period"].split("-")

    if msg_type == "revision":
        header = "🔄 <b>OECD 한국 경기선행지수 수치 개정</b>\n<i>기존 발표 수치에 변동이 발생했습니다.</i>"
    else:
        header = "📊 <b>OECD 한국 경기선행지수 신규 발표</b>"

    lines = [
        header,
        "",
        f"<b>기준월</b> : {y}년 {int(m)}월",
        f"<b>지수</b>   : {info['value']:.2f}  (전월 {info['prev']:.2f})",
        f"<b>전월비</b> : {info['change']:+.2f}p",
        "",
        f"<b>판정 : {info['state']} {info['icon']}</b>",
        f"모멘텀 : {info['momentum']} (전월 변화 {info['prev_change']:+.2f}p)",
        f"위치   : {info['zone']}",
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
        lines.append("🔄 <b>소급 개정 내역</b>")
        for p, o, n, d in revisions:
            diff_str = f"+{d:.2f}p" if d > 0 else f"{d:.2f}p"
            lines.append(f"· {p} : {o:.2f} → {n:.2f} ({diff_str})")
        lines.append("")

    if source != "OECD":
        lines.append(f"⚠️ 출처: {source} (OECD 직접 수집 실패로 대체)")

    lines.append(f"<i>{datetime.now(KST):%Y-%m-%d %H:%M} KST</i>")
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[경고] 텔레그램 환경변수(TOKEN/CHAT_ID)가 없어 알림을 건너뜁니다.")
        return

    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"텔레그램 전송 실패: {r.status_code} {r.text}")
    print("[정보] 텔레그램 전송 완료")


def main():
    force = os.environ.get("FORCE_NOTIFY", "").lower() == "true"

    series, source = get_series()
    info = classify(series)
    print(f"[정보] 최신 수집치: {info['period']} = {info['value']} ({info['state']}) [출처: {source}]")

    old_data = {}
    if DATA_FILE.exists():
        try:
            old_data = json.loads(DATA_FILE.read_text("utf-8"))
        except Exception as e:
            print(f"[경고] 기존 데이터 파일 읽기 실패: {e}")

    old_series = old_data.get("series", {})
    old_latest_period = max(old_series.keys()) if old_series else None

    # 예비 경로 수집 데이터가 저장된 데이터보다 현저히 과거인 경우 덮어쓰지 않음
    if old_series and max(series.keys()) < max(old_series.keys()):
        print(f"[경고] 수집된 데이터({max(series.keys())})가 기존 데이터({max(old_series.keys())})보다 과거 데이터입니다. 덮어쓰지 않습니다.")
        return

    first_run = not old_series
    
    # 1. 새 기준월 발표 여부 (예: 2026-08 -> 2026-09)
    is_new_period = bool(old_latest_period and info["period"] > old_latest_period)
    
    # 2. 동일 기준월 내 미세 수치 변동 여부 (잠정치 -> 확정치 변동 등)
    is_latest_changed = False
    if old_latest_period and info["period"] == old_latest_period and old_series:
        old_val = old_series.get(old_latest_period)
        if old_val is not None and abs(info["value"] - old_val) >= REVISION_THRESHOLD:
            is_latest_changed = True

    # 3. 과거 데이터 소급 개정 여부
    revisions = find_revisions(old_series, series)
    is_revised = bool(revisions)

    # 알림 발송 조건: 최초 실행, 새 기준월 발표, 최신치 변동, 소급 개정, 수동 강제 실행
    should_notify = first_run or is_new_period or is_latest_changed or is_revised or force

    # 파일 저장 (데이터 최신화)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    save_payload = {
        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": source,
        "latest": info,
        "series": series,
    }
    DATA_FILE.write_text(json.dumps(save_payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if first_run:
        send_telegram(
            "✅ <b>한국 경기선행지수 알리미 설치 완료</b>\n\n"
            "OECD 한국 경기선행지수 자동 모니터링을 시작합니다.\n"
            f"현재 최신치: {info['period']} 기준 {info['value']:.2f} "
            f"({info['state']} {info['icon']})\n"
            "새 기준월 발표 및 수치 소급 개정 시 자동으로 알려드립니다."
        )
    elif should_notify:
        msg_type = "revision" if (not is_new_period and (is_latest_changed or is_revised)) else "new"
        send_telegram(build_message(info, series, revisions, source, msg_type))
        print(f"[정보] 텔레그램 알림 전송 완료 (구분: {msg_type}, 기준월: {info['period']})")
    else:
        print(f"[정보] 신규 발표 및 수치 변동 없음 (최신 {info['period']} 유지). 알림 생략.")


if __name__ == "__main__":
    main()
