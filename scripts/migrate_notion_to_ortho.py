#!/usr/bin/env python3
"""
migrate_notion_to_ortho.py — 기존 Notion 추적 DB를 ORTHO-3 양식으로 전환
════════════════════════════════════════════════════════════════════
사용자 지시: "기존 노션을 활용. 원래 기록용도 페이지의 기록을 전부 삭제하고
            새로운 양식에 맞도록 전체 수정."

이 스크립트가 하는 일 (대상 = 기존 NOTION_DATABASE_ID):
  1) DB 스키마에 ORTHO 신규 속성을 추가(없으면 생성):
       Engine, Polarity, RR, L_pct, F_pct, S_state, MacroTag, Reason
     ※ 기존 속성(Signal/Status/Symbol/Direction/Entry/TP/SL/R Dist/Bars Limit/
        MFE R/MAE R/Bars To Exit/Signaled At/Resolved At …)은 그대로 둔다.
        → DB가 "구+신 속성 합집합"이 되어, 레거시·ORTHO 양쪽 기록 모두 호환.
  2) DB의 모든 기존 페이지(기록)를 archived=true 로 삭제(휴지통 이동).

⚠️ 2)는 되돌리기 어려운 작업이다. 기본은 DRY-RUN(미삭제). 실제 삭제하려면
   --apply 플래그를 명시해야 한다.

사용법:
  export NOTION_TOKEN="secret_xxx"
  export NOTION_DATABASE_ID="기존_DB_ID"
  python scripts/migrate_notion_to_ortho.py            # 1) 스키마만 갱신 + 삭제 미리보기
  python scripts/migrate_notion_to_ortho.py --apply    # 2) 기록 전체 삭제까지 실행
"""
import os
import sys
import time
import requests

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"

# ORTHO 양식에서 추가로 필요한 속성 (기존에 없을 때만 생성)
NEW_PROPERTIES = {
    "Engine":   {"select": {"options": [
        {"name": "ORTHO-REV", "color": "blue"},
        {"name": "ORTHO-CONT", "color": "purple"},
    ]}},
    "Polarity": {"select": {"options": [
        {"name": "REV", "color": "blue"},
        {"name": "CONT", "color": "purple"},
    ]}},
    "MacroTag": {"select": {"options": [
        {"name": "UPLEG", "color": "green"},
        {"name": "DOWNLEG", "color": "red"},
        {"name": "FLAT", "color": "gray"},
    ]}},
    "RR":      {"number": {"format": "number"}},
    "L_pct":   {"number": {"format": "number"}},
    "F_pct":   {"number": {"format": "number"}},
    "S_state": {"rich_text": {}},
    "Reason":  {"rich_text": {}},
}


def _headers(token):
    return {"Authorization": f"Bearer {token}",
            "Notion-Version": VERSION, "Content-Type": "application/json"}


def ensure_schema(token, db_id):
    """기존 DB를 조회해 누락된 ORTHO 속성만 추가."""
    r = requests.get(f"{API}/databases/{db_id}", headers=_headers(token), timeout=20)
    if r.status_code != 200:
        sys.exit(f"❌ DB 조회 실패 {r.status_code}: {r.text[:300]}")
    existing = set(r.json().get("properties", {}).keys())
    to_add = {k: v for k, v in NEW_PROPERTIES.items() if k not in existing}
    if not to_add:
        print("✓ 스키마: ORTHO 속성 이미 존재 — 추가 없음")
        return
    r = requests.patch(f"{API}/databases/{db_id}", headers=_headers(token),
                       json={"properties": to_add}, timeout=20)
    if r.status_code == 200:
        print(f"✅ 스키마: 신규 속성 추가 — {', '.join(to_add.keys())}")
    else:
        sys.exit(f"❌ 스키마 갱신 실패 {r.status_code}: {r.text[:300]}")


def list_all_pages(token, db_id):
    """DB의 모든 (미보관) 페이지 ID 수집."""
    ids, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"{API}/databases/{db_id}/query",
                          headers=_headers(token), json=body, timeout=20)
        if r.status_code != 200:
            sys.exit(f"❌ 페이지 조회 실패 {r.status_code}: {r.text[:300]}")
        data = r.json()
        ids += [p["id"] for p in data.get("results", [])]
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return ids


def archive_pages(token, page_ids):
    """각 페이지를 archived=true 로 삭제(휴지통)."""
    ok = 0
    for pid in page_ids:
        r = requests.patch(f"{API}/pages/{pid}", headers=_headers(token),
                           json={"archived": True}, timeout=20)
        if r.status_code == 200:
            ok += 1
        else:
            print(f"  ⚠️ 삭제 실패 {pid}: {r.status_code}")
        time.sleep(0.34)   # Notion 레이트리밋(~3 req/s) 보호
    return ok


def main():
    token = os.getenv("NOTION_TOKEN", "").strip()
    db_id = os.getenv("NOTION_DATABASE_ID", "").strip()
    apply = "--apply" in sys.argv
    if not token or not db_id:
        sys.exit("❌ NOTION_TOKEN, NOTION_DATABASE_ID 환경변수가 필요합니다.")

    print(f"대상 DB: {db_id}")
    # 1) 스키마 갱신 (항상 수행 — 비파괴적)
    ensure_schema(token, db_id)

    # 2) 기록 삭제
    ids = list_all_pages(token, db_id)
    print(f"기존 기록(페이지): {len(ids)}건")
    if not ids:
        print("→ 삭제할 기록 없음. 완료.")
        return
    if not apply:
        print("※ DRY-RUN — 삭제하지 않음. 실제 삭제하려면 --apply 를 붙여 다시 실행.")
        return
    print("⚠️ 전체 삭제 진행…")
    n = archive_pages(token, ids)
    print(f"✅ 삭제 완료: {n}/{len(ids)}건 archived")


if __name__ == "__main__":
    main()
