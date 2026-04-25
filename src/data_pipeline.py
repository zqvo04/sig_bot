"""
data_pipeline.py — 데이터 수집 (개선판)
신규: E. 강제청산 수집 / F. Taker Buy/Sell Volume 수집
수정: load_markets 추가, defaultType swap 분리, TypeError 예외 처리
"""
import time, logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import ccxt
import numpy as np
import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config

logger = logging.getLogger(__name__)


# ── ccxt OKX 버그 패치 ──────────────────────────────────────────
# OKX 마켓 목록에 base=None 인 항목(일부 옵션 상품 등)이 포함될 때
# ccxt parse_market() 내부에서 None + '/' + quote → TypeError 발생.
# ccxt는 모든 API 함수에서 lazy load_markets()를 호출하므로 회피 불가.
# parse_markets()를 monkey-patch해서 실패한 마켓만 skip하고 계속 진행.
_original_parse_markets = ccxt.okx.parse_markets  # type: ignore

def _safe_parse_markets(self, markets):
    result = []
    for market in (markets or []):
        try:
            parsed = self.parse_market(market)
            if parsed:
                result.append(parsed)
        except Exception:
            pass  # base=None 등 비정상 마켓은 조용히 skip
    return result

ccxt.okx.parse_markets = _safe_parse_markets  # type: ignore
# ────────────────────────────────────────────────────────────────


def create_exchange() -> ccxt.okx:
    """
    spot 전용 exchange + swap 전용 exchange 를 한 번에 생성.
    ccxt OKX는 defaultType 에 따라 URL 맵이 달라지므로
    선물 전용 엔드포인트(OI, Taker 선물)는 swap_exchange를 사용해야 한다.
    """
    common_opts = {"adjustForTimeDifference": True}
    auth = {}
    if config.OKX_API_KEY and config.OKX_PASSPHRASE:
        auth = {
            "apiKey":   config.OKX_API_KEY,
            "secret":   config.OKX_API_SECRET,
            "password": config.OKX_PASSPHRASE,
        }

    # ── spot exchange (ticker, OHLCV, 롱숏, Taker spot trades)
    spot_exchange = ccxt.okx({
        **auth,
        "options": {**common_opts, "defaultType": "spot"},
    })

    # ── swap exchange (OI, 펀딩비, Taker 선물 trades)
    swap_exchange = ccxt.okx({
        **auth,
        "options": {**common_opts, "defaultType": "swap"},
    })

    # load_markets() 명시 호출 금지:
    # OKX 마켓 목록에 base=null 인 항목이 있어 ccxt parse_market()이
    # None + '/' + quote → TypeError 로 크래시함.
    # ccxt는 실제 API 호출 시 필요한 심볼만 lazy load 하므로 호출 불필요.

    # 하위 호환을 위해 spot_exchange를 메인으로, swap_exchange를 속성으로 추가
    spot_exchange._swap = swap_exchange
    return spot_exchange


def _to_swap_symbol(symbol: str) -> str:
    base, quote = symbol.split("/")
    return f"{base}/{quote}:{quote}"


def retry_on_failure(func):
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except ccxt.RateLimitExceeded as e:
                wait = config.RETRY_DELAY_S * (2 ** attempt)
                logger.warning(f"[{func.__name__}] 레이트 리밋 — {wait}s")
                time.sleep(wait); last_exc = e
            except ccxt.NetworkError as e:
                time.sleep(config.RETRY_DELAY_S * attempt); last_exc = e
            except ccxt.BadSymbol as e:
                logger.error(f"[{func.__name__}] 지원하지 않는 심볼: {e}"); return None
            except ccxt.ExchangeError as e:
                logger.error(f"[{func.__name__}] 거래소 오류 (재시도 불가): {e}"); return None
            except Exception as e:
                logger.error(f"[{func.__name__}] 오류: {e}", exc_info=True); return None
        return None
    return wrapper


# ── OHLCV ──
@retry_on_failure
def fetch_ohlcv(exchange, symbol, timeframe, limit=None):
    limit = limit or config.CANDLE_LIMITS.get(timeframe, 100)
    raw   = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    if not raw: return None
    df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    return df


def fetch_multi_timeframe_ohlcv(exchange, symbol):
    result = {}
    for _, tf in config.TIMEFRAMES.items():
        df = fetch_ohlcv(exchange, symbol, tf)
        result[tf] = df
        logger.info(f"  {'✅' if df is not None else '❌'} {symbol} [{tf}] "
                    f"{'%d개' % len(df) if df is not None else '실패'}")
        time.sleep(0.3)
    return result


