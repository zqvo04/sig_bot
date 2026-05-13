"""
data_pipeline.py — OKX 선물 데이터 수집 파이프라인
──────────────────────────────────────────────────────────────────────────────
수집 항목:
  1. OHLCV          15m(100개) / 1h(210개) / 4h(210개)
  2. 펀딩비          현재 펀딩비율
  3. 롱숏 비율       포지션 수 기준 (직접)
  4. Taker 비율     최근 N기간 매수/매도 체결 비율
  5. OI 변화율      직전 12기간(1h) 대비 현재 OI 변화
  6. 현재가          ticker last price + 24h 변동률
  7. 마이크로구조     방안 1(청산) / 2(오더북) / 3(OI속도) / 6(마크/펀딩) / 7(LS괴리)

반환 구조 (analysis_engine.run_full_analysis 입력 형식):
  {
    'symbol':          str,
    'ohlcv':           {'15m': df, '1h': df, '4h': df},
    'ticker':          {'last': float, 'change_pct': float, 'available': bool},
    'funding_rate':    {'rate': float, 'rate_pct': float} | None,
    'ls_ratio':        {'available': bool, 'long_pct': float, 'short_pct': float},
    'oi_change':       {'available': bool, 'change_pct': float, ...},
    'taker_volume':    {'available': bool, 'buy_ratio': float, 'sell_ratio': float,
                        'bias': str, 'strength': str, 'buy_pct': float},
    'liquidations':    {},   # analyze_liquidations()는 df_15m 직접 사용
    'price':           float,
    'microstructure':  dict,
  }

OKX instId 변환 규칙:
  CCXT fetch_* 메서드 : "BTC/USDT:USDT"  (swap 타입)
  publicGet* 공개 API : "BTC-USDT"       (베이스 심볼)
  SWAP 계약 명시 필요  : "BTC-USDT-SWAP"
──────────────────────────────────────────────────────────────────────────────
"""

import logging
import time
from typing import Optional, Dict

import pandas as pd
import ccxt

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import config
from microstructure_analyzer_v2 import fetch_all_microstructure

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 거래소 초기화
# ══════════════════════════════════════════════════════════════════════════════

def create_exchange() -> ccxt.okx:
    """
    OKX 선물 거래소 객체 생성
    defaultType='swap' 로 퍼페츄얼 선물 기본 설정
    """
    exchange = ccxt.okx({
        'apiKey':          config.OKX_API_KEY,
        'secret':          config.OKX_API_SECRET,
        'password':        config.OKX_PASSPHRASE,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',
        },
    })
    return exchange


# ══════════════════════════════════════════════════════════════════════════════
# 심볼 변환 유틸리티
# ══════════════════════════════════════════════════════════════════════════════

def _to_ccxt_swap(symbol: str) -> str:
    """
    BTC/USDT → BTC/USDT:USDT  (CCXT 선물 형식)
    이미 변환된 심볼이면 그대로 반환
    """
    if ':' in symbol:
        return symbol
    parts = symbol.split('/')
    if len(parts) != 2:
        return symbol
    base, quote = parts
    return f"{base}/{quote}:{quote}"


def _to_base_id(symbol: str) -> str:
    """
    BTC/USDT      → BTC-USDT
    BTC/USDT:USDT → BTC-USDT
    OKX 공개 API (롱숏비율, Taker 등)에 사용
    """
    s = symbol.replace('/', '-').split(':')[0]
    return s


def _to_swap_id(symbol: str) -> str:
    """
    BTC/USDT → BTC-USDT-SWAP
    OKX 선물 계약 명시 API (OI 등)에 사용
    """
    return _to_base_id(symbol) + '-SWAP'


def _ohlcv_to_df(ohlcv_list: list) -> pd.DataFrame:
    """CCXT OHLCV 리스트 → pandas DataFrame"""
    if not ohlcv_list:
        return pd.DataFrame()
    df = pd.DataFrame(
        ohlcv_list,
        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
    )
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df = df.set_index('timestamp')
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['close'])


# ══════════════════════════════════════════════════════════════════════════════
# 1. OHLCV 수집
# ══════════════════════════════════════════════════════════════════════════════

