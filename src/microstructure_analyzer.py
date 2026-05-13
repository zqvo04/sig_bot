"""
microstructure_analyzer.py  (v2.1 — 최종 버전)
──────────────────────────────────────────────────────────────────────────────
v2 → v2.1 변경:
  [Fix Issue 6] direction 대소문자 정규화
    - 모든 direction 수신 함수 첫 줄에 direction = direction.upper() 추가
    - 기존 코드(소문자 "long"/"short")와 호환
──────────────────────────────────────────────────────────────────────────────
"""
import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

MICRO_PENALTY_CAP = -30


def _to_instId(s: str) -> str: return s.replace('/', '-').split(':')[0] + '-SWAP'
def _to_uly(s: str)    -> str: return s.replace('/', '-').split(':')[0]


# ──────────────────────────────────────────────────────────────────────────────
# 방안 1: Liquidation Cascade Discriminator
# ──────────────────────────────────────────────────────────────────────────────

def fetch_liquidation_data(exchange, symbol: str, lookback_minutes: int = 30) -> dict:
    empty = {'long_liq_vol': 0, 'short_liq_vol': 0, 'long_liq_count': 0,
             'short_liq_count': 0, 'total_vol': 0, 'available': False}
    try:
        resp = exchange.publicGetPublicLiquidationOrders(
            {'instType': 'SWAP', 'uly': _to_uly(symbol), 'state': 'filled', 'limit': '100'}
        )
        if not resp.get('data'):
            return empty
        cutoff = (time.time() - lookback_minutes * 60) * 1000
        lv = sv = lc = sc = 0.0
        for item in resp['data']:
            for d in item.get('details', []):
                if float(d.get('ts', 0)) < cutoff: continue
                sz = float(d.get('sz', 0))
                if d.get('side') == 'buy':    sv += sz; sc += 1
                elif d.get('side') == 'sell': lv += sz; lc += 1
        return {'long_liq_vol': lv, 'short_liq_vol': sv,
                'long_liq_count': int(lc), 'short_liq_count': int(sc),
                'total_vol': lv + sv, 'available': True}
    except Exception as e:
        logger.warning(f"[Micro/Liq] 수집 실패 ({symbol}): {e}"); return empty


def analyze_liquidation_cascade(liq: dict, taker_buy_pct: float, direction: str) -> Tuple[int, str]:
    direction = direction.upper()  # [Fix Issue 6]
    if not liq.get('available') or liq['total_vol'] == 0: return 0, ""
    t = liq['total_vol']
    sr = liq['short_liq_vol'] / t
    lr = liq['long_liq_vol']  / t
    tk = taker_buy_pct / 100.0
    p, r = 0, ""
    if direction == "LONG":
        if tk > 0.75 and sr > 0.65:
            p = -12; r = f"⚠️[Liq] 숏청산 주도 taker매수(스퀴즈말미) short_liq:{sr:.0%} taker:{tk:.0%}"
        elif lr > 0.70 and liq['long_liq_count'] >= 5:
            p = -15; r = f"⚠️[Liq] 롱청산 캐스케이드 진행중 long_liq:{lr:.0%} ×{liq['long_liq_count']}건"
        elif lr > 0.60 and tk > 0.65 and liq['long_liq_count'] < 3:
            p = +8;  r = "✅[Liq] 롱청산 완료 후 실매수 반전"
    elif direction == "SHORT":
        if tk < 0.30 and lr > 0.65:
            p = -10; r = f"⚠️[Liq] 롱청산 말미 숏진입 위험 long_liq:{lr:.0%}"
        elif sr > 0.70 and liq['short_liq_count'] >= 5:
            p = -15; r = f"⚠️[Liq] 숏청산(스퀴즈) 캐스케이드 진행중 short_liq:{sr:.0%} ×{liq['short_liq_count']}건"
        elif sr > 0.60 and tk < 0.35 and liq['short_liq_count'] < 3:
            p = +8;  r = "✅[Liq] 숏청산 완료 후 실매도 반전"
    return p, r


# ──────────────────────────────────────────────────────────────────────────────
# 방안 2: Order Book Structural Pressure
# ──────────────────────────────────────────────────────────────────────────────

