"""ORTHO-4 비용 0 가상캠페인의 불변 계보·상태 계약.

이 모듈은 진입 알파를 바꾸지 않는다. 닫힌 봉 snapshot에서 나온 후보가 어떤
코드·설정·시장 상태에서 LIVE, ALPHA_SHADOW, EXEC_REJECT 또는 APERTURE로
기록됐는지를 결정하고, 비용 0 가정의 순 R 장부를 일관되게 유지한다.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional


ALPHA_VETO_REASONS = frozenset({
    "MACRO_FRESH", "FLOW_FLOOR", "CROWD", "TAKER",
})


def base_reason(reason: Optional[str]) -> str:
    """차단 문자열에서 안정적인 원인 코드를 추출한다.

    예: ``crowd(0.91)`` → ``CROWD``, ``EXPLORE:DROP_F`` → ``EXPLORE``.
    """
    raw = (reason or "").strip().upper()
    if not raw:
        return ""
    return raw.split(":", 1)[0].split("(", 1)[0].strip()


def classify_stage(blocked_by: Optional[str], *, materialized: bool) -> tuple[str, str, str]:
    """기록 단계·VETO 종류·정규화된 사유를 반환한다.

    ``materialized=False``는 엔진의 후보(ARMED) 단계이며, ``True``는 실제
    Notion 원장에 기록되는 상태다. 실행 불가 후보는 알파 Shadow와 의도적으로
    다른 VETO Class를 가져 성과 비교에 섞이지 않는다.
    """
    reason = base_reason(blocked_by)
    if not reason:
        return ("LIVE" if materialized else "ARMED", "NONE", "")
    if reason == "EXPLORE":
        return "APERTURE", "APERTURE", reason
    if reason in ALPHA_VETO_REASONS:
        return "ALPHA_SHADOW", "ALPHA", reason
    return "EXEC_REJECT", "EXECUTION", reason


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Mapping[str, Any], length: int = 24) -> str:
    """정렬된 JSON의 짧은 SHA-256 hex digest를 생성한다."""
    return hashlib.sha256(_canonical(dict(value)).encode("utf-8")).hexdigest()[:length]


def manifest_payload(config: Any) -> dict[str, Any]:
    """한 캠페인에서 진입 집합을 정의하는 고정 설정만 직렬화한다."""
    names = (
        "V4_ENABLED", "STRATEGY_ID", "COST_MODE", "CLOSED_CANDLES",
        "W_L", "P_EXT", "N_MEAN", "W_F", "P_FLOW", "LS_CROWD_VETO",
        "TAKER_VETO", "SPREAD_MAX_BPS", "SL_ATR_BUF", "RR_MIN", "RR_MAX",
        "T_MAX", "MAX_POS_DIR", "REGIME_ROUTER", "VOL_HI", "TREND_ER",
        "ROUTER_MODE", "ROUTER_SOFT_ER", "TP_REACH_K", "P_VOL",
        "MACRO_FRESH", "MACRO_FRESH_LB", "FLOW_FLOOR_PCT", "FLOW_CEIL_PCT",
        "POLARITIES", "RISK_PER_TRADE", "MAX_CONCURRENT_DIR",
    )
    payload: dict[str, Any] = {}
    for name in names:
        value = getattr(config, name, None)
        payload[name] = list(value) if isinstance(value, tuple) else value
    return payload


def config_hash(config: Any) -> str:
    return digest(manifest_payload(config), length=32)


def enrich_signal(sig: dict[str, Any], config: Any, *, materialized: bool = False,
                  blocked_by: Optional[str] = None, git_sha: Optional[str] = None,
                  workflow_run_id: Optional[str] = None) -> dict[str, Any]:
    """신호에 ORTHO-4의 재현 가능한 계보·비용 0 장부 필드를 부여한다.

    호출은 멱등적이다. 신호가 후보 단계에서는 ARMED이며, Notion에 적재하는
    순간에만 LIVE/ALPHA_SHADOW/EXEC_REJECT/APERTURE 상태로 전환된다.
    """
    if not getattr(config, "V4_ENABLED", False):
        return sig

    reason = blocked_by if blocked_by is not None else sig.get("blocked_by")
    stage, veto_class, veto_reason = classify_stage(reason, materialized=materialized)
    c_hash = config_hash(config)
    stable_identity = {
        "strategy_id": getattr(config, "STRATEGY_ID", "ORTHO-4.SIM0"),
        "symbol": sig.get("symbol"),
        "polarity": sig.get("polarity"),
        "direction": (sig.get("direction") or "").lower(),
        "signaled_at": sig.get("signaled_at"),
        "entry": sig.get("entry"),
    }
    market_snapshot = {
        **stable_identity,
        "l_pct": sig.get("l_pct"), "f_pct": sig.get("f_pct"),
        "s_state": sig.get("s_state"), "regime": sig.get("regime"),
        "macro_tag": sig.get("macro_tag"), "tp": sig.get("tp"),
        "sl": sig.get("sl"), "r_dist": sig.get("r_dist"),
        "rr": sig.get("rr"), "axis_vec": sig.get("axis_vec"),
    }
    sig.update({
        "v4_stage": stage,
        "decision_id": sig.get("decision_id") or digest(stable_identity),
        "strategy_id": getattr(config, "STRATEGY_ID", "ORTHO-4.SIM0"),
        "config_hash": c_hash,
        "git_sha": git_sha or sig.get("git_sha") or getattr(config, "GIT_SHA", "local"),
        "workflow_run_id": workflow_run_id or sig.get("workflow_run_id") or getattr(config, "WORKFLOW_RUN_ID", "local"),
        "snapshot_at": sig.get("snapshot_at") or sig.get("signaled_at"),
        "quote_at": sig.get("quote_at") or sig.get("signaled_at"),
        "market_snapshot_hash": sig.get("market_snapshot_hash") or digest(market_snapshot, length=32),
        "cost_mode": getattr(config, "COST_MODE", "SIM_COST_0"),
        "estimated_cost_r": 0.0,
        "realized_cost_r": 0.0,
        "net_rr": sig.get("rr"),
        "fill_state": ("SIM_FILLED" if stage == "LIVE" else
                       "REJECTED" if stage == "EXEC_REJECT" else "NOT_APPLICABLE"),
        "veto_class": veto_class,
        "veto_reason_v4": veto_reason,
        "entry_drift_r": 0.0,
    })
    return sig


def outcome_fields(pnl_r: Optional[float], config: Any) -> dict[str, Optional[float]]:
    """비용 0 캠페인의 결과 장부 수치를 생성한다."""
    if pnl_r is None:
        return {"gross_r": None, "net_r": None, "realized_cost_r": 0.0}
    gross = round(float(pnl_r), 8)
    if getattr(config, "COST_MODE", "SIM_COST_0") == "SIM_COST_0":
        return {"gross_r": gross, "net_r": gross, "realized_cost_r": 0.0}
    # REAL_COST는 이 구현 범위의 실행 모델이 아니므로 비용이 기록될 때까지 gross를 보존한다.
    return {"gross_r": gross, "net_r": gross, "realized_cost_r": None}
