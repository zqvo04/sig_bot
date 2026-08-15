"""
ortho_notion.py — ORTHO 신호 Notion 기록/조회/채점연동 [TARGET: 15분봉]
════════════════════════════════════════════════════════════════════
기존 추적 DB(NOTION_DATABASE_ID, ORTHO 양식으로 마이그레이션됨)에 가상 신호를
기록하고, 채점기가 OPEN을 조회/업데이트한다. 알림(ON/OFF)과 무관하게 항상 동작.

DB 스키마(ORTHO 양식):
  Signal(title) Status Engine Polarity Symbol Direction
  Entry TP SL "R Dist" "Bars Limit" RR L_pct F_pct S_state MacroTag Reason
  "MFE R" "MAE R" "Bars To Exit" "Signaled At" "Resolved At" Note

Shadow DB(FN 측정, 별도 NOTION_SHADOW_DB_ID): 위 스키마 + "Blocked By"(select) 1컬럼.
  log_signal(sig, database_id=..., status=..., blocked_by=...)로 동일 함수 재사용.
  거부된 셋업을 막힌 순간의 배리어로 적재 → resolver가 같은 채점기로 would-be WIN/LOSS.
"""
import json
import logging
from typing import Optional

import requests

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import ortho_config as oc
import ortho_v4 as v4
import timeutil

logger = logging.getLogger("ortho.notion")
_API = "https://api.notion.com/v1"
_T = 15

# 스캘핑 피처/조리개 컬럼 (측정용). ensure_schema가 두 DB에 멱등 보장 → log_signal이 안전 적재.
_EXTRA_PROPS = {
    "OBI":         {"number": {}},        # 호가 불균형 [-1,+1]
    "Taker Slope": {"number": {}},        # CVD 가속(매수비율 기울기)
    "Taker Net":   {"number": {}},        # Stage 2.2 순-테이커압력 평균 [-1,+1] — 측정 전용
    "Funding %":   {"number": {}},        # funding 백분위
    "Regime Age":  {"number": {}},        # F1 레짐 나이(전환 후 4h봉 수) — 측정 전용
    "VWAP Dev":    {"number": {}},        # Stage 2.1 VWAP 이격도(ATR정규화) — 측정 전용
    "Vol %":       {"number": {}},        # rec1 거래량 백분위(pace 반영 가능) — 측정 전용
    "CVD Div":     {"number": {}},        # rec2 가격-CVD 다이버전스(±) — 측정 전용
    "Vol Grade":   {"select": {"options": [
        {"name": "A", "color": "green"}, {"name": "B", "color": "yellow"},
        {"name": "C", "color": "gray"},
    ]}},                           # 거래량 확신도 등급 A/B/C (informational)
    "Axis Vec":    {"rich_text": {}},     # 넓은 조리개 연속 축벡터(JSON)
    "Blocked By":  {"select": {"options": [
        {"name": "MACRO_FRESH", "color": "orange"}, {"name": "FLOW_FLOOR", "color": "yellow"},
        {"name": "CHASE", "color": "blue"}, {"name": "CROWD", "color": "purple"},
        {"name": "TAKER", "color": "pink"}, {"name": "SPREAD", "color": "brown"},
        {"name": "SLOT", "color": "gray"}, {"name": "DIRCAP", "color": "default"},
        {"name": "EXPLORE:DROP_L", "color": "blue"}, {"name": "EXPLORE:DROP_F", "color": "blue"},
        {"name": "EXPLORE:DROP_S", "color": "blue"}, {"name": "EXPLORE:UNIVERSE", "color": "blue"},
    ]}},                           # Shadow 차단/조리개 카테고리(라이브엔 무해)
    # ORTHO-4 계보·상태·비용 0 성과 원장. 별도 전용 필드를 써 Note 파싱을 제거한다.
    "V4 Stage":              {"select": {"options": [
        {"name": "ARMED", "color": "blue"}, {"name": "LIVE", "color": "green"},
        {"name": "ALPHA_SHADOW", "color": "purple"}, {"name": "EXEC_REJECT", "color": "gray"},
        {"name": "APERTURE", "color": "yellow"},
    ]}},
    "Decision ID":           {"rich_text": {}},
    "Strategy ID":           {"rich_text": {}},
    "Git SHA":               {"rich_text": {}},
    "Config Hash":           {"rich_text": {}},
    "Workflow Run ID":       {"rich_text": {}},
    "Snapshot At":           {"date": {}},
    "Market Snapshot Hash":  {"rich_text": {}},
    "Quote At":              {"date": {}},
    "Cost Mode":             {"select": {"options": [
        {"name": "SIM_COST_0", "color": "green"}, {"name": "REAL_COST", "color": "red"},
    ]}},
    "Estimated Cost R":      {"number": {}},
    "Realized Cost R":       {"number": {}},
    "Gross R":               {"number": {}},
    "Net R":                 {"number": {}},
    "Net RR":                {"number": {}},
    "Fill State":            {"select": {"options": [
        {"name": "NOT_APPLICABLE", "color": "gray"}, {"name": "SIM_FILLED", "color": "green"},
        {"name": "NOT_FILLED", "color": "yellow"}, {"name": "EXPIRED", "color": "orange"},
        {"name": "REJECTED", "color": "red"},
    ]}},
    "Veto Class":            {"select": {"options": [
        {"name": "NONE", "color": "green"}, {"name": "ALPHA", "color": "purple"},
        {"name": "EXECUTION", "color": "gray"}, {"name": "APERTURE", "color": "yellow"},
    ]}},
    "Veto Reason V4":        {"rich_text": {}},
    "Entry Drift R":         {"number": {}},
    "Risk Budget":           {"number": {}},
}
_EXTRA_KEYS = ("OBI", "Taker Slope", "Taker Net", "Funding %", "Regime Age", "VWAP Dev",
               "Vol %", "CVD Div", "Vol Grade", "Axis Vec",
               "V4 Stage", "Decision ID", "Strategy ID", "Git SHA", "Config Hash",
               "Workflow Run ID", "Snapshot At", "Market Snapshot Hash", "Quote At",
               "Cost Mode", "Estimated Cost R", "Realized Cost R", "Gross R", "Net R",
               "Net RR", "Fill State", "Veto Class", "Veto Reason V4", "Entry Drift R",
               "Risk Budget")   # 스키마 미보장 DB 재시도에서 제거 대상


