"""
microstructure_analyzer.py  (v3.3)
──────────────────────────────────────────────────────────────────────────────
[v3.3 변경]

★ OI Velocity 완전 제거 (방안 3)
  - fetch_oi_history(), _fetch_oi_history_fallback(), analyze_oi_velocity() 삭제
  - 제거 근거:
    1. OKX fetch_open_interest_history → 빈번한 API 오류(400)
    2. 폴백 시 단일 OI값 사용 → oi_d≈0 → 항상 중립 반환 → 실효성 없음
    3. 유지 비용 대비 신호 기여도 없음

★ fetch_ohlcv_micro() 신설
  - 5분봉 OHLCV만 수집 (OI 없음)
  - 방안 5(Candle Momentum) 단독 데이터 소스

★ "oi_history" → "ohlcv_micro" 키 리네임
  - fetch_all_microstructure() 반환 dict 키 변경
  - compute_microstructure_penalties() 참조 키 변경

★ 방안 번호 재정리 (7개 → 6개)
  방안 1: Liquidation Cascade Discriminator
  방안 2: Order Book Wall Detection
  방안 3: OB Volume Imbalance          (기존 방안 4)
  방안 4: Candle Momentum / CVD Proxy  (기존 방안 5)
  방안 5: Mark Price + Funding Rate    (기존 방안 6, v2.4 임계값 수정 유지)
  방안 6: Account vs Position LS Divergence (기존 방안 7)
  → 6개 분석 / 5개 데이터 수집 포인트

★ fetch_all_microstructure() status 5개로 업데이트
  기존: Liq/OB/OI/CM/MF/LS (6개)
  수정: Liq/OB/CM/MF/LS    (5개, OI 제거)

[v2.4] 펀딩비 임계값 수정(0.030→0.003), Liquidation 임계값 완화, OBImbalance 추가, CandleMom 추가
[v2.3] 시스템 초기 구축
──────────────────────────────────────────────────────────────────────────────
"""

import logging
import time
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

MICRO_PENALTY_CAP = -30
OKX_BASE = "https://www.okx.com/api/v5"


# ══════════════════════════════════════════════════════════════════════════════
# 유틸리티
# ══════════════════════════════════════════════════════════════════════════════

def _to_ccxt_swap(symbol: str) -> str:
    if ":" in symbol:
        return symbol
    p = symbol.split("/")
    return f"{p[0]}/{p[1]}:{p[1]}" if len(p) == 2 else symbol

def _to_base_id(symbol: str) -> str:
    return symbol.replace("/", "-").split(":")[0]

def _to_swap_id(symbol: str) -> str:
    return _to_base_id(symbol) + "-SWAP"

def _to_uly(symbol: str) -> str:
    return _to_base_id(symbol)

def _to_ccy(symbol: str) -> str:
    return symbol.split("/")[0]

def _okx_get(path: str, params: dict = None) -> dict:
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
    empty = {
        "long_liq_vol": 0.0, "short_liq_vol": 0.0,
        "long_liq_count": 0, "short_liq_count": 0,
        "total_vol": 0.0, "available": False,
    }
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
                    if ts < cutoff_ms:
                        continue
                    sz   = float(d.get("sz", 0))
                    side = d.get("side", "")
                    if side == "buy":      sv += sz; sc += 1
                    elif side == "sell":   lv += sz; lc += 1
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


