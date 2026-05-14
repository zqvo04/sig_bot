"""
data_pipeline.py — OKX 선물 데이터 수집 파이프라인
──────────────────────────────────────────────────────────────────[...]
핵심 원칙: OKX 전용 엔드포인트는 CCXT를 거치지 않고 _okx_get() 직접 호출.
CCXT는 fetch_ohlcv / fetch_ticker / fetch_funding_rate 등 표준 메서드만 사용.

수정 이력:
  - collect_ls_ratio:    CCXT 없는 메서드 → _okx_get 직접 호출, instId → -SWAP suffix
  - collect_taker_volume: instType 파라미터 제거, _okx_get 사용, instId → -SWAP suffix
  - collect_funding_rate: BTC/USDT:USDT swap 형식 명시
  - collect_oi_change:   OKX 공개 API 직접 호출로 변경 (CCXT 메서드 제거)
  - collect_all_data:    SINGLE_SYMBOL → flat dict 반환 (main.py 호환)
──────────────────────────────────────────────────────────────────[...]
"""

import logging
import os
import time
from typing import Optional, Dict

import pandas as pd
import requests
import ccxt

import sys
sys.path.insert(0, os.path.dirname(__file__))

import config
from microstructure_analyzer import fetch_all_microstructure

logger = logging.getLogger(__name__)

OKX_BASE = "https://www.okx.com/api/v5"


# ══════════════════════════════════════════════════════════════════[...]
# OKX 공개 API 직접 호출 헬퍼
# ══════════════════════════════════════════════════════════════════[...]

def _okx_get(path: str, params: dict = None) -> dict:
    """
    OKX 공개 REST API 직접 호출.
    CCXT 메서드가 없거나 파라미터 매핑이 틀릴 때 사용.
    """
    try:
        r = requests.get(
            f"{OKX_BASE}{path}",
            params=params or {},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"[OKX-HTTP] {path} 실패: {e}")
        return {"code": "error", "data": [], "msg": str(e)}


# ══════════════════════════════════════════════════════════════════[...]
# 거래소 초기화
# ══════════════════════════════════════════════════════════════════[...]

def create_exchange() -> ccxt.okx:
    return ccxt.okx({
        "apiKey":          config.OKX_API_KEY,
        "secret":          config.OKX_API_SECRET,
        "password":        config.OKX_PASSPHRASE,
        "enableRateLimit": True,
        "options":         {"defaultType": "swap"},
    })


# ══════════════════════════════════════════════════════════════════[...]
# 심볼 변환 유틸리티
# ══════════════════════════════════════════════════════════════════[...]

def _to_ccxt_swap(symbol: str) -> str:
    """BTC/USDT → BTC/USDT:USDT (CCXT swap 형식)"""
    if ":" in symbol:
        return symbol
    parts = symbol.split("/")
    return f"{parts[0]}/{parts[1]}:{parts[1]}" if len(parts) == 2 else symbol


def _to_base_id(symbol: str) -> str:
    """BTC/USDT → BTC-USDT (OKX instId 기본)"""
    return symbol.replace("/", "-").split(":")[0]


def _to_swap_id(symbol: str) -> str:
    """BTC/USDT → BTC-USDT-SWAP (OKX SWAP instId)"""
    return _to_base_id(symbol) + "-SWAP"


def _to_uly(symbol: str) -> str:
    """BTC/USDT → BTC-USDT (OKX uly 파라미터)"""
    return _to_base_id(symbol)