def ensure_schema(database_id: Optional[str] = None) -> bool:
    """DB에 측정용 컬럼을 멱등 추가(PATCH /databases). 이미 있으면 무변경. 권한/네트워크 실패는 경고만.
    봇 토큰은 페이지를 쓰는 통합이라 자기 DB엔 스키마 편집권 보유 → 라이브 DB는 보장 성공이 정상."""
    db = database_id or oc.NOTION_DATABASE_ID
    if not (oc.NOTION_TOKEN and db):
        return False
    try:
        r = requests.patch(f"{_API}/databases/{db}", headers=_h(),
                           json={"properties": _EXTRA_PROPS}, timeout=_T)
        if r.status_code == 200:
            logger.info(f"[notion] 스키마 보장 OK {db[:8]}…")
            return True
        logger.warning(f"[notion] 스키마 보장 실패 {r.status_code}: {r.text[:160]}")
    except Exception as e:
        logger.warning(f"[notion] 스키마 보장 예외: {e}")
    return False


def enabled() -> bool:
    # 설정 모듈 import 시점에 계산된 플래그 대신 현재 자격증명을 본다. 테스트·재시도·
    # 런타임 초기화 순서에서도 update_outcome이 실제 연결 상태를 정확히 반영한다.
    return bool(oc.NOTION_TOKEN and oc.NOTION_DATABASE_ID)


def _h():
    return {"Authorization": f"Bearer {oc.NOTION_TOKEN}",
            "Notion-Version": oc.NOTION_VERSION, "Content-Type": "application/json"}


def _title(s):  return {"title": [{"text": {"content": str(s)[:1900]}}]}
def _txt(s):    return {"rich_text": [{"text": {"content": str(s)[:1900]}}]} if s not in (None, "") else {"rich_text": []}
def _sel(s):    return {"select": {"name": str(s)[:100]}} if s not in (None, "") else {"select": None}
def _date(iso): return {"date": {"start": iso}} if iso else {"date": None}
def _num(x):
    try:
        return {"number": (round(float(x), 8) if x is not None else None)}
    except (TypeError, ValueError):
        return {"number": None}

def _p_num(p): return (p or {}).get("number")
def _p_sel(p):
    s = (p or {}).get("select"); return s.get("name") if s else None