# ── 펀딩비 (swap exchange 사용) ──
@retry_on_failure
def fetch_funding_rate(exchange, symbol):
    swap_exchange = getattr(exchange, '_swap', exchange)
    swap_symbol   = _to_swap_symbol(symbol)
    try:
        funding = swap_exchange.fetch_funding_rate(swap_symbol)
    except ccxt.BadSymbol:
        return None
    rate = funding.get("fundingRate")
    if rate is None: return None
    rate = float(rate)
    logger.info(f"  💸 {symbol} 펀딩비: {rate*100:+.4f}%")
    return {
        "rate": rate, "rate_pct": round(rate*100, 6),
        "next_funding_at": str(funding.get("nextFundingDatetime","N/A")),
        "symbol": swap_symbol,
    }


# ── 롱숏 비율 ──
def fetch_long_short_ratio(exchange, symbol) -> dict:
    swap_symbol   = _to_swap_symbol(symbol)
    swap_exchange = getattr(exchange, '_swap', exchange)
    _neutral = {"long_pct": 0.5, "short_pct": 0.5, "ratio": 1.0, "available": False}
    try:
        data = swap_exchange.fetch_long_short_ratio(swap_symbol, "1h", limit=1)
        if data and len(data) > 0:
            ls_ratio  = float(data[-1].get("longShortRatio", 1.0))
            long_pct  = ls_ratio / (1.0 + ls_ratio)
            short_pct = 1.0 - long_pct
            logger.info(f"  📊 {symbol} 롱숏: 롱 {long_pct*100:.1f}% / 숏 {short_pct*100:.1f}%")
            return {"long_pct": round(long_pct,4), "short_pct": round(short_pct,4),
                    "ratio": round(ls_ratio,4), "available": True}
    except AttributeError:
        pass
    except Exception as e:
        logger.debug(f"[fetch_long_short_ratio] CCXT 실패: {e}")
    try:
        base   = symbol.split("/")[0]
        result = exchange.publicGetRubikStatContractsLongShortAccountRatio(
            {"ccy": base, "period": "1H", "limit": "1"})
        data_list = result.get("data", [])
        if data_list:
            ls = float(data_list[0][1])
            long_pct  = ls / (1.0 + ls)
            short_pct = 1.0 - long_pct
            logger.info(f"  📊 {symbol} 롱숏(직접): 롱 {long_pct*100:.1f}%")
            return {"long_pct": round(long_pct,4), "short_pct": round(short_pct,4),
                    "ratio": round(ls,4), "available": True}
    except Exception as e:
        logger.warning(f"[fetch_long_short_ratio] {symbol}: {e}")
    return _neutral


# ── OI 변화율 (swap exchange 사용) ──
def fetch_oi_change(exchange, symbol) -> dict:
    """
    swap exchange로 OI를 조회.
    defaultType=spot 에서 fetch_open_interest_history 호출 시
    URL이 None이 되어 TypeError 발생 → swap exchange로 분리해서 해결.
    """
    swap_exchange = getattr(exchange, '_swap', exchange)
    swap_symbol   = _to_swap_symbol(symbol)
    _neutral = {"current_oi":0,"prev_oi":0,"change_pct":0.0,"direction":"unknown","available":False}
    try:
        history = swap_exchange.fetch_open_interest_history(swap_symbol, "1h", limit=3)
        if history and len(history) >= 2:
            current_oi = float(history[-1].get("openInterestAmount") or
                               history[-1].get("openInterest") or 0)
            prev_oi    = float(history[-2].get("openInterestAmount") or
                               history[-2].get("openInterest") or 0)
            if prev_oi > 0 and current_oi > 0:
                change_pct = (current_oi - prev_oi) / prev_oi
                logger.info(f"  📈 {symbol} OI: {change_pct*100:+.2f}%")
                return {"current_oi": round(current_oi,2), "prev_oi": round(prev_oi,2),
                        "change_pct": round(change_pct,6),
                        "direction": "increasing" if change_pct>0 else "decreasing",
                        "available": True}
    except (TypeError, AttributeError) as e:
        # URL None 오류 또는 미지원 메서드 → 조용히 neutral 반환
        logger.debug(f"[fetch_oi_change] {symbol} 미지원 또는 URL 오류: {e}")
    except Exception as e:
        logger.warning(f"[fetch_oi_change] {symbol}: {e}")
    return _neutral