def analyze_liquidation_cascade(liq: dict, taker_buy_pct: float,
                                 direction: str) -> Tuple[int, str]:
    direction = direction.upper()
    if not liq.get("available") or liq["total_vol"] == 0:
        return 0, ""

    t  = liq["total_vol"]
    sr = liq["short_liq_vol"] / t
    lr = liq["long_liq_vol"]  / t
    tk = taker_buy_pct / 100.0

    p, r = 0, ""

    if direction == "LONG":
        if tk > 0.72 and sr > 0.60:
            p = -12
            r = f"⚠️[Liq] 숏청산+taker과열(스퀴즈말미) short:{sr:.0%} taker:{tk:.0%}"
        elif lr > 0.55 and liq["long_liq_count"] >= 2:
            if liq["long_liq_count"] >= 5:
                p = -15
                r = f"⚠️[Liq] 롱청산 캐스케이드 롱:{lr:.0%} ×{liq['long_liq_count']}건"
            else:
                p = -8
                r = f"⚠️[Liq] 롱청산 압력 롱:{lr:.0%} ×{liq['long_liq_count']}건"
        elif lr > 0.60 and tk > 0.60 and liq["long_liq_count"] < 3:
            p = +8
            r = "✅[Liq] 롱청산 완료 후 실매수 반전"

    elif direction == "SHORT":
        if tk < 0.32 and lr > 0.60:
            p = -10
            r = f"⚠️[Liq] 롱청산 말미 숏역방향 위험 long:{lr:.0%} taker:{tk:.0%}"
        elif sr > 0.55 and liq["short_liq_count"] >= 2:
            if liq["short_liq_count"] >= 5:
                p = -15
                r = f"⚠️[Liq] 숏청산 캐스케이드 숏:{sr:.0%} ×{liq['short_liq_count']}건"
            else:
                p = -8
                r = f"⚠️[Liq] 숏청산 압력 숏:{sr:.0%} ×{liq['short_liq_count']}건"
        elif sr > 0.60 and tk < 0.38 and liq["short_liq_count"] < 3:
            p = +8
            r = "✅[Liq] 숏청산 완료 후 실매도 반전"

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


