"""
microstructure_analyzer.py  (v2.4 — 업그레이드)
──────────────────────────────────────────────────────────────────────────────
[v2.3 → v2.4 변경]

★ Fix 1: 방안 6 펀딩비 임계값 수정 (0.030→0.003, 0.015→0.001)
  OKX 실제 펀딩비 스케일 = 소수점 (0.000038 = 0.0038%)
  기존 0.030 임계값은 3%로 사실상 절대 발동 불가였음
  → MarkFunding 신호가 처음으로 실제 작동함

★ Fix 2: 방안 1 Liquidation 임계값 완화
  기존: lr > 0.70 AND count >= 5  → 거의 발동 없음
  수정: lr > 0.55 AND count >= 2  → 실질 신호 생성

★ Replace: 방안 4 BB Direction Compat → OB Volume Imbalance
  BB 정보는 analysis_engine이 이미 완전히 처리함 (이중 반영)
  → 주문장 전체 bid/ask 잔량 비율로 교체 (순수 마이크로구조 신호)
  → 기존 orderbook 데이터 재사용, 추가 API 없음

★ New: 방안 5 Candle Momentum (CVD Proxy)
  OI 히스토리 fetch 시 같이 가져오는 OHLCV 재활용
  캔들 방향(close-open 부호) × 상대 body 크기 × 거래량 가중
  → 최근 5캔들 가중평균 → 단기 매수/매도 압력 정량화
  → 추가 API 호출 없음

방안 구조:
  방안 1: Liquidation Cascade Discriminator
  방안 2: Order Book Wall Detection        (기존)
  방안 3: OI Velocity Matrix               (기존)
  방안 4: OB Volume Imbalance              (신규 교체)
  방안 5: Candle Momentum / CVD Proxy     (신규)
  방안 6: Mark Price + Next Funding Rate  (임계값 수정)
  방안 7: Account vs Position LS Divergence (기존)
  → 7개 분석 함수 / 6개 데이터 수집 포인트
──────────────────────────────────────────────────────────────────────────────
"""

import logging
import time
from typing import Optional, Tuple, List

import requests

logger = logging.getLogger(__name__)

MICRO_PENALTY_CAP = -30
OKX_BASE = "https://www.okx.com/api/v5"


# ══════════════════════════════════════════════════════════════════════════════
# 유틸리티
# ══════════════════════════════════════════════════════════════════════════════

def _to_ccxt_swap(symbol: str) -> str:
    if ":" in symbol: return symbol
    p = symbol.split("/")
    return f"{p[0]}/{p[1]}:{p[1]}" if len(p) == 2 else symbol

def _to_base_id(symbol: str) -> str:
    return symbol.replace("/", "-").split(":")[0]

def _to_swap_id(symbol: str) -> str:
    return _to_base_id(symbol) + "-SWAP"

def _to_uly(symbol: str) -> str:
    return _to_base_id(symbol)

def _to_ccy(symbol: str) -> str:
    """BTC/USDT → BTC"""
    return symbol.split("/")[0]

