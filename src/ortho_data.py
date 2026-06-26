"""
ortho_data.py — ORTHO 전용 OKX 데이터 수집 (자립형, 레거시 미의존)
════════════════════════════════════════════════════════════════════
ORTHO 엔진이 필요로 하는 최소 데이터만 수집한다:
  · OHLCV (15m/5m/1h/4h) — 캔들 (엔진이 직접 fetch_candles 호출)
  · ls_ratio   — 롱숏 포지션 비율 (군중 과밀 거부권용)
  · taker      — Taker 매수/매도 비율 (역방향 거부권용)
  · spread_bps — 호가 스프레드 (집행 품질 거부권용)
레거시 data_pipeline.py의 funding/OI/whale/PCR/liquidation 등은 일절 수집 안 함.
"""
import logging
import time

import ccxt
import requests

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import ortho_config as oc

logger = logging.getLogger("ortho.data")


# ── 심볼 변환 ─────────────────────────────────────────────────────
def to_ccxt_swap(symbol: str) -> str:
    if ":" in symbol:
        return symbol
    p = symbol.split("/")
    return f"{p[0]}/{p[1]}:{p[1]}" if len(p) == 2 else symbol

def to_swap_id(symbol: str) -> str:
    return symbol.replace("/", "-").split(":")[0] + "-SWAP"


def create_exchange() -> ccxt.okx:
    return ccxt.okx({
        "apiKey":   oc.OKX_API_KEY,
        "secret":   oc.OKX_API_SECRET,
        "password": oc.OKX_PASSPHRASE,
        "enableRateLimit": True,
        "options":  {"defaultType": "swap"},
    })


# ── 캔들 ──────────────────────────────────────────────────────────
def fetch_candles(exchange, symbol: str, tf: str, limit: int) -> list:
    """[[ts,o,h,l,c,v], ...] 반환. 실패 시 빈 리스트."""
    for attempt in range(oc.MAX_RETRIES):
        try:
            return exchange.fetch_ohlcv(to_ccxt_swap(symbol), tf, limit=limit) or []
        except ccxt.RateLimitExceeded:
            time.sleep(oc.RETRY_DELAY_S * (attempt + 2))
        except Exception as e:
            if attempt < oc.MAX_RETRIES - 1:
                time.sleep(oc.RETRY_DELAY_S)
            else:
                logger.warning(f"[data] {symbol} {tf} 캔들 실패: {e}")
    return []


# ── 롱숏 비율 (군중 과밀 거부권) ───────────────────────────────────
def fetch_ls_ratio(exchange, symbol: str) -> dict:
    neutral = {"available": False, "long_pct": 0.5, "short_pct": 0.5}
    try:
        data = exchange.fetch_long_short_ratio(to_ccxt_swap(symbol), "1h", limit=1)
        if data:
            r = float(data[-1].get("longShortRatio", 1.0))
            lp = r / (1.0 + r)
            return {"available": True, "long_pct": round(lp, 4),
                    "short_pct": round(1 - lp, 4)}
    except Exception:
        pass
    # 폴백: OKX REST 포지션 비율
    try:
        resp = requests.get(f"{oc.OKX_BASE}/rubik/stat/contracts/long-short-pos-ratio",
                            params={"ccy": symbol.split("/")[0], "period": "1H", "limit": "1"},
                            timeout=10).json()
        d = resp.get("data", [])
        if d:
            r = float(d[0][1])
            lp = r / (1.0 + r)
            return {"available": True, "long_pct": round(lp, 4),
                    "short_pct": round(1 - lp, 4)}
    except Exception as e:
        logger.debug(f"[data] {symbol} ls 폴백 실패: {e}")
    return neutral


# ── Taker 매수/매도 비율 (역방향 거부권) + 기울기(CVD 가속, 스캘핑 피처) ──
def _lin_slope(ys) -> float:
    """시계열의 최소제곱 기울기(봉당 변화). 표본<2면 0. 부호=방향, 크기=가속도."""
    n = len(ys)
    if n < 2:
        return 0.0
    xm = (n - 1) / 2.0
    ym = sum(ys) / n
    num = sum((i - xm) * (ys[i] - ym) for i in range(n))
    den = sum((i - xm) ** 2 for i in range(n))
    return (num / den) if den > 0 else 0.0