def fetch_orderbook_data(exchange, symbol: str, depth: int = 20) -> dict:
    try:
        b = exchange.fetch_order_book(symbol, limit=depth)
        return {'bids': b['bids'], 'asks': b['asks'], 'available': True}
    except Exception as e:
        logger.warning(f"[Micro/OB] 수집 실패 ({symbol}): {e}")
        return {'bids': [], 'asks': [], 'available': False}


def analyze_orderbook_pressure(
    books: dict, current_price: float, direction: str, depth: int = 20
) -> Tuple[int, str, Optional[float]]:
    direction = direction.upper()  # [Fix Issue 6]
    if not books.get('available') or not books['bids'] or not books['asks']:
        return 0, "", None
    bids, asks = books['bids'], books['asks']
    try:
        avg_ask = sum(a[1] for a in asks[:depth]) / max(len(asks[:depth]), 1)
        avg_bid = sum(b[1] for b in bids[:depth]) / max(len(bids[:depth]), 1)
        zone = 0.01; WALL = 4.0
        ask_walls = [(a[0], a[1]) for a in asks if a[0] <= current_price*(1+zone) and a[1] > avg_ask*WALL]
        bid_walls = [(b[0], b[1]) for b in bids if b[0] >= current_price*(1-zone) and b[1] > avg_bid*WALL]
        tight = 0.005
        tb = sum(b[1] for b in bids if b[0] > current_price*(1-tight))
        ta = sum(a[1] for a in asks if a[0] < current_price*(1+tight))
        imbalance = tb / (tb + ta) if (tb + ta) > 0 else 0.5
        p, r, suggested = 0, "", None
        if direction == "LONG":
            if ask_walls:
                wp, wv = ask_walls[0]; wd = (wp - current_price)/current_price; wr = wv/avg_ask
                if wd < 0.003:   p = -12; r = f"⚠️[OB] ask벽 {wd:.2%}내 {wr:.1f}배 — 즉시저항"
                elif wd < 0.005: p = -5;  r = f"⚠️[OB] ask벽 {wd:.2%}내 {wr:.1f}배"
            if imbalance < 0.35: logger.debug(f"[OB/참고] 매도우세 {imbalance:.0%} (패널티 미적용)")
            if bid_walls: suggested = bid_walls[-1][0] * 1.001
        elif direction == "SHORT":
            if bid_walls:
                wp, wv = bid_walls[0]; wd = (current_price - wp)/current_price; wr = wv/avg_bid
                if wd < 0.003:   p = -12; r = f"⚠️[OB] bid벽 {wd:.2%}내 {wr:.1f}배 — 즉시지지"
                elif wd < 0.005: p = -5;  r = f"⚠️[OB] bid벽 {wd:.2%}내 {wr:.1f}배"
            if imbalance > 0.65: logger.debug(f"[OB/참고] 매수우세 {imbalance:.0%} (패널티 미적용)")
            if ask_walls: suggested = ask_walls[-1][0] * 0.999
        return p, r, suggested
    except Exception as e:
        logger.warning(f"[Micro/OB] 분석 실패: {e}"); return 0, "", None


# ──────────────────────────────────────────────────────────────────────────────
# 방안 3: OI Velocity Matrix
# ──────────────────────────────────────────────────────────────────────────────

def fetch_oi_history(exchange, symbol: str, periods: int = 12) -> list:
    try:
        instId = _to_instId(symbol)
        try:
            oi_list = exchange.fetch_open_interest_history(symbol, '5m', limit=periods)
            ohlcv   = exchange.fetch_ohlcv(symbol, '5m', limit=periods)
            pm      = {c[0]: c[4] for c in ohlcv}
            return [{'oi': float(x.get('openInterestAmount', 0)),
                     'close': pm.get(x.get('timestamp', 0), 0),
                     'ts': x.get('timestamp', 0)} for x in oi_list[-periods:]]
        except Exception:
            resp = exchange.publicGetRubikStatContractsOpenInterestHistory(
                {'instType': 'SWAP', 'instId': instId, 'period': '5m', 'limit': str(periods)}
            )
            if not resp.get('data'): return []
            ohlcv = exchange.fetch_ohlcv(symbol, '5m', limit=periods)
            pm    = {str(c[0]): c[4] for c in ohlcv}
            result = []
            for row in reversed(resp['data']):
                ts = int(row[0])
                result.append({'oi': float(row[1]), 'close': pm.get(str(ts), 0), 'ts': ts})
            return result[-periods:]
    except Exception as e:
        logger.warning(f"[Micro/OI] 수집 실패 ({symbol}): {e}"); return []