def _okx_get(path: str, params: dict = None) -> dict:
    """OKX 공개 API 직접 호출"""
    try:
        r = requests.get(
            f"{OKX_BASE}{path}",
            params=params or {},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        return r.json()
    except Exception as e:
        logger.warning(f"[OKX-HTTP] {path} 실패: {e}")
        return {"code": "error", "data": [], "msg": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# 방안 1: Liquidation Cascade Discriminator
# ══════════════════════════════════════════════════════════════════════════════

def fetch_liquidation_data(exchange, symbol: str, lookback_minutes: int = 30) -> dict:
    """
    OKX: GET /api/v5/public/liquidation-orders
    OKX side 의미:
      'buy'  = 숏 포지션 강제청산 (시장 매수압력)
      'sell' = 롱 포지션 강제청산 (시장 매도압력)
    """
    empty = {"long_liq_vol": 0.0, "short_liq_vol": 0.0,
             "long_liq_count": 0, "short_liq_count": 0,
             "total_vol": 0.0, "available": False}
    try:
        resp = _okx_get("/public/liquidation-orders", {
            "instType": "SWAP",
            "uly":      _to_uly(symbol),
            "state":    "filled",
            "limit":    "100",
        })
        if not resp.get("data"):
            return empty

        cutoff_ms = (time.time() - lookback_minutes * 60) * 1000
        lv = sv = lc = sc = 0.0

        for item in resp["data"]:
            for d in item.get("details", []):
                try:
                    ts   = float(d.get("ts", 0))
                    if ts < cutoff_ms: continue
                    sz   = float(d.get("sz", 0))
                    side = d.get("side", "")
                    if side == "buy":    sv += sz; sc += 1   # 숏 포지션 청산
                    elif side == "sell": lv += sz; lc += 1   # 롱 포지션 청산
                except Exception:
                    continue

        return {
            "long_liq_vol":    lv,
            "short_liq_vol":   sv,
            "long_liq_count":  int(lc),
            "short_liq_count": int(sc),
            "total_vol":       lv + sv,
            "available":       True,
        }
    except Exception as e:
        logger.warning(f"[Micro/Liq] 수집 실패 ({symbol}): {e}")
        return empty


def analyze_liquidation_cascade(liq: dict, taker_buy_pct: float, direction: str) -> Tuple[int, str]:
    """
    ★ v2.4: 임계값 완화 (lr>0.70,count>=5 → lr>0.55,count>=2)
    너무 엄격한 조건으로 실질 신호 없던 문제 수정
    """
    direction = direction.upper()
    if not liq.get("available") or liq["total_vol"] == 0:
        return 0, ""

    t  = liq["total_vol"]
    sr = liq["short_liq_vol"] / t
    lr = liq["long_liq_vol"]  / t
    tk = taker_buy_pct / 100.0   # buy_pct(0~100) → 0~1

    p, r = 0, ""

    if direction == "LONG":
        if tk > 0.72 and sr > 0.60:
            # 숏 청산 주도 + taker 매수 과열 → 스퀴즈 말미 위험
            p = -12; r = f"⚠️[Liq] 숏청산 주도+taker과열(스퀴즈말미) short:{sr:.0%} taker:{tk:.0%}"
        elif lr > 0.55 and liq["long_liq_count"] >= 2:
            if liq["long_liq_count"] >= 5:
                # 대형 롱 캐스케이드
                p = -15; r = f"⚠️[Liq] 롱청산 캐스케이드 롱:{lr:.0%} ×{liq['long_liq_count']}건"
            else:
                # 중간 규모 롱 청산
                p = -8;  r = f"⚠️[Liq] 롱청산 압력 롱:{lr:.0%} ×{liq['long_liq_count']}건"
        elif lr > 0.60 and tk > 0.60 and liq["long_liq_count"] < 3:
            # 소규모 롱 청산 후 실매수 → 반등 기대
            p = +8; r = "✅[Liq] 롱청산 완료 후 실매수 반전"

    elif direction == "SHORT":
        if tk < 0.32 and lr > 0.60:
            # 롱 청산 주도 + taker 매도 과열 → 바닥 부근 숏 위험
            p = -10; r = f"⚠️[Liq] 롱청산 말미 숏역방향 위험 long:{lr:.0%} taker:{tk:.0%}"
        elif sr > 0.55 and liq["short_liq_count"] >= 2:
            if liq["short_liq_count"] >= 5:
                p = -15; r = f"⚠️[Liq] 숏청산 캐스케이드 숏:{sr:.0%} ×{liq['short_liq_count']}건"
            else:
                p = -8;  r = f"⚠️[Liq] 숏청산 압력 숏:{sr:.0%} ×{liq['short_liq_count']}건"
        elif sr > 0.60 and tk < 0.38 and liq["short_liq_count"] < 3:
            p = +8; r = "✅[Liq] 숏청산 완료 후 실매도 반전"

    return p, r


# ══════════════════════════════════════════════════════════════════════════════
# 방안 2: Order Book Wall Detection
# ══════════════════════════════════════════════════════════════════════════════

def fetch_orderbook_data(exchange, symbol: str, depth: int = 20) -> dict:
    try:
        b = exchange.fetch_order_book(_to_ccxt_swap(symbol), limit=depth)
        return {"bids": b["bids"], "asks": b["asks"], "available": True}
    except Exception as e:
        logger.warning(f"[Micro/OB] 수집 실패 ({symbol}): {e}")
        return {"bids": [], "asks": [], "available": False}


def analyze_orderbook_pressure(
    books: dict, current_price: float, direction: str, depth: int = 20
) -> Tuple[int, str, Optional[float]]:
    """가격 근처 대형 주문벽(wall) 탐지"""
    direction = direction.upper()
    if not books.get("available") or not books["bids"] or not books["asks"]:
        return 0, "", None

    bids, asks = books["bids"], books["asks"]
    try:
        avg_ask = sum(a[1] for a in asks[:depth]) / max(len(asks[:depth]), 1)
        avg_bid = sum(b[1] for b in bids[:depth]) / max(len(bids[:depth]), 1)
        zone = 0.01; WALL = 4.0

        ask_walls = [(a[0], a[1]) for a in asks
                     if a[0] <= current_price * (1 + zone) and a[1] > avg_ask * WALL]
        bid_walls = [(b[0], b[1]) for b in bids
                     if b[0] >= current_price * (1 - zone) and b[1] > avg_bid * WALL]

        p, r, suggested = 0, "", None

        if direction == "LONG":
            if ask_walls:
                wp, wv = ask_walls[0]
                wd = (wp - current_price) / current_price
                wr = wv / avg_ask
                if wd < 0.003:   p = -12; r = f"⚠️[OB] ask벽 {wd:.2%}내 {wr:.1f}배 즉시저항"
                elif wd < 0.005: p = -5;  r = f"⚠️[OB] ask벽 {wd:.2%}내 {wr:.1f}배"
            if bid_walls:
                suggested = bid_walls[-1][0] * 1.001

        elif direction == "SHORT":
            if bid_walls:
                wp, wv = bid_walls[0]
                wd = (current_price - wp) / current_price
                wr = wv / avg_bid
                if wd < 0.003:   p = -12; r = f"⚠️[OB] bid벽 {wd:.2%}내 {wr:.1f}배 즉시지지"
                elif wd < 0.005: p = -5;  r = f"⚠️[OB] bid벽 {wd:.2%}내 {wr:.1f}배"
            if ask_walls:
                suggested = ask_walls[-1][0] * 0.999

        return p, r, suggested
    except Exception as e:
        logger.warning(f"[Micro/OB] wall 분석 실패: {e}")
        return 0, "", None


# ══════════════════════════════════════════════════════════════════════════════
# 방안 3: OI Velocity Matrix
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_oi_history_fallback(exchange, symbol: str, periods: int) -> list:
    """
    fetch_open_interest_history 실패 시 폴백.
    현재 OI(단일값) + OHLCV 조합.
    OI가 일정 → oi_d≈0 → velocity 분석 neutral (안전)
    """
    try:
        swap = _to_ccxt_swap(symbol)
        resp = _okx_get("/public/open-interest", {"instId": _to_swap_id(symbol)})
        if not resp.get("data"):
            return []
        current_oi = float(resp["data"][0].get("oi", 0))
        if current_oi <= 0:
            return []
        ohlcv = exchange.fetch_ohlcv(swap, "5m", limit=periods)
        if not ohlcv:
            return []
        result = [
            {"oi": current_oi, "close": float(c[4]), "ts": c[0],
             "open": float(c[1]), "high": float(c[2]), "low": float(c[3]),
             "volume": float(c[5])}
            for c in ohlcv
        ]
        logger.debug(f"[Micro/OI] 폴백: OI={current_oi:.0f}, {len(result)}캔들")
        return result
    except Exception as e:
        logger.warning(f"[Micro/OI] 폴백 실패 ({symbol}): {e}")
        return []


def fetch_oi_history(exchange, symbol: str, periods: int = 12) -> list:
    """
    OHLCV 포함 확장 레코드 반환.
    CCXT 성공 시: {oi, close, ts}
    폴백 시:      {oi, close, ts, open, high, low, volume}
    """
    try:
        swap    = _to_ccxt_swap(symbol)
        oi_list = exchange.fetch_open_interest_history(swap, "5m", limit=periods)
        ohlcv   = exchange.fetch_ohlcv(swap, "5m", limit=periods)
        pm      = {c[0]: c for c in ohlcv}   # ts → candle 매핑
        result  = []
        for x in oi_list:
            ts  = x.get("timestamp", 0)
            oi  = float(x.get("openInterestAmount") or x.get("openInterest") or 0)
            c   = pm.get(ts)
            result.append({
                "oi":     oi,
                "close":  float(c[4]) if c else 0.0,
                "ts":     ts,
                "open":   float(c[1]) if c else 0.0,
                "high":   float(c[2]) if c else 0.0,
                "low":    float(c[3]) if c else 0.0,
                "volume": float(c[5]) if c else 0.0,
            })
        return result
    except (TypeError, AttributeError):
        logger.debug(f"[Micro/OI] fetch_open_interest_history 미지원 → 폴백 ({symbol})")
        return _fetch_oi_history_fallback(exchange, symbol, periods)
    except Exception as e:
        logger.warning(f"[Micro/OI] 수집 실패 ({symbol}): {e}")
        return _fetch_oi_history_fallback(exchange, symbol, periods)


def analyze_oi_velocity(oi_history: list, direction: str, regime: str = "") -> Tuple[int, str]:
    """OI 변화 방향 × 가격 방향 4분면 분류"""
    direction = direction.upper()
    if len(oi_history) < 6:
        return 0, ""
    try:
        e3 = oi_history[:3]    # 오래된 3개
        r3 = oi_history[-3:]   # 최근 3개
        avg_oi_e = sum(x["oi"]    for x in e3) / 3
        avg_oi_r = sum(x["oi"]    for x in r3) / 3
        avg_px_e = sum(x["close"] for x in e3) / 3
        avg_px_r = sum(x["close"] for x in r3) / 3
        if avg_oi_e == 0 or avg_px_e == 0:
            return 0, ""

        oi_d = (avg_oi_r - avg_oi_e) / avg_oi_e
        px_d = (avg_px_r - avg_px_e) / avg_px_e
        TH   = 0.008

        oi_up = oi_d > TH; oi_dn = oi_d < -TH
        px_up = px_d > TH; px_dn = px_d < -TH

        # OI 폴백(단일값)이면 oi_up/oi_dn 모두 False → neutral 반환
        if not (oi_up or oi_dn) and not (px_up or px_dn):
            return 0, ""

        mult = 0.6 if regime == "EXPLOSIVE" else 1.0
        p, name, r = 0, "NEUTRAL", ""

        if oi_up and px_up:
            name = "ACCUMULATION"
            if direction == "SHORT":
                p = int(-10 * mult)
                r = f"⚠️[OI] ACCUMULATION 숏역행 (oi:{oi_d:+.1%} px:{px_d:+.1%})"
            else:
                p = +5; r = "✅[OI] ACCUMULATION 신규롱 축적"
        elif oi_up and px_dn:
            name = "SHORT_BUILDUP"
            if direction == "LONG":
                p = int(-10 * mult)
                r = f"⚠️[OI] SHORT_BUILDUP 롱역행 (oi:{oi_d:+.1%} px:{px_d:+.1%})"
            else:
                p = +5; r = "✅[OI] SHORT_BUILDUP 신규숏 진입"
        elif oi_dn and px_up:
            name = "SHORT_SQUEEZE"
            if direction == "LONG":
                p = int(-12 * mult)
                r = f"⚠️[OI] SHORT_SQUEEZE 말미 롱위험 (oi:{oi_d:+.1%} px:{px_d:+.1%})"
        elif oi_dn and px_dn:
            name = "LONG_LIQUIDATION"
            if direction == "LONG":
                p = int(-15 * mult)
                r = f"⚠️[OI] LONG_LIQUIDATION 롱금지 (oi:{oi_d:+.1%} px:{px_d:+.1%})"
            elif direction == "SHORT" and oi_d < -0.03:
                p = int(-6 * mult)
                r = "⚠️[OI] LONG_LIQUIDATION 과매도 숏주의"

        logger.debug(f"[OI/{regime}] {name} oi_δ:{oi_d:.2%} px_δ:{px_d:.2%} → {p:+d}pt")
        return p, r
    except Exception as e:
        logger.warning(f"[Micro/OI] 분석 실패: {e}")
        return 0, ""


# ══════════════════════════════════════════════════════════════════════════════
# 방안 4: OB Volume Imbalance  [★ v2.4 신규 — BB Compat 교체]
# ══════════════════════════════════════════════════════════════════════════════

def analyze_orderbook_imbalance(books: dict, direction: str, depth: int = 10) -> Tuple[int, str]:
    """
    ★ v2.4 신규: 전체 bid/ask 잔량 비율로 단기 방향 압력 측정
    BB Direction Compat 교체 (BB는 analysis_engine이 이미 처리)

    bid_ratio = total_bid_vol / (total_bid_vol + total_ask_vol)
      > 0.62: 매수 압력 우세 → 롱 유리
      < 0.38: 매도 압력 우세 → 숏 유리
      사이: 중립

    Wall Detection(방안 2)과 상호보완:
      방안 2: 가격 근처 단일 대형 주문 탐지 (즉각 저항/지지)
      방안 4: 전체 호가창 균형 측정 (방향 압력 누적)
    """
    direction = direction.upper()
    if not books.get("available") or not books["bids"] or not books["asks"]:
        return 0, ""
    try:
        total_bid = sum(b[1] for b in books["bids"][:depth])
        total_ask = sum(a[1] for a in books["asks"][:depth])
        total = total_bid + total_ask
        if total <= 0:
            return 0, ""

        bid_ratio = total_bid / total   # 0.5 = 균형
        bid_pct   = round(bid_ratio * 100, 1)
        ask_pct   = round((1 - bid_ratio) * 100, 1)

        p, r = 0, ""

        if direction == "LONG":
            if bid_ratio >= 0.65:
                p = +7;  r = f"✅[OBI] 매수잔량 우세 bid:{bid_pct}% ask:{ask_pct}% → 롱 압력"
            elif bid_ratio >= 0.58:
                p = +4;  r = f"✅[OBI] 매수잔량 소폭 우세 bid:{bid_pct}%"
            elif bid_ratio <= 0.35:
                p = -10; r = f"⚠️[OBI] 매도잔량 압도 bid:{bid_pct}% ask:{ask_pct}% → 롱 역풍"
            elif bid_ratio <= 0.42:
                p = -5;  r = f"⚠️[OBI] 매도잔량 우세 bid:{bid_pct}%"

        elif direction == "SHORT":
            if bid_ratio <= 0.35:
                p = +7;  r = f"✅[OBI] 매도잔량 우세 bid:{bid_pct}% ask:{ask_pct}% → 숏 압력"
            elif bid_ratio <= 0.42:
                p = +4;  r = f"✅[OBI] 매도잔량 소폭 우세 ask:{ask_pct}%"
            elif bid_ratio >= 0.65:
                p = -10; r = f"⚠️[OBI] 매수잔량 압도 bid:{bid_pct}% ask:{ask_pct}% → 숏 역풍"
            elif bid_ratio >= 0.58:
                p = -5;  r = f"⚠️[OBI] 매수잔량 우세 bid:{bid_pct}%"

        return p, r
    except Exception as e:
        logger.warning(f"[Micro/OBI] 분석 실패: {e}")
        return 0, ""


# ══════════════════════════════════════════════════════════════════════════════
# 방안 5: Candle Momentum / CVD Proxy  [★ v2.4 신규]
# ══════════════════════════════════════════════════════════════════════════════

def analyze_candle_momentum(oi_history: list, direction: str) -> Tuple[int, str]:
    """
    ★ v2.4 신규: CVD Proxy — 캔들 방향 × body 크기 × 거래량 가중치
    추가 API 없음: fetch_oi_history()가 반환하는 OHLCV 재사용

    계산 방식:
      각 캔들: bull_weight = max(0, (close-open)/(high-low)) × volume
              bear_weight = max(0, (open-close)/(high-low)) × volume
      최근 캔들에 시간 가중치 부여 (1~5, 최신=5)
      mom_score = weighted_bull / (weighted_bull + weighted_bear)

    신호 기준:
      > 0.65: 매수 모멘텀 → 롱 유리
      < 0.35: 매도 모멘텀 → 숏 유리
    """
    direction = direction.upper()
    # open/volume 필드 포함 여부 확인 (CCXT 성공 경로 or 폴백 경로 모두 포함)
    if (len(oi_history) < 3 or
            "open" not in oi_history[0] or
            "volume" not in oi_history[0]):
        return 0, ""
    try:
        recent = oi_history[-5:]   # 최근 5캔들
        w_bull = 0.0; w_bear = 0.0

        for i, c in enumerate(recent):
            o, h, l, cl, vol = (
                c.get("open", 0), c.get("high", cl := c["close"]),
                c.get("low",  cl), c["close"], c.get("volume", 1.0)
            )
            rng = h - l
            if rng <= 0 or vol <= 0:
                continue

            body_bull = max(0.0, (cl - o) / rng)   # 양봉 비율
            body_bear = max(0.0, (o - cl) / rng)   # 음봉 비율
            weight    = (i + 1) * vol               # 최신 캔들 가중치 높음

            w_bull += body_bull * weight
            w_bear += body_bear * weight

        total = w_bull + w_bear
        if total <= 0:
            return 0, ""

        mom_score = w_bull / total   # 0~1, 0.5=중립
        mom_pct   = round(mom_score * 100, 1)

        p, r = 0, ""

        if direction == "LONG":
            if mom_score >= 0.70:
                p = +6;  r = f"✅[CM] 매수모멘텀 강 ({mom_pct}%) → 롱 확인"
            elif mom_score >= 0.60:
                p = +3;  r = f"✅[CM] 매수모멘텀 ({mom_pct}%)"
            elif mom_score <= 0.30:
                p = -8;  r = f"⚠️[CM] 매도모멘텀 강 ({mom_pct}%) → 롱 역풍"
            elif mom_score <= 0.40:
                p = -4;  r = f"⚠️[CM] 매도모멘텀 ({mom_pct}%)"

        elif direction == "SHORT":
            if mom_score <= 0.30:
                p = +6;  r = f"✅[CM] 매도모멘텀 강 ({mom_pct}%) → 숏 확인"
            elif mom_score <= 0.40:
                p = +3;  r = f"✅[CM] 매도모멘텀 ({mom_pct}%)"
            elif mom_score >= 0.70:
                p = -8;  r = f"⚠️[CM] 매수모멘텀 강 ({mom_pct}%) → 숏 역풍"
            elif mom_score >= 0.60:
                p = -4;  r = f"⚠️[CM] 매수모멘텀 ({mom_pct}%)"

        return p, r
    except Exception as e:
        logger.warning(f"[Micro/CM] 분석 실패: {e}")
        return 0, ""


# ══════════════════════════════════════════════════════════════════════════════
# 방안 6: Mark Price Basis + Next Funding Rate  [★ v2.4: 임계값 수정]
# ══════════════════════════════════════════════════════════════════════════════

def fetch_mark_funding_data(exchange, symbol: str) -> dict:
    """마크 가격 + 펀딩비 직접 HTTP 호출"""
    res = {"mark_price": None, "current_funding_rate": None,
           "next_funding_rate": None, "available": False}
    try:
        resp = _okx_get("/public/mark-price", {
            "instType": "SWAP",
            "instId":   _to_swap_id(symbol),
        })
        if resp.get("data"):
            res["mark_price"] = float(resp["data"][0]["markPx"])
    except Exception as e:
        logger.warning(f"[Micro/MF] 마크가격 실패: {e}")

    try:
        resp2 = _okx_get("/public/funding-rate", {"instId": _to_swap_id(symbol)})
        if resp2.get("data"):
            d = resp2["data"][0]
            res["current_funding_rate"] = float(d.get("fundingRate",     0) or 0)
            res["next_funding_rate"]    = float(d.get("nextFundingRate", 0) or 0)
    except Exception as e:
        logger.warning(f"[Micro/MF] 펀딩비 실패: {e}")

    res["available"] = res["mark_price"] is not None or res["next_funding_rate"] is not None
    return res


def analyze_mark_funding_composite(mf: dict, current_price: float, direction: str) -> Tuple[int, str]:
    """
    ★ v2.4: 펀딩비 임계값 전면 수정
    기존: nf > 0.030 (3%)  → 사실상 절대 발동 불가
    수정: nf > 0.003 (0.3%)→ 실제 시장 수준에 맞게 조정

    OKX 실제 펀딩비 스케일 (소수):
      평상시: ±0.0001 ~ ±0.0005 (±0.01% ~ ±0.05%)
      상승장: +0.001 ~ +0.005   (+0.1% ~ +0.5%)
      극단:   > +0.01            (> 1%)

    임계값 재설정:
      mild:   |nf| > 0.0005  (0.05% — 롱쏠림 시작)
      strong: |nf| > 0.001   (0.1%  — 명확한 방향 편향)
      extreme:|nf| > 0.003   (0.3%  — 강한 청산 위험)
    """
    direction = direction.upper()
    if not mf.get("available"):
        return 0, ""

    p = 0; parts = []

    # ── 마크가격 괴리 ──────────────────────────────────────────
    mark = mf.get("mark_price")
    if mark and mark > 0 and current_price > 0:
        basis = (current_price - mark) / mark
        if direction == "LONG":
            if   basis > 0.005:  p -= 14; parts.append(f"⚠️마크괴리+{basis:.3%}(과열)")
            elif basis > 0.003:  p -= 8;  parts.append(f"⚠️마크괴리+{basis:.3%}")
            elif basis > 0.002:  p -= 4;  parts.append(f"마크괴리+{basis:.3%}")
            elif basis < -0.003: p += 5;  parts.append(f"✅마크디스카운트({basis:.3%})")
        elif direction == "SHORT":
            if   basis < -0.005: p -= 14; parts.append(f"⚠️마크괴리{basis:.3%}(과열)")
            elif basis < -0.003: p -= 8;  parts.append(f"⚠️마크괴리{basis:.3%}")
            elif basis < -0.002: p -= 4;  parts.append(f"마크괴리{basis:.3%}")
            elif basis >  0.003: p += 5;  parts.append(f"✅마크프리미엄({basis:.3%})")

    # ── 차기 펀딩비 (★ 임계값 수정) ───────────────────────────
    nf = mf.get("next_funding_rate")
    if nf is not None:
        if direction == "LONG":
            if   nf >  0.003:  p -= 12; parts.append(f"⚠️차기펀딩극단롱불리 {nf*100:+.4f}%")
            elif nf >  0.001:  p -= 7;  parts.append(f"⚠️차기펀딩롱불리 {nf*100:+.4f}%")
            elif nf >  0.0005: p -= 3;  parts.append(f"차기펀딩롱편향 {nf*100:+.4f}%")
            elif nf < -0.001:  p += 5;  parts.append(f"✅차기펀딩롱유리 {nf*100:+.4f}%")
            elif nf < -0.0005: p += 3;  parts.append(f"✅차기펀딩소폭롱유리 {nf*100:+.4f}%")
        elif direction == "SHORT":
            if   nf < -0.003:  p -= 12; parts.append(f"⚠️차기펀딩극단숏불리 {nf*100:+.4f}%")
            elif nf < -0.001:  p -= 7;  parts.append(f"⚠️차기펀딩숏불리 {nf*100:+.4f}%")
            elif nf < -0.0005: p -= 3;  parts.append(f"차기펀딩숏편향 {nf*100:+.4f}%")
            elif nf >  0.001:  p += 5;  parts.append(f"✅차기펀딩숏유리 {nf*100:+.4f}%")
            elif nf >  0.0005: p += 3;  parts.append(f"✅차기펀딩소폭숏유리 {nf*100:+.4f}%")

    # ── 복합 불리: 마크괴리 + 펀딩비 같은 방향 ────────────────
    if mark and mark > 0 and nf is not None and current_price > 0:
        basis = (current_price - mark) / mark
        if   direction == "LONG"  and basis > 0.002 and nf > 0.0005: p -= 3; parts.append("복합불리↑")
        elif direction == "SHORT" and basis < -0.002 and nf < -0.0005:p -= 3; parts.append("복합불리↓")

    return p, " | ".join(f"[MF]{x}" for x in parts) if parts else ""


# ══════════════════════════════════════════════════════════════════════════════
# 방안 7: Account-Level vs Position-Level LS Divergence
# ══════════════════════════════════════════════════════════════════════════════

def fetch_account_ls_ratio(exchange, symbol: str) -> Optional[float]:
    """
    OKX: GET /api/v5/rubik/stat/contracts/long-short-account-ratio
    파라미터: ccy (필수) — instId 미지원
    반환: 계좌 기준 롱 비율 (0.0 ~ 1.0)
    """
    try:
        resp = _okx_get("/rubik/stat/contracts/long-short-account-ratio", {
            "ccy":    _to_ccy(symbol),
            "period": "5m",
            "limit":  "1",
        })
        if resp.get("code") != "0" or not resp.get("data"):
            logger.debug(
                f"[Micro/LS] 계좌LS 응답 오류 ({symbol}): "
                f"code={resp.get('code')} msg={resp.get('msg', '')}"
            )
            return None
        ratio    = float(resp["data"][0][1])
        long_pct = ratio / (1.0 + ratio)
        logger.debug(f"[Micro/LS] 계좌롱비율: {long_pct:.1%} ({symbol})")
        return long_pct
    except Exception as e:
        logger.warning(f"[Micro/LS] 계좌LS 실패 ({symbol}): {e}")
        return None


def analyze_ls_divergence(
    account_long_pct: Optional[float],
    position_long_pct: float,
    direction: str,
) -> Tuple[int, str]:
    """
    계좌 기준 vs 포지션 기준 롱 비율 괴리 → 고래 포지션 방향 추정
    account > position: 소액 계좌가 롱 주도, 고래는 숏
    account < position: 고래 계좌가 롱 주도
    """
    direction = direction.upper()
    if account_long_pct is None:
        return 0, ""
    div = account_long_pct - position_long_pct
    s   = f"계좌:{account_long_pct:.1%} 포지션:{position_long_pct:.1%} 괴리:{div:+.1%}"
    p, r = 0, ""

    if direction == "LONG":
        if   div >  0.15: p = -10; r = f"⚠️[LS] 고래숏 강력포착 ({s})"
        elif div >  0.10: p =  -5; r = f"⚠️[LS] 고래숏 경향 ({s})"
        elif div < -0.15: p =  +8; r = f"✅[LS] 고래롱 포착 ({s})"
        elif div < -0.10: p =  +4; r = f"✅[LS] 고래롱 경향 ({s})"
    elif direction == "SHORT":
        if   div < -0.15: p = -10; r = f"⚠️[LS] 고래롱 강력포착 ({s})"
        elif div < -0.10: p =  -5; r = f"⚠️[LS] 고래롱 경향 ({s})"
        elif div >  0.15: p =  +8; r = f"✅[LS] 고래숏 포착 ({s})"
        elif div >  0.10: p =  +4; r = f"✅[LS] 고래숏 경향 ({s})"

    return p, r


# ══════════════════════════════════════════════════════════════════════════════
# 통합 수집
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_microstructure(exchange, symbol: str) -> dict:
    """
    6개 데이터 포인트 수집.
    oi_history에 OHLCV 포함 → 방안 5 추가 API 호출 없음.
    """
    logger.info(f"[Microstructure] 📡 수집: {symbol}")

    liq_data  = fetch_liquidation_data(exchange, symbol)
    ob_data   = fetch_orderbook_data(exchange, symbol)
    oi_data   = fetch_oi_history(exchange, symbol)       # OHLCV 포함
    mf_data   = fetch_mark_funding_data(exchange, symbol)
    acct_ls   = fetch_account_ls_ratio(exchange, symbol)

    data = {
        "liquidation":  liq_data,
        "orderbook":    ob_data,
        "oi_history":   oi_data,   # {oi, close, ts, open, high, low, volume}
        "mark_funding": mf_data,
        "account_ls":   acct_ls,
    }

    # 방안별 활성 상태 (7분석 / 6데이터)
    status = {
        "Liq":  liq_data.get("available", False),
        "OB":   ob_data.get("available",  False),
        "OI":   len(oi_data) > 0,
        "CM":   len(oi_data) > 0 and "open" in (oi_data[0] if oi_data else {}),
        "MF":   mf_data.get("available",  False),
        "LS":   acct_ls is not None,
    }
    ok = sum(status.values())
    logger.info(f"[Microstructure] ✅ {ok}/6 방안 활성  {status}")

    if ok < 6:
        failed = [k for k, v in status.items() if not v]
        logger.info(f"[Microstructure] ⚠️ 비활성: {failed}")

    return data


# ══════════════════════════════════════════════════════════════════════════════
# 통합 패널티 계산
# ══════════════════════════════════════════════════════════════════════════════

def compute_microstructure_penalties(
    micro_data:        dict,
    current_price:     float,
    direction:         str,
    regime:            str,
    percent_b:         float,
    taker_buy_pct:     float,   # 0~100 스케일
    position_long_pct: float,   # 0~1 스케일
) -> dict:
    """
    7개 분석 → 패널티/보너스 합산.
    방안 4: BB Compat 제거 → OB Volume Imbalance 교체
    방안 5: Candle Momentum 신규 추가
    """
    direction = direction.upper()
    checks = []; suggested = None

    # 방안 1: Liquidation Cascade
    p1, r1 = analyze_liquidation_cascade(
        micro_data.get("liquidation", {}), taker_buy_pct, direction
    )
    if r1: checks.append(("LiqCascade", p1, r1))

    # 방안 2: Order Book Wall
    p2, r2, entry = analyze_orderbook_pressure(
        micro_data.get("orderbook", {}), current_price, direction
    )
    if r2: checks.append(("OrderBook", p2, r2))
    if entry: suggested = entry

    # 방안 3: OI Velocity
    p3, r3 = analyze_oi_velocity(
        micro_data.get("oi_history", []), direction, regime
    )
    if r3: checks.append(("OIVelocity", p3, r3))

    # 방안 4: OB Volume Imbalance (★ 교체)
    p4, r4 = analyze_orderbook_imbalance(
        micro_data.get("orderbook", {}), direction
    )
    if r4: checks.append(("OBImbalance", p4, r4))

    # 방안 5: Candle Momentum (★ 신규)
    p5, r5 = analyze_candle_momentum(
        micro_data.get("oi_history", []), direction
    )
    if r5: checks.append(("CandleMom", p5, r5))

    # 방안 6: Mark Price + Funding Rate
    p6, r6 = analyze_mark_funding_composite(
        micro_data.get("mark_funding", {}), current_price, direction
    )
    if r6: checks.append(("MarkFunding", p6, r6))

    # 방안 7: LS Divergence
    p7, r7 = analyze_ls_divergence(
        micro_data.get("account_ls"), position_long_pct, direction
    )
    if r7: checks.append(("LSDivergence", p7, r7))

    raw   = sum(p for _, p, _ in checks)
    total = max(raw, MICRO_PENALTY_CAP)

    if checks:
        cap_note = f" [캡 {MICRO_PENALTY_CAP}pt 적용]" if raw < MICRO_PENALTY_CAP else ""
        logger.info(
            f"[Microstructure/{direction}] 합계: {raw:+d}pt → {total:+d}pt{cap_note}"
        )
        for name, p, r in checks:
            logger.info(f"  {'🔴' if p < 0 else '🟢'} [{name}] {p:+d}pt  {r}")
    else:
        logger.debug(f"[Microstructure/{direction}] 패널티/보너스 없음")

    return {
        "total_penalty":   total,
        "raw_total":       raw,
        "details":         checks,
        "suggested_entry": suggested,
    }