def analyze_orderbook_pressure(books: dict, current_price: float,
                                direction: str, depth: int = 20) -> Tuple[int, str, Optional[float]]:
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
# 방안 3: OB Volume Imbalance  (기존 방안 4)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_orderbook_imbalance(books: dict, direction: str,
                                 depth: int = 10) -> Tuple[int, str]:
    """
    전체 bid/ask 잔량 비율로 단기 방향 압력 측정.

    방안 2(Wall Detection)와 상호보완:
      방안 2: 가격 근처 단일 대형 주문 탐지 (즉각 저항/지지)
      방안 3: 전체 호가창 균형 측정 (방향 압력 누적)
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

        bid_ratio = total_bid / total
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
# OHLCV 수집 (방안 4 Candle Momentum 전용)
# [v3.3] OI 제거 후 순수 OHLCV만 수집
# ══════════════════════════════════════════════════════════════════════════════

def fetch_ohlcv_micro(exchange, symbol: str, periods: int = 12) -> list:
    """
    5분봉 OHLCV 수집 — Candle Momentum(방안 4) 전용.
    [v3.3] fetch_oi_history 대체: OI 없이 OHLCV만 수집.
    반환: [{open, high, low, close, volume, ts}, ...]
    """
    try:
        swap  = _to_ccxt_swap(symbol)
        ohlcv = exchange.fetch_ohlcv(swap, "5m", limit=periods)
        result = [
            {
                "ts":     c[0],
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": float(c[5]),
            }
            for c in ohlcv
        ]
        logger.debug(f"[Micro/OHLCV] {len(result)}캔들 수집 ({symbol})")
        return result
    except Exception as e:
        logger.warning(f"[Micro/OHLCV] 수집 실패 ({symbol}): {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 방안 4: Candle Momentum / CVD Proxy  (기존 방안 5)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_candle_momentum(ohlcv_micro: list, direction: str) -> Tuple[int, str]:
    """
    CVD Proxy — 캔들 방향 × body 크기 × 거래량 가중치.

    계산:
      각 캔들: bull_weight = max(0, (close-open)/(high-low)) × volume
               bear_weight = max(0, (open-close)/(high-low)) × volume
      최신 캔들에 높은 시간 가중치 (i+1, i=0이 가장 오래됨)
      mom_score = weighted_bull / (weighted_bull + weighted_bear)

    기준:
      > 0.65: 매수 모멘텀  → 롱 유리
      < 0.35: 매도 모멘텀  → 숏 유리
    """
    direction = direction.upper()
    if (len(ohlcv_micro) < 3 or
            "open" not in ohlcv_micro[0] or
            "volume" not in ohlcv_micro[0]):
        return 0, ""

    try:
        recent = ohlcv_micro[-5:]
        w_bull = 0.0; w_bear = 0.0

        for i, c in enumerate(recent):
            o   = c.get("open",   c["close"])
            h   = c.get("high",   c["close"])
            l   = c.get("low",    c["close"])
            cl  = c["close"]
            vol = c.get("volume", 1.0)

            rng = h - l
            if rng <= 0 or vol <= 0:
                continue

            body_bull = max(0.0, (cl - o) / rng)
            body_bear = max(0.0, (o - cl) / rng)
            weight    = (i + 1) * vol

            w_bull += body_bull * weight
            w_bear += body_bear * weight

        total = w_bull + w_bear
        if total <= 0:
            return 0, ""

        mom_score = w_bull / total
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
# 방안 5: Mark Price Basis + Next Funding Rate  (기존 방안 6)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_mark_funding_data(exchange, symbol: str) -> dict:
    res = {
        "mark_price": None, "current_funding_rate": None,
        "next_funding_rate": None, "available": False,
    }
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

    res["available"] = (res["mark_price"] is not None or
                        res["next_funding_rate"] is not None)
    return res


def analyze_mark_funding_composite(mf: dict, current_price: float,
                                    direction: str) -> Tuple[int, str]:
    """
    마크가격 괴리 + 차기 펀딩비 복합 분석.

    OKX 실제 펀딩비 스케일:
      평상시: ±0.0001 ~ ±0.0005 (±0.01% ~ ±0.05%)
      상승장: +0.001  ~ +0.005  (+0.1% ~ +0.5%)
      극단:   > +0.01            (> 1%)

    임계값:
      mild:    |nf| > 0.0005  (0.05% — 방향 편향 시작)
      strong:  |nf| > 0.001   (0.1%  — 명확한 편향)
      extreme: |nf| > 0.003   (0.3%  — 강한 청산 위험)
    """
    direction = direction.upper()
    if not mf.get("available"):
        return 0, ""

    p = 0; parts = []

    # 마크가격 괴리
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

    # 차기 펀딩비
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

    # 복합 불리: 마크괴리 + 펀딩비 동일 방향
    if mark and mark > 0 and nf is not None and current_price > 0:
        basis = (current_price - mark) / mark
        if   direction == "LONG"  and basis > 0.002 and nf > 0.0005:
            p -= 3; parts.append("복합불리↑")
        elif direction == "SHORT" and basis < -0.002 and nf < -0.0005:
            p -= 3; parts.append("복합불리↓")

    return p, (" | ".join(f"[MF]{x}" for x in parts) if parts else "")


# ══════════════════════════════════════════════════════════════════════════════
# 방안 6: Account-Level vs Position-Level LS Divergence  (기존 방안 7)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_account_ls_ratio(exchange, symbol: str) -> Optional[float]:
    """
    OKX: GET /api/v5/rubik/stat/contracts/long-short-account-ratio
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