def analyze_oi_velocity(oi_history: list, direction: str, regime: str = "") -> Tuple[int, str]:
    direction = direction.upper()  # [Fix Issue 6]
    if len(oi_history) < 6: return 0, ""
    try:
        e3 = oi_history[:3]; r3 = oi_history[-3:]
        avg_oi_e = sum(x['oi']    for x in e3) / 3
        avg_oi_r = sum(x['oi']    for x in r3) / 3
        avg_px_e = sum(x['close'] for x in e3) / 3
        avg_px_r = sum(x['close'] for x in r3) / 3
        if avg_oi_e == 0 or avg_px_e == 0: return 0, ""
        oi_d = (avg_oi_r - avg_oi_e) / avg_oi_e
        px_d = (avg_px_r - avg_px_e) / avg_px_e
        TH   = 0.008
        oi_up = oi_d > TH; oi_dn = oi_d < -TH
        px_up = px_d > TH; px_dn = px_d < -TH
        if not (oi_up or oi_dn) and not (px_up or px_dn): return 0, ""
        mult = 0.6 if regime == "EXPLOSIVE" else 1.0
        p, name, r = 0, "NEUTRAL", ""
        if oi_up and px_up:
            name = "ACCUMULATION"
            if direction == "SHORT": p = int(-10*mult); r = f"⚠️[OI] ACCUMULATION 중 숏역행 (oi:{oi_d:+.1%} px:{px_d:+.1%})"
            else:                    p = +5;             r = "✅[OI] ACCUMULATION 추세지속"
        elif oi_up and px_dn:
            name = "SHORT_BUILDUP"
            if direction == "LONG":  p = int(-10*mult); r = f"⚠️[OI] SHORT_BUILDUP 중 롱역행 (oi:{oi_d:+.1%} px:{px_d:+.1%})"
            else:                    p = +5;             r = "✅[OI] SHORT_BUILDUP 하락추세지속"
        elif oi_dn and px_up:
            name = "SHORT_SQUEEZE"
            if direction == "LONG":  p = int(-12*mult); r = f"⚠️[OI] SHORT_SQUEEZE 말미 롱위험 (oi:{oi_d:+.1%} px:{px_d:+.1%})"
        elif oi_dn and px_dn:
            name = "LONG_LIQUIDATION"
            if direction == "LONG":                      p = int(-15*mult); r = f"⚠️[OI] LONG_LIQUIDATION 중 롱금지 (oi:{oi_d:+.1%} px:{px_d:+.1%})"
            elif direction == "SHORT" and oi_d < -0.03: p = int(-6*mult);  r = "⚠️[OI] LONG_LIQUIDATION 과매도 숏주의"
        logger.debug(f"[OI/{regime}] {name} oi_δ:{oi_d:.2%} px_δ:{px_d:.2%} → {p:+d}pt")
        return p, r
    except Exception as e:
        logger.warning(f"[Micro/OI] 분석 실패: {e}"); return 0, ""


# ──────────────────────────────────────────────────────────────────────────────
# 방안 4: BB Direction Compatibility Filter
# ──────────────────────────────────────────────────────────────────────────────

def _ranging_bb_raw(pb: float, d: str) -> Tuple[int, str]:
    d = d.upper()
    if d == "SHORT":
        if pb < 0:      return -18, f"BB하단이탈({pb:.2f}) 숏"
        elif pb < 0.15: return -12, f"BB하단근처({pb:.2f}) 숏"
    elif d == "LONG":
        if pb > 1.0:    return -18, f"BB상단이탈({pb:.2f}) 롱"
        elif pb > 0.80: return -12, f"BB상단근처({pb:.2f}) 롱"
        elif pb < -0.05:return -15, f"BB하단이탈({pb:.2f}) 롱(하락모멘텀)"
    return 0, ""