def collect_ohlcv(exchange: ccxt.okx, symbol: str) -> Dict[str, pd.DataFrame]:
    """
    멀티 타임프레임 OHLCV 수집
    config.CANDLE_LIMITS: {'15m': 100, '1h': 210, '4h': 210}
    """
    swap_symbol = _to_ccxt_swap(symbol)
    ohlcv_dict  = {}

    for tf, limit in config.CANDLE_LIMITS.items():
        for attempt in range(config.MAX_RETRIES):
            try:
                raw = exchange.fetch_ohlcv(swap_symbol, tf, limit=limit)
                df  = _ohlcv_to_df(raw)
                ohlcv_dict[tf] = df
                logger.info(f"  ✅ {symbol} [{tf}] {len(df)}개")
                break
            except ccxt.RateLimitExceeded:
                wait = config.RETRY_DELAY_S * (attempt + 2)
                logger.warning(f"  ⏳ {symbol} [{tf}] 레이트 리밋 — {wait}s 대기")
                time.sleep(wait)
            except Exception as e:
                if attempt < config.MAX_RETRIES - 1:
                    time.sleep(config.RETRY_DELAY_S)
                else:
                    logger.warning(f"  ❌ {symbol} [{tf}] OHLCV 수집 실패: {e}")
                    ohlcv_dict[tf] = pd.DataFrame()

    return ohlcv_dict


# ══════════════════════════════════════════════════════════════════════════════
# 2. 펀딩비 수집
# ══════════════════════════════════════════════════════════════════════════════

