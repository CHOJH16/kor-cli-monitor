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
        notify("🟡 <b>깃허브 PAT 만료 안내</b>\n\n"
               "기존에 등록하셨던 PAT 토큰이 만료되었습니다.\n"
               "현재 워크플로는 기본 GITHUB_TOKEN으로 작동하여 수집 및 알림은 정상 지속됩니다.\n\n"
               "PAT 갱신을 원하시면:\n"
               "1. github.com/settings/personal-access-tokens 에서 Regenerate\n"
               "2. 저장소 Settings → Secrets → PAT_TOKEN 갱신")
        print("[정보] PAT 만료 확인됨 (기본 토큰으로 대체 작동)", file=sys.stderr)
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
               "1. github.com/settings/personal-access-tokens 접속\n"
               "2. 토큰 Regenerate\n"
               "3. 저장소 Settings → Secrets → PAT_TOKEN 갱신")


if __name__ == "__main__":
    main()