def analyze_bb_direction_compatibility(percent_b: float, direction: str, regime: str) -> Tuple[int, str]:
    direction = direction.upper()  # [Fix Issue 6]
    p, r = 0, ""
    if regime == "RANGING":
        if direction == "SHORT":
            if   percent_b < 0:    p = -18; r = f"⚠️[BB] RANGING BB하단이탈(%B={percent_b:.2f}) — 반등위험"
            elif percent_b < 0.15: p = -12; r = f"⚠️[BB] RANGING BB하단근처(%B={percent_b:.2f}) — 반등구간"
            elif percent_b < 0.25: p = -6;  r = f"⚠️[BB] RANGING BB하단권(%B={percent_b:.2f}) 숏주의"
            elif percent_b > 1.05: p = -15; r = f"⚠️[BB] RANGING BB상단이탈(%B={percent_b:.2f}) 숏위험"
        elif direction == "LONG":
            if   percent_b > 1.0:  p = -18; r = f"⚠️[BB] RANGING BB상단이탈(%B={percent_b:.2f}) — 저항위험"
            elif percent_b > 0.80: p = -12; r = f"⚠️[BB] RANGING BB상단근처(%B={percent_b:.2f}) — 저항구간"
            elif percent_b > 0.70: p = -6;  r = f"⚠️[BB] RANGING BB상단권(%B={percent_b:.2f}) 롱주의"
            elif percent_b < -0.05:p = -15; r = f"⚠️[BB] RANGING BB하단이탈(%B={percent_b:.2f}) 롱진입 — 하락모멘텀 역행"
    elif regime == "TRENDING":
        if   direction == "SHORT" and percent_b < 0:   p = +5; r = "✅[BB] TRENDING BB하단이탈 하락추세 강도확인"
        elif direction == "LONG"  and percent_b > 1.0: p = +5; r = "✅[BB] TRENDING BB상단이탈 상승추세 강도확인"
    elif regime in ("EXPLOSIVE", "SQUEEZE"):
        base_p, base_r = _ranging_bb_raw(percent_b, direction)
        p = int(base_p * 0.3)
        r = f"({regime} ×0.3) {base_r}" if base_r and p != 0 else ""
    return p, r


# ──────────────────────────────────────────────────────────────────────────────
# 방안 6: Mark Price Basis + Next Funding Rate
# ──────────────────────────────────────────────────────────────────────────────

def fetch_mark_funding_data(exchange, symbol: str) -> dict:
    res = {'mark_price': None, 'current_funding_rate': None, 'next_funding_rate': None, 'available': False}
    try:
        resp = exchange.publicGetPublicMarkPrice({'instType': 'SWAP', 'instId': _to_instId(symbol)})
        if resp.get('data'): res['mark_price'] = float(resp['data'][0]['markPx'])
    except Exception as e: logger.warning(f"[Micro/MF] 마크가격 실패: {e}")
    try:
        fr = exchange.fetch_funding_rate(symbol)
        res['current_funding_rate'] = float(fr.get('fundingRate',     0) or 0)
        res['next_funding_rate']    = float(fr.get('nextFundingRate', 0) or 0)
    except Exception as e: logger.warning(f"[Micro/MF] 펀딩비 실패: {e}")
    res['available'] = res['mark_price'] is not None or res['next_funding_rate'] is not None
    return res


def analyze_mark_funding_composite(mf: dict, current_price: float, direction: str) -> Tuple[int, str]:
    direction = direction.upper()  # [Fix Issue 6]
    if not mf.get('available'): return 0, ""
    p = 0; parts = []
    mark = mf.get('mark_price')
    if mark and mark > 0:
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
    nf = mf.get('next_funding_rate')
    if nf is not None:
        if direction == "LONG":
            if   nf > 0.030: p -= 12; parts.append(f"⚠️차기펀딩롱불리{nf:.4%}")
            elif nf > 0.015: p -= 6;  parts.append(f"⚠️차기펀딩↑{nf:.4%}")
            elif nf < -0.015:p += 5;  parts.append(f"✅차기펀딩롱유리{nf:.4%}")
        elif direction == "SHORT":
            if   nf < -0.030:p -= 12; parts.append(f"⚠️차기펀딩숏불리{nf:.4%}")
            elif nf < -0.015:p -= 6;  parts.append(f"⚠️차기펀딩↓{nf:.4%}")
            elif nf >  0.015:p += 5;  parts.append(f"✅차기펀딩숏유리{nf:.4%}")
    if mark and mark > 0 and nf is not None:
        basis = (current_price - mark) / mark
        if   direction == "LONG"  and basis > 0.002 and nf > 0.010:  p -= 3; parts.append("복합불리↑")
        elif direction == "SHORT" and basis < -0.002 and nf < -0.010: p -= 3; parts.append("복합불리↓")
    return p, " | ".join(f"[MF]{x}" for x in parts) if parts else ""