def collect_funding_rate(exchange: ccxt.okx, symbol: str) -> Optional[dict]:
    """
    현재 펀딩비 + 차기 펀딩비
    CCXT fetch_funding_rate → fundingRate, nextFundingRate 포함
    """
    swap_symbol = _to_ccxt_swap(symbol)
    try:
        fr        = exchange.fetch_funding_rate(swap_symbol)
        rate      = float(fr.get('fundingRate',     0) or 0)
        next_rate = float(fr.get('nextFundingRate', 0) or 0)
        logger.info(f"  💸 {symbol} 펀딩비: {rate*100:+.4f}%")
        return {
            'rate':           rate,
            'rate_pct':       round(rate * 100, 6),
            'next_rate':      next_rate,
            'next_rate_pct':  round(next_rate * 100, 6),
        }
    except Exception as e:
        logger.warning(f"  ❌ {symbol} 펀딩비 수집 실패: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 3. 롱숏 비율 수집 (포지션 수 기준)
# ══════════════════════════════════════════════════════════════════════════════

def collect_ls_ratio(exchange: ccxt.okx, symbol: str) -> dict:
    """
    포지션 수 기준 롱숏 비율 (직접, Position-Level)
    OKX: GET /api/v5/rubik/stat/contracts/long-short-pos-ratio
         instId: BTC-USDT  (SWAP 없음)

    반환: long_pct, short_pct (0.0 ~ 1.0)
    """
    _empty = {'available': False, 'long_pct': 0.5, 'short_pct': 0.5}
    try:
        resp = exchange.publicGetRubikStatContractsLongShortPosRatio({
            'instId': _to_base_id(symbol),
            'period': '5m',
            'limit':  '1',
        })
        if not resp.get('data'):
            return _empty

        # OKX 반환: [[timestamp, longShortPosRatio], ...]
        # longShortPosRatio = 롱포지션수 / 숏포지션수
        ls_ratio  = float(resp['data'][0][1])
        long_pct  = ls_ratio / (1.0 + ls_ratio)
        short_pct = 1.0 - long_pct

        logger.info(f"  📊 {symbol} 롱숏(직접): 롱 {long_pct*100:.1f}%")
        return {
            'available':  True,
            'long_pct':   round(long_pct,  4),
            'short_pct':  round(short_pct, 4),
        }
    except Exception as e:
        logger.warning(f"  ❌ {symbol} 롱숏비율 수집 실패: {e}")
        return _empty


# ══════════════════════════════════════════════════════════════════════════════
# 4. Taker 비율 수집
# ══════════════════════════════════════════════════════════════════════════════

def collect_taker_volume(exchange: ccxt.okx, symbol: str) -> dict:
    """
    Taker 매수/매도 체결 비율
    OKX: GET /api/v5/rubik/stat/taker-volume
         instId: BTC-USDT, instType: SWAP

    최근 N개(config.TAKER_LOOKBACK) 기간 합산으로 계산
    반환: buy_ratio, sell_ratio (0.0 ~ 1.0), bias, strength, buy_pct (0~100)
    """
    _empty = {
        'available':  False,
        'buy_ratio':  0.5,
        'sell_ratio': 0.5,
        'bias':       'neutral',
        'strength':   'neutral',
        'buy_pct':    50.0,
    }
    try:
        resp = exchange.publicGetRubikStatTakerVolume({
            'instId':   _to_base_id(symbol),
            'instType': 'SWAP',
            'period':   '5m',
            'limit':    str(config.TAKER_LOOKBACK),
        })
        if not resp.get('data'):
            return _empty

        # OKX 반환: [[timestamp, buy_vol, sell_vol], ...]  (최신→오래된 순)
        # 최근 20개 기간만 합산 (약 100분)
        n_periods    = min(20, len(resp['data']))
        total_buy    = sum(float(row[1]) for row in resp['data'][:n_periods])
        total_sell   = sum(float(row[2]) for row in resp['data'][:n_periods])
        total        = total_buy + total_sell

        if total <= 0:
            return _empty

        buy_ratio  = total_buy  / total
        sell_ratio = total_sell / total

        # bias 판정 (config 임계값 사용)
        if buy_ratio >= config.TAKER_STRONG_BUY:
            bias, strength = 'buy_dominant', 'strong'
        elif sell_ratio >= config.TAKER_STRONG_SELL:
            bias, strength = 'sell_dominant', 'strong'
        elif buy_ratio >= 0.55:
            bias, strength = 'buy_dominant', 'normal'
        elif sell_ratio >= 0.55:
            bias, strength = 'sell_dominant', 'normal'
        else:
            bias, strength = 'neutral', 'neutral'

        logger.info(
            f"  🔄 {symbol} Taker: 매수 {buy_ratio*100:.1f}% / 매도 {sell_ratio*100:.1f}% [{bias}]"
        )
        return {
            'available':  True,
            'buy_ratio':  round(buy_ratio,  4),
            'sell_ratio': round(sell_ratio, 4),
            'bias':       bias,
            'strength':   strength,
            'buy_pct':    round(buy_ratio * 100, 2),  # microstructure_analyzer용
        }
    except Exception as e:
        logger.warning(f"  ❌ {symbol} Taker 수집 실패: {e}")
        return _empty


# ══════════════════════════════════════════════════════════════════════════════
# 5. OI 변화율 수집
# ══════════════════════════════════════════════════════════════════════════════

def collect_oi_change(exchange: ccxt.okx, symbol: str) -> dict:
    """
    미결제약정(OI) 변화율 계산
    방법: 현재 OI vs 1시간 전(12기간 전) OI 비교

    OKX endpoints:
      현재 OI : GET /api/v5/public/open-interest  (instType=SWAP, instId=BTC-USDT-SWAP)
      OI 히스토리: GET /api/v5/rubik/stat/contracts/open-interest-history
    """
    _empty = {
        'available':  False,
        'change_pct': 0.0,
        'current_oi': 0.0,
        'prev_oi':    0.0,
    }
    try:
        swap_id = _to_swap_id(symbol)

        # 현재 OI (계약 수 또는 코인 기준)
        resp_cur = exchange.publicGetPublicOpenInterest({
            'instType': 'SWAP',
            'instId':   swap_id,
        })
        if not resp_cur.get('data'):
            return _empty

        # oiCcy: 코인 기준 OI (BTC 등), oi: 계약 수 기준
        # 일관성을 위해 oi(계약 수) 사용
        data_cur   = resp_cur['data'][0]
        current_oi = float(data_cur.get('oi', 0) or data_cur.get('oiCcy', 0))

        # OI 히스토리 (5분봉 12개 = 약 1시간)
        resp_hist = exchange.publicGetRubikStatContractsOpenInterestHistory({
            'instType': 'SWAP',
            'instId':   swap_id,
            'period':   '5m',
            'limit':    '13',   # 1개 여유
        })
        if not resp_hist.get('data') or len(resp_hist['data']) < 2:
            return _empty

        # API는 최신→오래된 순 반환
        # 12기간 전 값 = 마지막 원소
        prev_oi = float(resp_hist['data'][-1][1])

        if prev_oi <= 0:
            return _empty

        change_pct = (current_oi - prev_oi) / prev_oi

        return {
            'available':   True,
            'change_pct':  round(change_pct, 6),
            'current_oi':  round(current_oi, 2),
            'prev_oi':     round(prev_oi, 2),
        }
    except Exception as e:
        logger.warning(f"  ❌ {symbol} OI 수집 실패: {e}")
        return _empty


# ══════════════════════════════════════════════════════════════════════════════
# 6. 현재가 수집
# ══════════════════════════════════════════════════════════════════════════════

def collect_ticker(exchange: ccxt.okx, symbol: str) -> dict:
    """
    현재 가격 및 24h 변동률
    CCXT fetch_ticker → last, open (24h 기준 변동률 계산)
    """
    swap_symbol = _to_ccxt_swap(symbol)
    try:
        ticker     = exchange.fetch_ticker(swap_symbol)
        last       = float(ticker.get('last',  0) or 0)
        open_24h   = float(ticker.get('open',  last) or last)
        change_pct = ((last - open_24h) / open_24h * 100) if open_24h > 0 else 0.0
        logger.info(f"  💰 {symbol} ${last:,.4f} ({change_pct:+.2f}%)")
        return {
            'last':       last,
            'open':       open_24h,
            'change_pct': round(change_pct, 4),
            'available':  True,
        }
    except Exception as e:
        logger.warning(f"  ❌ {symbol} 티커 수집 실패: {e}")
        return {'last': 0.0, 'open': 0.0, 'change_pct': 0.0, 'available': False}


# ══════════════════════════════════════════════════════════════════════════════
# 메인 수집 함수
# ══════════════════════════════════════════════════════════════════════════════

def collect(exchange: ccxt.okx, symbol: str) -> dict:
    """
    단일 심볼 전체 데이터 수집

    Args:
        exchange: ccxt.okx 인스턴스 (create_exchange()로 생성)
        symbol:   "BTC/USDT" 형식

    Returns:
        analysis_engine.run_full_analysis() 입력 형식 dict
    """
    logger.info(f"{'─'*50}")
    logger.info(f"📡 수집: {symbol}")

    # ── 1. OHLCV ────────────────────────────────────────────
    ohlcv = collect_ohlcv(exchange, symbol)

    # ── 2. 펀딩비 ───────────────────────────────────────────
    funding_rate = collect_funding_rate(exchange, symbol)

    # ── 3. 롱숏 비율 ────────────────────────────────────────
    ls_ratio = collect_ls_ratio(exchange, symbol)

    # ── 4. Taker 비율 ───────────────────────────────────────
    taker_volume = collect_taker_volume(exchange, symbol)

    # ── 5. OI 변화율 ────────────────────────────────────────
    oi_change = collect_oi_change(exchange, symbol)

    # ── 6. 현재가 ───────────────────────────────────────────
    ticker = collect_ticker(exchange, symbol)

    # ── 7. 마이크로구조 (방안 1/2/3/6/7) ─────────────────────
    # fetch_all_microstructure는 내부적으로 독립 예외처리 →
    # 실패해도 collect() 전체에 영향 없음
    microstructure = fetch_all_microstructure(exchange, symbol)

    return {
        'symbol':         symbol,
        'ohlcv':          ohlcv,
        'ticker':         ticker,
        'funding_rate':   funding_rate,
        'ls_ratio':       ls_ratio,
        'oi_change':      oi_change,
        'taker_volume':   taker_volume,
        'liquidations':   {},   # analyze_liquidations()는 df_15m을 직접 인자로 받음
        'price':          ticker.get('last', 0.0),
        'microstructure': microstructure,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 거래소 연결 체크 (선택적 헬스체크)
# ══════════════════════════════════════════════════════════════════════════════

def check_connection(exchange: ccxt.okx) -> bool:
    """API 연결 및 인증 상태 확인"""
    try:
        exchange.fetch_time()
        logger.info("✅ OKX API 연결 정상")
        return True
    except Exception as e:
        logger.error(f"❌ OKX API 연결 실패: {e}")
        return False