def analyze_ls_divergence(account_long_pct: Optional[float],
                           position_long_pct: float,
                           direction: str) -> Tuple[int, str]:
    """
    계좌 기준 vs 포지션 기준 롱 비율 괴리 → 고래 포지션 방향 추정.
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
# 통합 수집  [v3.3: OI 제거, ohlcv_micro 키 사용]
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_microstructure(exchange, symbol: str) -> dict:
    """
    5개 데이터 포인트 수집 (OI 제거됨).

    데이터:
      liquidation  → 방안 1 (Liq Cascade)
      orderbook    → 방안 2 (OB Wall) + 방안 3 (OBI)
      ohlcv_micro  → 방안 4 (Candle Momentum)
      mark_funding → 방안 5 (Mark/Funding)
      account_ls   → 방안 6 (LS Divergence)
    """
    logger.info(f"[Microstructure] 📡 수집: {symbol}")

    liq_data   = fetch_liquidation_data(exchange, symbol)
    ob_data    = fetch_orderbook_data(exchange, symbol)
    ohlcv_data = fetch_ohlcv_micro(exchange, symbol)         # OI 없이 OHLCV만
    mf_data    = fetch_mark_funding_data(exchange, symbol)
    acct_ls    = fetch_account_ls_ratio(exchange, symbol)

    data = {
        "liquidation":  liq_data,
        "orderbook":    ob_data,
        "ohlcv_micro":  ohlcv_data,   # [v3.3] oi_history → ohlcv_micro
        "mark_funding": mf_data,
        "account_ls":   acct_ls,
    }

    status = {
        "Liq": liq_data.get("available", False),
        "OB":  ob_data.get("available",  False),
        "CM":  len(ohlcv_data) > 0 and "open" in (ohlcv_data[0] if ohlcv_data else {}),
        "MF":  mf_data.get("available",  False),
        "LS":  acct_ls is not None,
    }
    ok = sum(status.values())
    logger.info(f"[Microstructure] ✅ {ok}/5 방안 활성  {status}")

    if ok < 5:
        failed = [k for k, v in status.items() if not v]
        logger.info(f"[Microstructure] ⚠️ 비활성: {failed}")

    return data


# ══════════════════════════════════════════════════════════════════════════════
# 통합 패널티 계산  [v3.3: 6개 방안, OI 제거]
# ══════════════════════════════════════════════════════════════════════════════

def compute_microstructure_penalties(
    micro_data:        dict,
    current_price:     float,
    direction:         str,
    regime:            str,
    percent_b:         float,
    taker_buy_pct:     float,
    position_long_pct: float,
) -> dict:
    """
    6개 분석 → 패널티/보너스 합산 후 캡(-30pt) 적용.

    마이크로구조 패널티는 소프트 패널티(×배율) 이후 덧셈 적용:
      final_score = (base + bonus) × soft_penalty + micro_penalty
    → 독립적 order flow 근거로 partial rescue/block 허용 (의도적 설계).
    → 방향 역풍이면 BOS ×0.82 이후에도 추가 차감.
    → 방향 순풍이면 partial offset — 진짜 order flow 확인 시 합리적.
    """
    direction = direction.upper()
    checks = []; suggested = None

    # 방안 1: Liquidation Cascade
    p1, r1 = analyze_liquidation_cascade(
        micro_data.get("liquidation", {}), taker_buy_pct, direction
    )
    if r1:
        checks.append(("LiqCascade", p1, r1))

    # 방안 2: Order Book Wall
    p2, r2, entry = analyze_orderbook_pressure(
        micro_data.get("orderbook", {}), current_price, direction
    )
    if r2:
        checks.append(("OrderBook", p2, r2))
    if entry:
        suggested = entry

    # 방안 3: OB Volume Imbalance
    p3, r3 = analyze_orderbook_imbalance(
        micro_data.get("orderbook", {}), direction
    )
    if r3:
        checks.append(("OBImbalance", p3, r3))

    # 방안 4: Candle Momentum (ohlcv_micro 키 사용)
    p4, r4 = analyze_candle_momentum(
        micro_data.get("ohlcv_micro", []), direction
    )
    if r4:
        checks.append(("CandleMom", p4, r4))

    # 방안 5: Mark Price + Funding Rate
    p5, r5 = analyze_mark_funding_composite(
        micro_data.get("mark_funding", {}), current_price, direction
    )
    if r5:
        checks.append(("MarkFunding", p5, r5))

    # 방안 6: LS Divergence
    p6, r6 = analyze_ls_divergence(
        micro_data.get("account_ls"), position_long_pct, direction
    )
    if r6:
        checks.append(("LSDivergence", p6, r6))

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