def _ohlcv_to_df(ohlcv_list: list) -> pd.DataFrame:
    if not ohlcv_list:
        return pd.DataFrame()
    df = pd.DataFrame(
        ohlcv_list,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"])


# ══════════════════════════════════════════════════════════════════[...]
# 1. OHLCV
# ══════════════════════════════════════════════════════════════════[...]

def collect_ohlcv(exchange: ccxt.okx, symbol: str) -> Dict[str, pd.DataFrame]:
    swap = _to_ccxt_swap(symbol)
    result = {}
    for tf, limit in config.CANDLE_LIMITS.items():
        for attempt in range(config.MAX_RETRIES):
            try:
                raw = exchange.fetch_ohlcv(swap, tf, limit=limit)
                df  = _ohlcv_to_df(raw)
                result[tf] = df
                logger.info(f"  ✅ {symbol} [{tf}] {len(df)}개")
                break
            except ccxt.RateLimitExceeded:
                time.sleep(config.RETRY_DELAY_S * (attempt + 2))
            except Exception as e:
                if attempt < config.MAX_RETRIES - 1:
                    time.sleep(config.RETRY_DELAY_S)
                else:
                    logger.warning(f"  ❌ {symbol} [{tf}] OHLCV 실패: {e}")
                    result[tf] = pd.DataFrame()
    return result


# ══════════════════════════════════════════════════════════════════[...]
# 2. 펀딩비
# ══════════════════════════════════════════════════════════════════[...]

def collect_funding_rate(exchange: ccxt.okx, symbol: str) -> Optional[dict]:
    """CCXT fetch_funding_rate — swap 형식 심볼 필수"""
    try:
        fr        = exchange.fetch_funding_rate(_to_ccxt_swap(symbol))
        rate      = float(fr.get("fundingRate",     0) or 0)
        next_rate = float(fr.get("nextFundingRate", 0) or 0)
        logger.info(f"  💸 {symbol} 펀딩비: {rate*100:+.4f}%")
        return {
            "rate":          rate,
            "rate_pct":      round(rate * 100, 6),
            "next_rate":     next_rate,
            "next_rate_pct": round(next_rate * 100, 6),
        }
    except Exception as e:
        logger.warning(f"  ❌ {symbol} 펀딩비 실패: {e}")
        return None


# ══════════════════════════════════════════════════════════════════[...]
# 3. 롱숏 비율 (포지션 수 기준)
# ══════════════════════════════════════════════════════════════════[...]

def collect_ls_ratio(exchange: ccxt.okx, symbol: str) -> dict:
    """
    OKX: GET /api/v5/rubik/stat/contracts/long-short-pos-ratio
    CCXT에 이 메서드 없음 → 직접 HTTP 호출
    선물(SWAP)의 경우 instId는 -SWAP suffix 필수
    """
    empty = {"available": False, "long_pct": 0.5, "short_pct": 0.5}
    try:
        resp = _okx_get("/rubik/stat/contracts/long-short-pos-ratio", {
            "instId": _to_swap_id(symbol),  # 중요: -SWAP suffix
            "period": "5m",
            "limit":  "1",
        })
        
        if resp.get("code") != "0" or not resp.get("data"):
            logger.warning(f"  ⚠️  {symbol} 롱숏비율 응답 오류: {resp.get('msg', 'unknown')}")
            return empty

        # [[timestamp, longShortPosRatio], ...]
        ratio     = float(resp["data"][0][1])
        long_pct  = ratio / (1.0 + ratio)
        short_pct = 1.0 - long_pct
        logger.info(f"  📊 {symbol} 롱숏(직접): 롱 {long_pct*100:.1f}%")
        return {
            "available": True,
            "long_pct":  round(long_pct,  4),
            "short_pct": round(short_pct, 4),
        }
    except Exception as e:
        logger.warning(f"  ❌ {symbol} 롱숏비율 실패: {e}")
        return empty


# ══════════════════════════════════════════════════════════════════[...]
# 4. Taker 비율
# ══════════════════════════════════════════════════════════════════[...]

def collect_taker_volume(exchange: ccxt.okx, symbol: str) -> dict:
    """
    OKX: GET /api/v5/rubik/stat/taker-volume-contract
    파라미터: instId (필수, -SWAP suffix), period, limit
    CCXT publicGetRubikStatTakerVolumeContract가 ccy 파라미터를 강제 추가 → 직접 HTTP 호출
    """
    empty = {
        "available": False, "buy_ratio": 0.5, "sell_ratio": 0.5,
        "bias": "neutral", "strength": "neutral", "buy_pct": 50.0,
    }
    try:
        resp = _okx_get("/rubik/stat/taker-volume-contract", {
            "instId": _to_swap_id(symbol),  # 중요: -SWAP suffix
            "period": "5m",
            "limit":  str(min(config.TAKER_LOOKBACK, 100)),
        })
        
        if resp.get("code") != "0" or not resp.get("data"):
            logger.warning(f"  ⚠️  {symbol} Taker 응답 오류: {resp.get('msg', 'unknown')}")
            return empty

        # [[timestamp, buy_vol_usdt, sell_vol_usdt], ...] 최신→오래된
        n          = min(20, len(resp["data"]))
        total_buy  = sum(float(row[1]) for row in resp["data"][:n])
        total_sell = sum(float(row[2]) for row in resp["data"][:n])
        total      = total_buy + total_sell
        if total <= 0:
            return empty

        buy_r  = total_buy  / total
        sell_r = total_sell / total

        if   buy_r  >= config.TAKER_STRONG_BUY:  bias, strength = "buy_dominant",  "strong"
        elif sell_r >= config.TAKER_STRONG_SELL:  bias, strength = "sell_dominant", "strong"
        elif buy_r  >= 0.55:                       bias, strength = "buy_dominant",  "normal"
        elif sell_r >= 0.55:                       bias, strength = "sell_dominant", "normal"
        else:                                      bias, strength = "neutral",       "neutral"

        logger.info(f"  🔄 {symbol} Taker: 매수 {buy_r*100:.1f}% / 매도 {sell_r*100:.1f}% [{bias}]")
        return {
            "available": True,
            "buy_ratio":  round(buy_r,  4),
            "sell_ratio": round(sell_r, 4),
            "bias":       bias,
            "strength":   strength,
            "buy_pct":    round(buy_r * 100, 2),
        }
    except Exception as e:
        logger.warning(f"  ❌ {symbol} Taker 실패: {e}")
        return empty


# ══════════════════════════════════════════════════════════════════[...]
# 5. OI 변화율
# ══════════════════════════════════════════════════════════════════[...]

def collect_oi_change(exchange: ccxt.okx, symbol: str) -> dict:
    """
    OI 변화율 (1시간 전 대비 현재).
    OKX 공개 API 직접 호출: /api/v5/public/open-interest
    """
    empty = {"available": False, "change_pct": 0.0, "current_oi": 0.0, "prev_oi": 0.0, 
             "direction": "", "interpretation": ""}
    try:
        # 현재 OI 조회
        resp_now = _okx_get("/public/open-interest", {
            "instId": _to_swap_id(symbol),
        })
        
        if resp_now.get("code") != "0" or not resp_now.get("data"):
            logger.warning(f"  ⚠️  {symbol} OI 현재값 조회 실패: {resp_now.get('msg', 'unknown')}")
            return empty
        
        current_oi = float(resp_now["data"][0].get("oi", 0))
        if current_oi <= 0:
            return empty
        
        # 1시간 전 OI 조회 (openInterestHistory 사용)
        resp_hist = _okx_get("/public/open-interest-history", {
            "instId": _to_swap_id(symbol),
            "period": "1m",
            "limit":  "65",  # 1시간 = 약 60분
        })
        
        if resp_hist.get("code") != "0" or not resp_hist.get("data"):
            logger.warning(f"  ⚠️  {symbol} OI 히스토리 조회 실패")
            return empty
        
        if len(resp_hist["data"]) < 2:
            return empty
        
        # 가장 오래된 데이터 = 1시간 전 추정
        prev_oi = float(resp_hist["data"][-1].get("oi", 0))
        
        if prev_oi <= 0:
            return empty
        
        change_pct = (current_oi - prev_oi) / prev_oi
        
        # 방향성 판단
        direction = "increasing" if change_pct > 0 else "decreasing"
        
        logger.info(f"  📈 {symbol} OI: {prev_oi:.0f} → {current_oi:.0f} ({change_pct*100:+.2f}%) [{direction}]")
        
        return {
            "available":     True,
            "change_pct":    round(change_pct, 6),
            "current_oi":    round(current_oi, 2),
            "prev_oi":       round(prev_oi,    2),
            "direction":     direction,
            "interpretation": "bullish_trend_confirm" if change_pct > 0.02 else 
                             "bearish_trend_confirm" if change_pct < -0.02 else "neutral",
        }
    except Exception as e:
        logger.warning(f"  ❌ {symbol} OI 실패: {e}")
        return empty


# ══════════════════════════════════════════════════════════════════[...]
# 6. 현재가
# ══════════════════════════════════════════════════════════════════[...]

def collect_ticker(exchange: ccxt.okx, symbol: str) -> dict:
    try:
        t        = exchange.fetch_ticker(_to_ccxt_swap(symbol))
        last     = float(t.get("last",  0) or 0)
        open_24h = float(t.get("open",  last) or last)
        change   = ((last - open_24h) / open_24h * 100) if open_24h > 0 else 0.0
        logger.info(f"  💰 {symbol} ${last:,.4f} ({change:+.2f}%)")
        return {"last": last, "open": open_24h, "change_pct": round(change, 4), "available": True}
    except Exception as e:
        logger.warning(f"  ❌ {symbol} 티커 실패: {e}")
        return {"last": 0.0, "open": 0.0, "change_pct": 0.0, "available": False}


# ══════════════════════════════════════════════════════════════════[...]
# 단일 심볼 수집
# ══════════════════════════════════════════════════════════════════[...]

def collect(exchange: ccxt.okx, symbol: str) -> dict:
    """
    단일 심볼 전체 데이터 수집.
    반환 dict는 analysis_engine.run_full_analysis() 입력 형식과 일치.
    """
    logger.info(f"{'─'*50}")
    logger.info(f"📡 수집: {symbol}")

    ohlcv        = collect_ohlcv(exchange, symbol)
    funding_rate = collect_funding_rate(exchange, symbol)
    ls_ratio     = collect_ls_ratio(exchange, symbol)
    taker_volume = collect_taker_volume(exchange, symbol)
    oi_change    = collect_oi_change(exchange, symbol)
    ticker       = collect_ticker(exchange, symbol)
    micro        = fetch_all_microstructure(exchange, symbol)

    return {
        "symbol":         symbol,
        "ohlcv":          ohlcv,
        "ticker":         ticker,
        "funding_rate":   funding_rate,
        "ls_ratio":       ls_ratio,
        "oi_change":      oi_change,
        "taker_volume":   taker_volume,
        "liquidations":   {},   # analyze_liquidations()는 df_15m 직접 사용
        "price":          ticker.get("last", 0.0),
        "microstructure": micro,
    }


# ══════════════════════════════════════════════════════════════════[...]
# 일괄 수집 (main.py 진입점)
# ══════════════════════════════════════════════════════════════════[...]

def collect_all_data(exchange: ccxt.okx, symbols) -> dict:
    """
    GitHub Actions Matrix Job: SINGLE_SYMBOL 환경변수 설정 시
    → collect() 결과를 flat dict로 반환 (main.py가 collected["ticker"] 접근과 호환)

    로컬 전체 실행: {symbol: collected_data} 형식 반환
    """
    single = os.environ.get("SINGLE_SYMBOL", "").strip()
    if single:
        return collect(exchange, single)

    # 전체 심볼 처리
    if isinstance(symbols, str):
        symbols = [symbols]
    results = {}
    for sym in symbols:
        try:
            results[sym] = collect(exchange, sym)
        except Exception as e:
            logger.error(f"[Pipeline] {sym} 수집 오류: {e}")
            results[sym] = None
    return results


# ══════════════════════════════════════════════════════════════════[...]
# 헬스체크
# ══════════════════════════════════════════════════════════════════[...]

def check_connection(exchange: ccxt.okx) -> bool:
    try:
        exchange.fetch_time()
        logger.info("✅ OKX API 연결 정상")
        return True
    except Exception as e:
        logger.error(f"❌ OKX API 연결 실패: {e}")
        return False