def _p_date(p):
    d = (p or {}).get("date"); return d.get("start") if d else None


# ── INSERT ────────────────────────────────────────────────────────
def log_signal(sig: dict, database_id: Optional[str] = None,
               status: str = "OPEN", blocked_by: Optional[str] = None) -> Optional[str]:
    """신호 1건을 Notion에 적재. database_id 미지정=라이브 DB(현행).
    blocked_by 지정 시 Shadow 기록(별도 DB) — Engine=ORTHO-SHADOW, "Blocked By" 컬럼 채움."""
    db = database_id or oc.NOTION_DATABASE_ID
    if not (oc.NOTION_TOKEN and db):
        return None
    try:
        # 엔진의 ARMED 후보를 실제 원장 상태로 전환한다. 섀도우 사유는 명시 인자 우선으로
        # 받아 LIVE/ALPHA_SHADOW/EXEC_REJECT/APERTURE 분류가 호출 지점과 무관하게 일관된다.
        blocked_by = blocked_by if blocked_by is not None else sig.get("blocked_by")
        sig = v4.enrich_signal(dict(sig), oc, materialized=True, blocked_by=blocked_by)
        d = (sig.get("direction") or "").upper()
        is_shadow = bool(blocked_by)
        title = (f"{'🌑 ' if is_shadow else ''}{sig['symbol']} {d} · {sig['polarity']} · "
                 f"RG{sig.get('regime','OFF')} · RR{sig.get('rr','?')} · {sig.get('macro_tag','?')}"
                 f"{(' · BLK:'+blocked_by) if is_shadow else ''}")
        props = {
            "Signal":      _title(title),
            "Status":      _sel(status),
            "Engine":      _sel("ORTHO-SHADOW" if is_shadow else f"ORTHO-{sig['polarity']}"),
            "Polarity":    _sel(sig["polarity"]),
            "Symbol":      _sel(sig["symbol"]),
            "Direction":   _sel(d),
            "Entry":       _num(sig["entry"]),
            "TP":          _num(sig["tp"]),
            "SL":          _num(sig["sl"]),
            "R Dist":      _num(sig["r_dist"]),
            "Bars Limit":  _num(sig["bars_limit"]),
            "RR":          _num(sig.get("rr")),
            "L_pct":       _num(sig.get("l_pct")),
            "F_pct":       _num(sig.get("f_pct")),
            "S_state":     _txt(sig.get("s_state")),
            "MacroTag":    _sel(sig.get("macro_tag")),
            "Reason":      _txt(sig.get("reason")),
            "Signaled At": _date(sig.get("signaled_at") or timeutil.now_kst_iso()),
            # C-1 등가-R 사이징 기록(기존 Note 칼럼 재사용 — 스키마 변경 불필요).
            "Note":        _txt(f"RG={sig.get('regime','OFF')} "
                                f"size={sig.get('size')} ({sig.get('notional')}U) "
                                f"risk={sig.get('risk_quote')}U=1R({sig.get('risk_pct')}%) "
                                f"| V4={sig.get('v4_stage','OFF')} {sig.get('cost_mode','')} "
                                f"cfg={sig.get('config_hash','')[:8]} "),
        }
        if oc.V4_ENABLED:
            props.update({
                "V4 Stage":             _sel(sig.get("v4_stage")),
                "Decision ID":          _txt(sig.get("decision_id")),
                "Strategy ID":          _txt(sig.get("strategy_id")),
                "Git SHA":              _txt(sig.get("git_sha")),
                "Config Hash":          _txt(sig.get("config_hash")),
                "Workflow Run ID":      _txt(sig.get("workflow_run_id")),
                "Snapshot At":          _date(sig.get("snapshot_at")),
                "Market Snapshot Hash": _txt(sig.get("market_snapshot_hash")),
                "Quote At":             _date(sig.get("quote_at")),
                "Cost Mode":            _sel(sig.get("cost_mode")),
                "Estimated Cost R":     _num(sig.get("estimated_cost_r")),
                "Realized Cost R":      _num(sig.get("realized_cost_r")),
                "Gross R":              _num(None),
                "Net R":                _num(None),
                "Net RR":               _num(sig.get("net_rr")),
                "Fill State":           _sel(sig.get("fill_state")),
                "Veto Class":           _sel(sig.get("veto_class")),
                "Veto Reason V4":       _txt(sig.get("veto_reason_v4")),
                "Entry Drift R":        _num(sig.get("entry_drift_r")),
                "Risk Budget":          _num(sig.get("risk_quote")),
            })
        if is_shadow:
            props["Blocked By"] = _sel(blocked_by)   # Shadow/조리개 카테고리 컬럼
        # 스캘핑 미시구조 피처(모든 신호) + 조리개 연속벡터(EXPLORE 행) — 측정용 컬럼.
        if oc.SCALP_FEATS:
            props["OBI"]         = _num(sig.get("obi"))
            props["Taker Slope"] = _num(sig.get("taker_slope"))
            props["Taker Net"]   = _num(sig.get("taker_net"))     # Stage 2.2 CVD 원재료
            props["Funding %"]   = _num(sig.get("funding_pct"))
        if oc.REGIME_AGE:                            # F1 측정 컬럼(라이브+Shadow 공통, 게이트 아님)
            props["Regime Age"]  = _num(sig.get("macro_age"))
        if oc.VWAP_DEV:                              # Stage 2.1 측정 컬럼(게이트 아님)
            props["VWAP Dev"]    = _num(sig.get("vwap_dev"))
        if oc.VOL_PCT:                               # rec1 거래량 백분위(측정)
            props["Vol %"]       = _num(sig.get("vol_pct"))
        if oc.CVD_DIV:                               # rec2 가격-CVD 다이버전스(측정)
            props["CVD Div"]     = _num(sig.get("cvd_div"))
        if oc.VOL_CONF and sig.get("vol_grade"):     # 거래량 확신도 등급(informational)
            props["Vol Grade"]   = _sel(sig.get("vol_grade"))
        if sig.get("axis_vec"):
            props["Axis Vec"] = _txt(json.dumps(sig["axis_vec"], separators=(",", ":")))
        r = requests.post(f"{_API}/pages", headers=_h(),
                          json={"parent": {"database_id": db}, "properties": props}, timeout=_T)
        # 라이브 안전판: 스키마 미보장 DB가 신규 컬럼을 거부(400)하면 추가컬럼만 빼고 1회 재시도.
        if r.status_code != 200 and ("is not a property" in r.text
                                     or any(k in r.text for k in _EXTRA_KEYS)):
            for k in _EXTRA_KEYS:
                props.pop(k, None)
            r = requests.post(f"{_API}/pages", headers=_h(),
                              json={"parent": {"database_id": db}, "properties": props}, timeout=_T)
        if r.status_code == 200:
            pid = r.json().get("id")
            tag = f"🌑shadow[{blocked_by}]" if is_shadow else "✅ 기록"
            logger.info(f"[notion] {tag} {sig['symbol']} {sig['polarity']} {d} → {pid}")
            return pid
        logger.error(f"[notion] ❌ 기록 실패 {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[notion] ❌ 기록 예외: {e}")
    return None