def fetch_taker(symbol: str) -> dict:
    neutral = {"available": False, "buy_ratio": 0.5, "sell_ratio": 0.5, "slope": None}
    try:
        # 기울기 표본 확보: 룩백과 기울기창 중 큰 값으로 받되 과도하지 않게.
        limit = max(oc.TAKER_LOOKBACK, oc.TAKER_SLOPE_LB)
        resp = requests.get(f"{oc.OKX_BASE}/rubik/stat/taker-volume-contract",
                            params={"instId": to_swap_id(symbol),
                                    "period": oc.TAKER_PERIOD,
                                    "limit": str(limit)},
                            timeout=10).json()
        rows = resp.get("data", [])
        if not rows:
            return neutral
        # OKX taker-volume-contract 응답 = [ts, sellVol, buyVol] → r[1]=매도, r[2]=매수. (최신우선)
        #   (실데이터 검증: r[1]-비중 높은 5m봉의 양봉률 25% vs 낮은 봉 78%, n≈1만 → r[1]=매도 확정)
        #   기존엔 buy=r[1]/sell=r[2]로 뒤바뀌어 역방향 거부권이 거꾸로(승자 제거) 작동했음.
        # 레벨(거부권): 룩백 전체 합산 비율. 기울기(피처): 봉별 매수비율 시계열의 추세.
        lvl = rows[:oc.TAKER_LOOKBACK]
        sell = sum(float(r[1]) for r in lvl)
        buy  = sum(float(r[2]) for r in lvl)
        tot = buy + sell
        if tot <= 0:
            return neutral
        # 봉별 매수비율 시계열 → 최신우선이므로 시간순(과거→현재)으로 뒤집고 기울기.
        series = []
        for r in rows[:oc.TAKER_SLOPE_LB]:
            s, b = float(r[1]), float(r[2]); t = s + b
            if t > 0:
                series.append(b / t)
        slope = round(_lin_slope(series[::-1]), 6) if len(series) >= 2 else None
        return {"available": True, "buy_ratio": round(buy / tot, 4),
                "sell_ratio": round(sell / tot, 4), "slope": slope}
    except Exception as e:
        logger.debug(f"[data] {symbol} taker 실패: {e}")
        return neutral


# ── 호가 스프레드 (집행 품질 거부권) ───────────────────────────────
def fetch_spread_bps(exchange, symbol: str):
    try:
        ob = exchange.fetch_order_book(to_ccxt_swap(symbol), limit=5)
        bid = ob["bids"][0][0] if ob.get("bids") else None
        ask = ob["asks"][0][0] if ob.get("asks") else None
        if bid and ask and bid > 0:
            return (ask - bid) / ((ask + bid) / 2) * 10000.0
    except Exception:
        pass
    return None


# ── OBI 호가 불균형 (스캘핑 피처 — 측정용 컬럼) ───────────────────
def fetch_orderbook_feats(exchange, symbol: str, depth: int) -> dict:
    """상위 depth단 호가 깊이 불균형 = (매수량−매도량)/(합). [-1,+1].
    +면 매수벽 우위(롱 우호), −면 매도벽 우위(숏 우호) — 부호로 롱숏 대칭. 단타 방향 선행지표.
    백분위 자기정규화는 누적 컬럼값으로 *오프라인* 수행(런 상태 무보존이라 raw 저장)."""
    out = {"obi": None}
    try:
        ob = exchange.fetch_order_book(to_ccxt_swap(symbol), limit=depth)
        bids = ob.get("bids") or []
        asks = ob.get("asks") or []
        bvol = sum(float(b[1]) for b in bids[:depth])
        avol = sum(float(a[1]) for a in asks[:depth])
        tot = bvol + avol
        if tot > 0:
            out["obi"] = round((bvol - avol) / tot, 4)
    except Exception as e:
        logger.debug(f"[data] {symbol} OBI 실패: {e}")
    return out


# ── Funding 백분위 (스캘핑 피처 — 군중 쏠림/평균회귀) ─────────────
def fetch_funding(exchange, symbol: str) -> dict:
    """현재 펀딩과, 과거 FUNDING_HIST 표본 내 백분위. 고펀딩=롱 과밀(숏 우호), 저=거울 → 롱숏 대칭.
    백분위는 *런 내* 계산 가능(과거 히스토리 1콜)이라 즉시 컬럼화."""
    out = {"rate": None, "pct": None}
    try:
        hist = exchange.fetch_funding_rate_history(to_ccxt_swap(symbol), limit=oc.FUNDING_HIST)
        rates = [float(h["fundingRate"]) for h in hist
                 if h.get("fundingRate") is not None]
        if rates:
            cur = rates[-1]
            out["rate"] = round(cur, 8)
            below = sum(1 for x in rates if x < cur)
            equal = sum(1 for x in rates if x == cur)
            out["pct"] = round((below + 0.5 * equal) / len(rates) * 100.0, 1)
    except Exception as e:
        logger.debug(f"[data] {symbol} funding 실패: {e}")
    return out


def collect_context(exchange, symbol: str) -> dict:
    """거부권 판정에 필요한 맥락 데이터(캔들 제외 — 엔진이 직접 fetch).
    SCALP_FEATS ON 시 OBI·funding을 추가 수집(taker 기울기는 fetch_taker 내장)."""
    ctx = {
        "ls_ratio":     fetch_ls_ratio(exchange, symbol),
        "taker":        fetch_taker(symbol),
    }
    if oc.SCALP_FEATS:
        ctx["orderbook"] = fetch_orderbook_feats(exchange, symbol, oc.OBI_DEPTH)
        ctx["funding"]   = fetch_funding(exchange, symbol)
    return ctx
