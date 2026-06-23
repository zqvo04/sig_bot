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


# ── Taker 매수/매도 비율 (역방향 거부권) ───────────────────────────
def fetch_taker(symbol: str) -> dict:
    neutral = {"available": False, "buy_ratio": 0.5, "sell_ratio": 0.5}
    try:
        resp = requests.get(f"{oc.OKX_BASE}/rubik/stat/taker-volume-contract",
                            params={"instId": to_swap_id(symbol),
                                    "period": oc.TAKER_PERIOD,
                                    "limit": str(oc.TAKER_LOOKBACK)},
                            timeout=10).json()
        rows = resp.get("data", [])
        if not rows:
            return neutral
        # OKX taker-volume-contract 응답 = [ts, sellVol, buyVol] → r[1]=매도, r[2]=매수.
        #   (실데이터 검증: r[1]-비중 높은 5m봉의 양봉률 25% vs 낮은 봉 78%, n≈1만 → r[1]=매도 확정)
        #   기존엔 buy=r[1]/sell=r[2]로 뒤바뀌어 역방향 거부권이 거꾸로(승자 제거) 작동했음.
        sell = sum(float(r[1]) for r in rows)
        buy  = sum(float(r[2]) for r in rows)
        tot = buy + sell
        if tot <= 0:
            return neutral
        return {"available": True, "buy_ratio": round(buy / tot, 4),
                "sell_ratio": round(sell / tot, 4)}
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


def collect_context(exchange, symbol: str) -> dict:
    """거부권 판정에 필요한 맥락 데이터(캔들 제외 — 엔진이 직접 fetch)."""
    return {
        "ls_ratio":     fetch_ls_ratio(exchange, symbol),
        "taker":        fetch_taker(symbol),
    }