# ──────────────────────────────────────────────────────────────────────────────
# 방안 7: Account-Level vs Position-Level LS Divergence
# ──────────────────────────────────────────────────────────────────────────────

def fetch_account_ls_ratio(exchange, symbol: str) -> Optional[float]:
    try:
        base = symbol.split('/')[0] + '-' + symbol.split('/')[1].split(':')[0]
        resp = exchange.publicGetRubikStatContractsLongShortAccountRatio(
            {'instId': base, 'period': '5m', 'limit': '1'}
        )
        if not resp.get('data'): return None
        ratio = float(resp['data'][0][1])
        return ratio / (1.0 + ratio)
    except Exception as e:
        logger.warning(f"[Micro/LS] 계좌LS 실패 ({symbol}): {e}"); return None


def analyze_ls_divergence(account_long_pct: Optional[float], position_long_pct: float, direction: str) -> Tuple[int, str]:
    direction = direction.upper()  # [Fix Issue 6]
    if account_long_pct is None: return 0, ""
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


# ──────────────────────────────────────────────────────────────────────────────
# 통합 수집
# ──────────────────────────────────────────────────────────────────────────────

def fetch_all_microstructure(exchange, symbol: str) -> dict:
    logger.info(f"[Microstructure] 📡 수집: {symbol}")
    data = {
        'liquidation':  fetch_liquidation_data(exchange, symbol),
        'orderbook':    fetch_orderbook_data(exchange, symbol),
        'oi_history':   fetch_oi_history(exchange, symbol),
        'mark_funding': fetch_mark_funding_data(exchange, symbol),
        'account_ls':   fetch_account_ls_ratio(exchange, symbol),
    }
    ok = sum([data['liquidation']['available'], data['orderbook']['available'],
              len(data['oi_history']) > 0, data['mark_funding']['available'],
              data['account_ls'] is not None])
    logger.info(f"[Microstructure] ✅ {ok}/5 방안 활성")
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 통합 패널티 계산 (메인 진입점)
# ──────────────────────────────────────────────────────────────────────────────

def compute_microstructure_penalties(
    micro_data:        dict,
    current_price:     float,
    direction:         str,          # "long"/"LONG" 모두 허용
    regime:            str,
    percent_b:         float,
    taker_buy_pct:     float,
    position_long_pct: float,
) -> dict:
    direction = direction.upper()    # [Fix Issue 6] 정규화

    checks = []; suggested = None

    p1, r1 = analyze_liquidation_cascade(micro_data.get('liquidation', {}), taker_buy_pct, direction)
    if r1: checks.append(('LiqCascade', p1, r1))

    p2, r2, entry = analyze_orderbook_pressure(micro_data.get('orderbook', {}), current_price, direction)
    if r2: checks.append(('OrderBook', p2, r2))
    if entry: suggested = entry

    p3, r3 = analyze_oi_velocity(micro_data.get('oi_history', []), direction, regime)
    if r3: checks.append(('OIVelocity', p3, r3))

    p4, r4 = analyze_bb_direction_compatibility(percent_b, direction, regime)
    if r4: checks.append(('BBCompat', p4, r4))

    p6, r6 = analyze_mark_funding_composite(micro_data.get('mark_funding', {}), current_price, direction)
    if r6: checks.append(('MarkFunding', p6, r6))

    p7, r7 = analyze_ls_divergence(micro_data.get('account_ls'), position_long_pct, direction)
    if r7: checks.append(('LSDivergence', p7, r7))

    raw   = sum(p for _, p, _ in checks)
    total = max(raw, MICRO_PENALTY_CAP)

    if checks:
        cap_note = f" [캡 {MICRO_PENALTY_CAP}pt 적용]" if raw < MICRO_PENALTY_CAP else ""
        logger.info(f"[Microstructure/{direction}] 합계: {raw:+d}pt → {total:+d}pt{cap_note}")
        for name, p, r in checks:
            logger.info(f"  {'🔴' if p < 0 else '🟢'} [{name}] {p:+d}pt  {r}")
    else:
        logger.debug(f"[Microstructure/{direction}] 패널티/보너스 없음")

    return {
        'total_penalty':   total,
        'raw_total':       raw,
        'details':         checks,
        'suggested_entry': suggested,
    }
