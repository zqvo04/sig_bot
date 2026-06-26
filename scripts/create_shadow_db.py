#!/usr/bin/env python3
"""
create_shadow_db.py — FN 측정용 Shadow Notion DB를 1회 생성한다.
════════════════════════════════════════════════════════════════════
거부권(MACRO_FRESH·crowd·taker·spread)·추격컷·리스크캡이 막은 셋업을 적재할
"별도" Notion DB를 만든다. 라이브 신호 DB와 물리분리 → 라이브 통계 오염 0.

★ 반드시 봇과 *동일한* NOTION_TOKEN으로 실행할 것.
  그래야 그 인티그레이션이 생성된 DB에 자동으로 쓰기 권한을 갖는다(런타임에서 적재 가능).

전제: NOTION_TOKEN 인티그레이션이 부모 페이지에 연결(공유)되어 있어야 한다.
  Notion에서 아무 페이지나 열고 → ⋯ → "연결 추가(Add connections)" → 봇 인티그레이션 선택.
  그 페이지 URL 끝의 32자리 hex가 PARENT_PAGE_ID.

사용:
  NOTION_TOKEN=secret_xxx python scripts/create_shadow_db.py <PARENT_PAGE_ID>
  # 또는
  NOTION_TOKEN=secret_xxx NOTION_SHADOW_PARENT_PAGE_ID=xxx python scripts/create_shadow_db.py

출력: 생성된 database_id. 이 값을
  · GitHub → Secrets: NOTION_SHADOW_DB_ID = <database_id>
  · GitHub → Variables: ORTHO_SHADOW_LOG  = true   (측정 캠페인 시작)
로 등록하면 다음 cron부터 Shadow 적재·채점이 시작된다.
"""
import json
import os
import sys

import requests

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# 라이브 DB와 동일 스키마 + "Blocked By"(차단 카테고리) 1컬럼.
#   number/select/date/rich_text 타입은 log_signal/_parse가 쓰는 프로퍼티명과 정확히 일치해야 한다.
SCHEMA = {
    "Signal":      {"title": {}},
    "Status":      {"select": {"options": [
        {"name": "OPEN", "color": "yellow"},
        {"name": "WIN",  "color": "green"},
        {"name": "LOSS", "color": "red"},
    ]}},
    "Engine":      {"select": {"options": [{"name": "ORTHO-SHADOW", "color": "gray"}]}},
    "Polarity":    {"select": {"options": [
        {"name": "REV"}, {"name": "CONT"}, {"name": "BREAKOUT"},
    ]}},
    "Symbol":      {"select": {}},
    "Direction":   {"select": {"options": [
        {"name": "LONG", "color": "green"}, {"name": "SHORT", "color": "red"},
    ]}},
    # ★ Shadow 전용: 어떤 게이트가 이 셋업을 막았는가 (FN 분석의 핵심 축)
    "Blocked By":  {"select": {"options": [
        {"name": "MACRO_FRESH", "color": "orange"},
        {"name": "CHASE",       "color": "blue"},
        {"name": "CROWD",       "color": "purple"},
        {"name": "TAKER",       "color": "pink"},
        {"name": "SPREAD",      "color": "brown"},
        {"name": "SLOT",        "color": "gray"},
        {"name": "DIRCAP",      "color": "default"},
    ]}},
    "Entry":       {"number": {}},
    "TP":          {"number": {}},
    "SL":          {"number": {}},
    "R Dist":      {"number": {}},
    "Bars Limit":  {"number": {}},
    "RR":          {"number": {}},
    "L_pct":       {"number": {}},
    "F_pct":       {"number": {}},
    "S_state":     {"rich_text": {}},
    "MacroTag":    {"select": {}},
    "Reason":      {"rich_text": {}},
    "MFE R":       {"number": {}},
    "MAE R":       {"number": {}},
    "Bars To Exit": {"number": {}},
    "PnL %":       {"number": {}},
    "Signaled At": {"date": {}},
    "Resolved At": {"date": {}},
    "Note":        {"rich_text": {}},
}


def main():
    token = os.getenv("NOTION_TOKEN", "").strip()
    parent = (sys.argv[1] if len(sys.argv) > 1 else
              os.getenv("NOTION_SHADOW_PARENT_PAGE_ID", "")).strip()
    if not token:
        sys.exit("❌ NOTION_TOKEN 미설정 — 봇과 동일한 토큰으로 실행하세요.")
    if not parent:
        sys.exit("❌ PARENT_PAGE_ID 미지정 — 인자 또는 NOTION_SHADOW_PARENT_PAGE_ID 로 전달하세요.\n"
                 "   (해당 페이지에 봇 인티그레이션을 'Add connections'로 연결해 두어야 합니다.)")

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    body = {
        "parent": {"type": "page_id", "page_id": parent},
        "title": [{"type": "text", "text": {"content": "ORTHO Shadow Signals (FN 측정)"}}],
        "description": [{"type": "text", "text": {"content":
            "거부권/추격/리스크캡이 막은 셋업을 막힌 순간의 배리어로 적재 → resolver가 "
            "라이브와 동일 triple-barrier로 채점. 막힌 ExpR > 남긴 ExpR 이면 그 게이트는 FN 생성기."}}],
        "properties": SCHEMA,
    }
    r = requests.post(f"{API}/databases", headers=headers, json=body, timeout=20)
    if r.status_code != 200:
        sys.exit(f"❌ 생성 실패 {r.status_code}: {r.text[:500]}")
    db = r.json()
    db_id = db.get("id")
    url = db.get("url", "")
    print("✅ Shadow DB 생성 완료")
    print(f"   database_id : {db_id}")
    if url:
        print(f"   url         : {url}")
    print()
    print("다음을 등록하면 측정이 시작됩니다:")
    print(f"   [Secrets]   NOTION_SHADOW_DB_ID = {db_id}")
    print( "   [Variables] ORTHO_SHADOW_LOG    = true")
    print( "   [Variables] ORTHO_SHADOW_REASONS = MACRO_FRESH   # (선택) 캠페인 집중 시")


if __name__ == "__main__":
    main()