# ── OPEN 조회 (채점기) ─────────────────────────────────────────────
def _parse(page):
    try:
        p = page.get("properties", {})
        return {
            "page_id": page.get("id"),
            "symbol": _p_sel(p.get("Symbol")),
            "polarity": _p_sel(p.get("Polarity")),
            "direction": (_p_sel(p.get("Direction")) or "").lower(),
            "entry": _p_num(p.get("Entry")), "tp": _p_num(p.get("TP")),
            "sl": _p_num(p.get("SL")), "r_dist": _p_num(p.get("R Dist")),
            "bars_limit": _p_num(p.get("Bars Limit")),
            "signaled_at": _p_date(p.get("Signaled At")),
            "cost_mode": _p_sel(p.get("Cost Mode")),
            "v4_stage": _p_sel(p.get("V4 Stage")),
            "blocked_by": _p_sel(p.get("Blocked By")),   # Shadow 행에만 존재(라이브=None)
        }
    except Exception as e:
        logger.warning(f"[notion] 파싱 실패: {e}")
        return None


def query_open(limit=None, database_id: Optional[str] = None):
    """Status=OPEN 신호 조회. database_id 미지정=라이브 DB. Shadow DB도 같은 함수로 조회."""
    db = database_id or oc.NOTION_DATABASE_ID
    if not (oc.NOTION_TOKEN and db):
        return []
    cap = limit or oc.RESOLVER_MAX_OPEN_PER_RUN
    out, cursor = [], None
    try:
        while True:
            body = {"filter": {"property": "Status", "select": {"equals": "OPEN"}},
                    "sorts": [{"property": "Signaled At", "direction": "ascending"}],
                    "page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            r = requests.post(f"{_API}/databases/{db}/query",
                              headers=_h(), json=body, timeout=_T)
            if r.status_code != 200:
                logger.error(f"[notion] ❌ OPEN 조회 실패 {r.status_code}: {r.text[:200]}")
                break
            data = r.json()
            for pg in data.get("results", []):
                parsed = _parse(pg)
                if parsed:
                    out.append(parsed)
                if len(out) >= cap:
                    return out
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
    except Exception as e:
        logger.error(f"[notion] ❌ OPEN 조회 예외: {e}")
    return out


