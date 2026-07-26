#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PAT 만료일을 점검하고, 임박하면 텔레그램으로 경고한다."""

import os
import sys
from datetime import datetime, timezone

import requests

WARN_DAYS = 21


def notify(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
    except Exception as e:
        print(f"[경고] 알림 전송 실패: {e}", file=sys.stderr)


def main():
    pat = os.environ.get("PAT_TOKEN", "").strip()
    if not pat:
        print("[정보] PAT 없음. 점검 생략.")
        return

    try:
        r = requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {pat}",
                     "Accept": "application/vnd.github+json"},
            timeout=30,
        )
    except Exception as e:
        print(f"[경고] GitHub 조회 실패: {e}", file=sys.stderr)
        return

    if r.status_code == 401:
        notify("🔴 <b>PAT 만료 또는 무효</b>\n\n"
               "깃허브 토큰이 더 이상 동작하지 않습니다.\n"
               "자동 실행이 60일 뒤 중단될 수 있습니다.\n\n"
               "재발급: github.com/settings/personal-access-tokens\n"
               "권한: Contents=RW, Actions=RW\n"
               "재등록: 저장소 Settings → Secrets → PAT_TOKEN")
        print("[오류] PAT 무효", file=sys.stderr)
        return

    exp = r.headers.get("github-authentication-token-expiration")
    if not exp:
        print("[정보] 만료일 없음(무기한). 양호.")
        return

    try:
        dt = datetime.fromisoformat(exp.replace(" UTC", "+00:00").strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        print(f"[정보] 만료일 해석 불가: {exp}")
        return

    left = (dt - datetime.now(timezone.utc)).days
    print(f"[정보] PAT 잔여 {left}일 (만료 {dt:%Y-%m-%d})")

    if left <= WARN_DAYS:
        notify(f"🟡 <b>PAT 만료 임박 (D-{left})</b>\n\n"
               f"만료일: {dt:%Y-%m-%d}\n"
               "이 날짜가 지나면 자동 실행이 서서히 중단됩니다.\n\n"
               "1. github.com/settings/personal-access-tokens 접속\n"
               "2. kor-cli-keepalive → Regenerate token\n"
               "3. 저장소 Settings → Secrets → PAT_TOKEN 갱신")


if __name__ == "__main__":
    main()
