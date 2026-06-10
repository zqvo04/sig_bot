"""
timeutil.py — 한국시간(KST) 공통 유틸 [TARGET: 15분봉 시그봇 / 15-MINUTE SIGBOT]
────────────────────────────────────────────────────────────────────
모든 표시·기록 시각을 KST(UTC+9)로 통일한다.
ortho_notify(텔레그램), ortho_notion(기록), ortho_resolver(판정)가 공유.
"""
from datetime import datetime, timezone, timedelta

# 한국 표준시 (DST 없음 → 고정 +09:00)
KST = timezone(timedelta(hours=9), name="KST")


def now_kst() -> datetime:
    """현재 시각 (tz-aware, KST)."""
    return datetime.now(KST)


def now_kst_iso() -> str:
    """Notion date 속성용 ISO-8601 문자열 (+09:00 오프셋 포함, 초 단위)."""
    return datetime.now(KST).isoformat(timespec="seconds")


def now_kst_str(fmt: str = "%Y-%m-%d %H:%M KST") -> str:
    """사람이 읽는 KST 시각 문자열 (기본: 텔레그램 표시용)."""
    return datetime.now(KST).strftime(fmt)


def to_kst(dt: datetime) -> datetime:
    """임의 datetime을 KST로 변환. naive면 UTC로 가정."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def kst_iso(dt: datetime) -> str:
    """임의 datetime → KST ISO-8601 문자열."""
    return to_kst(dt).isoformat(timespec="seconds")