# ── 중복 진입 차단 인덱스 (신호 생성기) ──────────────────────────
def open_index(database_id: Optional[str] = None) -> dict:
    """현재 OPEN 신호를 색인해 중복/과밀 진입을 차단한다 (1회 쿼리).
      keys      : {(symbol, polarity, direction)} — 동일 셋업 OPEN 여부
      dir_count : {(symbol, direction): 건수}      — 심볼·방향별 슬롯(MAX_POS_DIR)
      glob_dir  : {direction: 건수}               — 전역 방향 노출(MAX_CONCURRENT_DIR, A-3)
    동일 셋업이 이미 OPEN이면 해소(WIN/LOSS/TIMEOUT) 전까지 재진입 금지 →
    같은 시장상황에서 15분마다 같은 신호가 중복 적재되는 것을 막는다.
    database_id 지정 시 그 DB(예: Shadow DB) 기준 색인 — Shadow 중복 적재 방지에 재사용.
    """
    db = database_id or oc.NOTION_DATABASE_ID
    idx = {"keys": set(), "dir_count": {}, "glob_dir": {}}
    if not (oc.NOTION_TOKEN and db):
        return idx
    for r in query_open(database_id=db):
        sym = r.get("symbol")
        dr  = (r.get("direction") or "").lower()
        pol = r.get("polarity") or ""
        if not sym or not dr:
            continue
        idx["keys"].add((sym, pol, dr))
        idx["dir_count"][(sym, dr)] = idx["dir_count"].get((sym, dr), 0) + 1
        idx["glob_dir"][dr] = idx["glob_dir"].get(dr, 0) + 1   # A-3 전역 방향 노출
    return idx


# ── 판정 UPDATE ───────────────────────────────────────────────────
def update_outcome(page_id, status, mfe_r=None, mae_r=None, bars_to_exit=None,
                   pnl_pct=None, pnl_r=None, exit_reason=None) -> bool:
    if not enabled() or not page_id:
        return False
    try:
        props = {"Status": _sel(status), "Resolved At": _date(timeutil.now_kst_iso())}
        if oc.V4_ENABLED:
            ledger = v4.outcome_fields(pnl_r, oc)
            props.update({
                "Gross R": _num(ledger["gross_r"]),
                "Net R": _num(ledger["net_r"]),
                "Realized Cost R": _num(ledger["realized_cost_r"]),
            })
        if mfe_r is not None:        props["MFE R"] = _num(round(mfe_r, 3))
        if mae_r is not None:        props["MAE R"] = _num(round(mae_r, 3))
        if bars_to_exit is not None: props["Bars To Exit"] = _num(bars_to_exit)
        if pnl_pct is not None:      props["PnL %"] = _num(round(pnl_pct, 3))
        # C-1 실현 R + 청산사유(TP/SL/BE/TIME)를 Note에 기록(스키마 변경 불필요).
        bits = []
        if pnl_r is not None: bits.append(f"R={pnl_r:+.2f}")
        if exit_reason:       bits.append(str(exit_reason))
        if mfe_r is not None and mae_r is not None:
            bits.append(f"MFE{mfe_r:+.2f}/MAE{mae_r:+.2f}")
        if bits:                     props["Note"] = _txt(" | ".join(bits))
        r = requests.patch(f"{_API}/pages/{page_id}", headers=_h(),
                           json={"properties": props}, timeout=_T)
        if r.status_code == 200:
            logger.info(f"[notion] ✅ 판정 {page_id} → {status}")
            return True
        logger.error(f"[notion] ❌ 판정 실패 {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"[notion] ❌ 판정 예외: {e}")
    return False