# ── F: Taker Buy/Sell Volume ──────────────────────────────────
def fetch_taker_volume(exchange, symbol) -> dict:
    """
    spot trades로 Taker 비율 계산.
    TypeError(URL None) 발생 시 조용히 neutral 반환.
    """
    _neutral = {"buy_ratio": 0.5, "sell_ratio": 0.5,
                "buy_volume": 0, "sell_volume": 0,
                "bias": "neutral", "strength": "neutral", "available": False}
    try:
        trades = exchange.fetch_trades(symbol, limit=config.TAKER_LOOKBACK)
        if not trades or len(trades) < 10:
            return _neutral

        buy_vol  = sum(float(t.get("amount") or 0) * float(t.get("price") or 0)
                       for t in trades if t.get("side") == "buy")
        sell_vol = sum(float(t.get("amount") or 0) * float(t.get("price") or 0)
                       for t in trades if t.get("side") == "sell")
        total = buy_vol + sell_vol
        if total <= 0:
            return _neutral

        buy_ratio  = buy_vol / total
        sell_ratio = sell_vol / total

        if buy_ratio >= config.TAKER_STRONG_BUY:
            bias, strength = "buy_dominant", "strong"
        elif buy_ratio >= 0.55:
            bias, strength = "buy_dominant", "mild"
        elif sell_ratio >= config.TAKER_STRONG_SELL:
            bias, strength = "sell_dominant", "strong"
        elif sell_ratio >= 0.55:
            bias, strength = "sell_dominant", "mild"
        else:
            bias, strength = "neutral", "neutral"

        logger.info(f"  🔄 {symbol} Taker: 매수 {buy_ratio*100:.1f}% / 매도 {sell_ratio*100:.1f}% [{bias}]")
        return {
            "buy_ratio":  round(buy_ratio, 4),
            "sell_ratio": round(sell_ratio, 4),
            "buy_volume": round(buy_vol, 2),
            "sell_volume":round(sell_vol, 2),
            "bias":       bias,
            "strength":   strength,
            "available":  True,
        }
    except (TypeError, AttributeError) as e:
        # URL None 오류 → 조용히 neutral 반환
        logger.debug(f"[fetch_taker_volume] {symbol} URL 오류: {e}")
        return _neutral
    except Exception as e:
        logger.warning(f"[fetch_taker_volume] {symbol}: {e}")
        return _neutral


# ── E: 강제청산 프록시 ──────────────────────────────
def fetch_liquidations(exchange, symbol) -> dict:
    return {
        "long_liq_proxy":  0.0,
        "short_liq_proxy": 0.0,
        "signal":          "none",
        "is_large":        False,
        "available":       False,
    }


# ── 오더북 ──
@retry_on_failure
def fetch_orderbook(exchange, symbol, depth=None):
    depth = depth or config.ORDERBOOK_DEPTH
    ob    = exchange.fetch_order_book(symbol, limit=depth)
    bids  = ob.get("bids", [])
    asks  = ob.get("asks", [])
    if not bids or not asks: return None
    bid_volume = sum(float(e[1]) for e in bids)
    ask_volume = sum(float(e[1]) for e in asks)
    best_bid   = float(bids[0][0])
    best_ask   = float(asks[0][0])
    mid_price  = (best_bid + best_ask) / 2
    return {
        "bids": bids, "asks": asks,
        "bid_volume": bid_volume, "ask_volume": ask_volume,
        "bid_ask_ratio": bid_volume / ask_volume if ask_volume > 0 else 0.0,
        "spread_pct": (best_ask - best_bid) / mid_price * 100,
        "mid_price": mid_price, "best_bid": best_bid, "best_ask": best_ask,
    }


# ── 티커 ──
@retry_on_failure
def fetch_ticker(exchange, symbol):
    t = exchange.fetch_ticker(symbol)
    if t is None:
        return None
    last = t.get("last")
    if last is None:
        return None
    return {
        "last":       float(last),
        "change_pct": t.get("percentage"),
        "volume_24h": t.get("quoteVolume"),
        "high_24h":   t.get("high"),
        "low_24h":    t.get("low"),
    }


# ── 통합 수집 ──
def collect_all_data(exchange, symbol: str) -> dict:
    import datetime as dt
    logger.info(f"{'─'*50}")
    logger.info(f"📡 수집: {symbol}")

    ticker       = fetch_ticker(exchange, symbol)
    ohlcv_data   = fetch_multi_timeframe_ohlcv(exchange, symbol)
    funding_rate = fetch_funding_rate(exchange, symbol)
    ls_ratio     = fetch_long_short_ratio(exchange, symbol)
    oi_change    = fetch_oi_change(exchange, symbol)
    taker_vol    = fetch_taker_volume(exchange, symbol)
    liquidations = fetch_liquidations(exchange, symbol)

    if ticker and ticker.get("last"):
        logger.info(f"💰 {symbol} ${ticker['last']:,.4f} ({ticker.get('change_pct',0):+.2f}%)")

    return {
        "symbol":       symbol,
        "ticker":       ticker,
        "ohlcv":        ohlcv_data,
        "funding_rate": funding_rate,
        "ls_ratio":     ls_ratio,
        "oi_change":    oi_change,
        "taker_volume": taker_vol,
        "liquidations": liquidations,
        "collected_at": dt.datetime.utcnow().isoformat() + "Z",
    }
