"""
analysis_engine.py — 분석 엔진 (개선판 + 트레이더 업그레이드)
A: EMA를 배율로 전환 (역추세 신호 구조적 제거)
E: 강제청산 분석
F: Taker Buy/Sell Volume 분석
H: 시장 국면 자동 분류
+ 캔들 패턴 / 시장 구조 / 거래량 다이버전스 분석
"""
import logging
from typing import Optional
import numpy as np
import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
# 1. 기본 유틸
# ══════════════════════════════════════════════

def calculate_atr(df: pd.DataFrame, period: int = None) -> pd.Series:
    if df is None or df.empty or "high" not in df.columns:
        return pd.Series(dtype=float)
    period     = period or config.ATR_PERIOD
    high       = df["high"].astype(float)
    low        = df["low"].astype(float)
    close      = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/period, adjust=False).mean()


def get_atr_state(df: pd.DataFrame) -> dict:
    if df is None or len(df) < config.ATR_PERIOD + 5:
        return {"current":0.0,"pct":0.0,"expanding":False,"ratio":1.0}
    atr   = calculate_atr(df)
    cur   = float(atr.iloc[-1])
    avg   = float(atr.iloc[-20:].mean()) if len(atr)>=20 else float(atr.mean())
    price = float(df["close"].iloc[-1])
    ratio = cur/avg if avg>0 else 1.0
    return {"current":round(cur,6),"pct":round(cur/price*100,4),"expanding":bool(ratio>1.3),"ratio":round(ratio,3)}


def check_volume_confirmation(df: pd.DataFrame) -> dict:
    lb = config.VOLUME_CONFIRM_LOOKBACK
    if df is None or len(df) < lb+1:
        return {"confirmed":False,"strong":False,"ratio":0.0,"score":0.0,"current_vol":0.0,"avg_vol":0.0}
    cur_vol = float(df["volume"].iloc[-1])
    avg_vol = float(df["volume"].iloc[-(lb+1):-1].mean())
    if avg_vol <= 0:
        return {"confirmed":False,"strong":False,"ratio":0.0,"score":0.0,"current_vol":0.0,"avg_vol":0.0}
    ratio     = cur_vol / avg_vol
    confirmed = ratio >= config.VOLUME_SPIKE_MULTIPLIER
    strong    = ratio >= config.VOLUME_STRONG_MULTIPLIER
    if ratio < 1.0:              score = ratio*20
    elif ratio < config.VOLUME_SPIKE_MULTIPLIER:
        score = 20+(ratio-1.0)/(config.VOLUME_SPIKE_MULTIPLIER-1.0)*30
    elif ratio < config.VOLUME_STRONG_MULTIPLIER:
        score = 50+(ratio-config.VOLUME_SPIKE_MULTIPLIER)/(config.VOLUME_STRONG_MULTIPLIER-config.VOLUME_SPIKE_MULTIPLIER)*30
    else:
        score = min(100, 80+(ratio-config.VOLUME_STRONG_MULTIPLIER)*10)
    return {"confirmed":confirmed,"strong":strong,"ratio":round(ratio,3),"score":round(min(100,max(0,score)),2),
            "current_vol":round(cur_vol,2),"avg_vol":round(avg_vol,2)}


# ══════════════════════════════════════════════
# 2. 멀티TF RSI + 다이버전스
# ══════════════════════════════════════════════

def calculate_rsi(df: pd.DataFrame, period: int = None) -> pd.Series:
    period = period or config.RSI_PERIOD
    close  = df["close"].astype(float)
    delta  = close.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    alpha  = 1.0/period
    ag     = gain.ewm(alpha=alpha, adjust=False).mean()
    al     = loss.ewm(alpha=alpha, adjust=False).mean()
    rs     = ag / al.replace(0, np.nan)
    return (100 - (100/(1+rs))).fillna(50)


def _rsi_to_score(v: float) -> tuple:
    if v <= 20:       ls = 95
    elif v <= 30:     ls = 85 - (v-20)/10*10
    elif v <= 50:     ls = 75 - (v-30)/20*25
    elif v <= 70:     ls = 50 - (v-50)/20*30
    else:             ls = max(5, 20 - (v-70)*1.5)
    return round(min(100,max(0,ls)),2), round(min(100,max(0,100-ls)),2)


def analyze_mtf_rsi(df_15m, df_1h, df_4h) -> dict:
    def _get_rsi_val(df):
        if df is None or len(df) < config.RSI_PERIOD + 1:
            return None
        return float(calculate_rsi(df).iloc[-1])

    v_15m = _get_rsi_val(df_15m)
    v_1h  = _get_rsi_val(df_1h)
    v_4h  = _get_rsi_val(df_4h)

    weights   = [(v_15m, 0.50), (v_1h, 0.30), (v_4h, 0.20)]
    available = [(v, w) for v, w in weights if v is not None]
    if not available:
        return _empty_rsi()

    total_w    = sum(w for _, w in available)
    v_weighted = sum(v * w for v, w in available) / total_w
    v_entry    = v_15m if v_15m is not None else v_weighted
    state = ("oversold"   if v_entry <= config.RSI_OVERSOLD  else
             "overbought" if v_entry >= config.RSI_OVERBOUGHT else "neutral")

    long_score_raw, short_score_raw = _rsi_to_score(v_weighted)

    pullback_long_strong  = (v_1h is not None and v_1h  > 58 and v_15m is not None and v_15m < 40)
    pullback_long_weak    = (v_1h is not None and v_1h  > 50 and v_15m is not None and v_15m < 46 and not pullback_long_strong)
    pullback_long_micro   = (v_1h is not None and v_1h  > 45 and v_15m is not None and v_15m < 50 and not pullback_long_strong and not pullback_long_weak)
    pullback_long         = pullback_long_strong or pullback_long_weak or pullback_long_micro

    pullback_short_strong = (v_1h is not None and v_1h  < 42 and v_15m is not None and v_15m > 60)
    pullback_short_weak   = (v_1h is not None and v_1h  < 50 and v_15m is not None and v_15m > 54 and not pullback_short_strong)
    pullback_short_micro  = (v_1h is not None and v_1h  < 55 and v_15m is not None and v_15m > 50 and not pullback_short_strong and not pullback_short_weak)
    pullback_short        = pullback_short_strong or pullback_short_weak or pullback_short_micro

    macro_bull = v_4h is not None and v_4h > 52
    macro_bear = v_4h is not None and v_4h < 48

    pb_long_adj  = (14 if pullback_long_strong  else 9 if pullback_long_weak  else 5 if pullback_long_micro  else 0)
    pb_short_adj = (14 if pullback_short_strong else 9 if pullback_short_weak else 5 if pullback_short_micro else 0)

    if pullback_long:
        long_score_raw  = min(100, long_score_raw  + pb_long_adj)
        short_score_raw = max(0,   short_score_raw - pb_long_adj)
    if pullback_short:
        short_score_raw = min(100, short_score_raw + pb_short_adj)
        long_score_raw  = max(0,   long_score_raw  - pb_short_adj)
    if macro_bull and long_score_raw > 50:
        long_score_raw  = min(100, long_score_raw  + 5)
    if macro_bear and short_score_raw > 50:
        short_score_raw = min(100, short_score_raw + 5)

    long_score  = round(min(100,max(0,long_score_raw)),  2)
    short_score = round(min(100,max(0,short_score_raw)), 2)

    bull_div = bool(_detect_bull_div(df_15m, calculate_rsi(df_15m))) if df_15m is not None and len(df_15m)>=12 else False
    bear_div = bool(_detect_bear_div(df_15m, calculate_rsi(df_15m))) if df_15m is not None and len(df_15m)>=12 else False

    v15_str = f"{v_15m:.1f}" if v_15m is not None else "N/A"
    v1h_str = f"{v_1h:.1f}"  if v_1h  is not None else "N/A"
    v4h_str = f"{v_4h:.1f}"  if v_4h  is not None else "N/A"
    logger.info(
        f"[MTF-RSI] 15m:{v15_str} 1h:{v1h_str} 4h:{v4h_str} "
        f"가중:{v_weighted:.1f} [{state}] "
        f"롱:{long_score:.1f}pt 숏:{short_score:.1f}pt"
        + (" ★눌림목롱(강)" if pullback_long_strong  else " ★눌림목롱(약)" if pullback_long_weak   else " ★눌림목롱(미)" if pullback_long_micro  else "")
        + (" ★눌림목숏(강)" if pullback_short_strong else " ★눌림목숏(약)" if pullback_short_weak  else " ★눌림목숏(미)" if pullback_short_micro else "")
    )
    return {
        "value":              round(v_entry, 2),
        "value_1h":           round(v_1h, 2)  if v_1h  is not None else None,
        "value_4h":           round(v_4h, 2)  if v_4h  is not None else None,
        "value_weighted":     round(v_weighted, 2),
        "state":              state,
        "long_score":         long_score,
        "short_score":        short_score,
        "bullish_divergence": bull_div,
        "bearish_divergence": bear_div,
        "pullback_long":      pullback_long,
        "pullback_short":     pullback_short,
        "pullback_long_strong":  pullback_long_strong,
        "pullback_short_strong": pullback_short_strong,
    }


def _detect_bull_div(df, rsi, lb=6) -> bool:
    if df is None or len(df)<lb*2: return False
    c=df["close"].values; r=rsi.values
    return bool(c[-lb:].min()<c[-lb*2:-lb].min() and r[-lb:].min()>r[-lb*2:-lb].min())

def _detect_bear_div(df, rsi, lb=6) -> bool:
    if df is None or len(df)<lb*2: return False
    c=df["close"].values; r=rsi.values
    return bool(c[-lb:].max()>c[-lb*2:-lb].max() and r[-lb:].max()<r[-lb*2:-lb].max())

def _empty_rsi() -> dict:
    return {"value":50.0,"value_1h":None,"value_4h":None,"value_weighted":50.0,
            "state":"neutral","long_score":50.0,"short_score":50.0,
            "bullish_divergence":False,"bearish_divergence":False,
            "pullback_long":False,"pullback_short":False,
            "pullback_long_strong":False,"pullback_short_strong":False}

def get_rsi_signal(df: pd.DataFrame) -> dict:
    if df is None or len(df) < config.RSI_PERIOD+1:
        return _empty_rsi()
    rsi_s = calculate_rsi(df)
    v     = float(rsi_s.iloc[-1])
    state = "oversold" if v<=config.RSI_OVERSOLD else ("overbought" if v>=config.RSI_OVERBOUGHT else "neutral")
    ls, ss = _rsi_to_score(v)
    bull_div = bool(_detect_bull_div(df, rsi_s))
    bear_div = bool(_detect_bear_div(df, rsi_s))
    return {"value":round(v,2),"value_1h":None,"value_4h":None,"value_weighted":round(v,2),
            "state":state,"long_score":ls,"short_score":ss,
            "bullish_divergence":bull_div,"bearish_divergence":bear_div,
            "pullback_long":False,"pullback_short":False,
            "pullback_long_strong":False,"pullback_short_strong":False}


# ══════════════════════════════════════════════
# 3. 볼린저밴드
# ══════════════════════════════════════════════

def analyze_bollinger_bands(df: pd.DataFrame) -> dict:
    period = config.BOLLINGER_PERIOD; std_dev = config.BOLLINGER_STD
    if df is None or len(df)<period+1:
        return _empty_bb()
    close = df["close"].astype(float)
    mid   = close.rolling(period).mean()
    std   = close.rolling(period).std()
    upper = mid + std_dev*std
    lower = mid - std_dev*std
    bw_s  = (upper-lower)/mid.replace(0,np.nan)
    cur_bw= float(bw_s.iloc[-1]) if not pd.isna(bw_s.iloc[-1]) else 0.0
    avg_bw= float(bw_s.iloc[-50:].mean()) if len(bw_s)>=50 else (
            float(bw_s.iloc[-20:].mean()) if len(bw_s)>=20 else cur_bw)
    squeeze = bool(cur_bw < avg_bw*config.REGIME_SQUEEZE_RATIO and avg_bw>0)
    c_close = float(close.iloc[-1])
    c_upper = float(upper.iloc[-1]); c_lower = float(lower.iloc[-1]); c_mid = float(mid.iloc[-1])
    band_range = c_upper - c_lower
    if band_range <= 0: return _empty_bb()
    pct_b = (c_close - c_lower) / band_range

    if pct_b<=0.0:      ls,ss,state=92,8,"lower_breakout"
    elif pct_b<=0.15:   ls,ss,state=82,18,"near_lower"
    elif pct_b<=0.35:   ls,ss,state=65,35,"lower_zone"
    elif pct_b<=0.65:   ls,ss,state=50,50,"middle"
    elif pct_b<=0.85:   ls,ss,state=35,65,"upper_zone"
    elif pct_b<=1.0:    ls,ss,state=18,82,"near_upper"
    else:               ls,ss,state=8,92,"upper_breakout"

    pctb_s = (close-lower)/(upper-lower).replace(0,np.nan).fillna(0.5)
    lower_streak=0; upper_streak=0
    for pb in reversed(pctb_s.iloc[-10:].values):
        if pb<0.0: lower_streak+=1
        else: break
    for pb in reversed(pctb_s.iloc[-10:].values):
        if pb>1.0: upper_streak+=1
        else: break

    return {"long_score":ls,"short_score":ss,"pct_b":round(pct_b,4),"squeeze":squeeze,
            "state":state,"upper":round(c_upper,6),"lower":round(c_lower,6),"mid":round(c_mid,6),
            "band_width":round(cur_bw,6),"avg_band_width":round(avg_bw,6),
            "lower_streak":lower_streak,"upper_streak":upper_streak,"available":True}

def _empty_bb() -> dict:
    return {"long_score":50,"short_score":50,"pct_b":0.5,"squeeze":False,"state":"unknown",
            "upper":0,"lower":0,"mid":0,"band_width":0,"avg_band_width":0,
            "lower_streak":0,"upper_streak":0,"available":False}


# ══════════════════════════════════════════════
# A: EMA 교차 → 배율 계산
# ══════════════════════════════════════════════

def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _ema_direction(df: pd.DataFrame) -> str:
    if df is None or len(df) < config.EMA_SLOW+1:
        return "neutral"
    close    = df["close"].astype(float)
    ema_fast = float(_calc_ema(close, config.EMA_FAST).iloc[-1])
    ema_slow = float(_calc_ema(close, config.EMA_SLOW).iloc[-1])
    gap_pct  = abs(ema_fast-ema_slow)/ema_slow if ema_slow>0 else 0
    if gap_pct < 0.0005: return "neutral"
    return "bullish" if ema_fast>ema_slow else "bearish"


def calculate_ema_multiplier(ohlcv_dict: dict, direction: str) -> dict:
    df_15m = ohlcv_dict.get("15m")
    df_1h  = ohlcv_dict.get("1h")
    df_4h  = ohlcv_dict.get("4h")

    tf_signals = {
        "15m": _ema_direction(df_15m),
        "1h":  _ema_direction(df_1h),
        "4h":  _ema_direction(df_4h),
    }

    opposite      = "bearish" if direction == "long" else "bullish"
    reverse_count = sum(1 for sig in tf_signals.values() if sig == opposite)
    same_count    = sum(1 for sig in tf_signals.values() if sig == (
        "bullish" if direction=="long" else "bearish"))

    multiplier = config.EMA_MULTIPLIER.get(reverse_count, 1.0)

    if same_count == 3:    ema_dir = "bullish" if direction=="long" else "bearish"
    elif reverse_count==3: ema_dir = "bearish" if direction=="long" else "bullish"
    else:                  ema_dir = "mixed"

    reason = (
        f"EMA {same_count}/3 {direction}방향 일치 "
        f"(역방향:{reverse_count}개 → ×{multiplier:.2f} 배율)"
    )
    logger.info(f"[EMA배율/{direction.upper()}] {tf_signals} → ×{multiplier:.2f} ({reason})")

    return {
        "tf_signals":    tf_signals,
        "same_count":    same_count,
        "reverse_count": reverse_count,
        "multiplier":    multiplier,
        "direction":     ema_dir,
        "reason":        reason,
    }


# ══════════════════════════════════════════════
# 5. ADX
# ══════════════════════════════════════════════

def calculate_adx(df: pd.DataFrame, period: int = None) -> dict:
    period  = period or config.ADX_PERIOD
    _neutral = {"adx":0.0,"plus_di":0.0,"minus_di":0.0,"trend_dir":"neutral",
                "strength":"none","multiplier":1.0,"available":False}
    if df is None or len(df)<period*2+1: return _neutral
    high=df["high"].astype(float); low=df["low"].astype(float); close=df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high-low,(high-prev_close).abs(),(low-prev_close).abs()],axis=1).max(axis=1)
    up_move=high-high.shift(1); down_move=low.shift(1)-low
    plus_dm  = up_move.where((up_move>down_move)&(up_move>0),0.0)
    minus_dm = down_move.where((down_move>up_move)&(down_move>0),0.0)
    alpha    = 1.0/period
    atr_ema  = tr.ewm(alpha=alpha,adjust=False).mean()
    plus_ema = plus_dm.ewm(alpha=alpha,adjust=False).mean()
    minus_ema= minus_dm.ewm(alpha=alpha,adjust=False).mean()
    plus_di  = 100*plus_ema/atr_ema.replace(0,np.nan)
    minus_di = 100*minus_ema/atr_ema.replace(0,np.nan)
    dx       = 100*(plus_di-minus_di).abs()/(plus_di+minus_di).replace(0,np.nan)
    adx      = dx.ewm(alpha=alpha,adjust=False).mean()
    c_adx    = round(float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0, 2)
    c_pdi    = round(float(plus_di.iloc[-1]) if not pd.isna(plus_di.iloc[-1]) else 0.0, 2)
    c_mdi    = round(float(minus_di.iloc[-1]) if not pd.isna(minus_di.iloc[-1]) else 0.0, 2)
    if c_adx<config.ADX_NO_TREND:    strength,mult="none",0.70
    elif c_adx<config.ADX_WEAK_TREND:strength,mult="weak",0.85
    elif c_adx<config.ADX_STRONG:    strength,mult="normal",1.00
    else:                             strength,mult="strong",1.00
    trend_dir = "bullish" if c_pdi>c_mdi else ("bearish" if c_mdi>c_pdi else "neutral")
    return {"adx":c_adx,"plus_di":c_pdi,"minus_di":c_mdi,"trend_dir":trend_dir,
            "strength":strength,"multiplier":mult,"available":True}


# ══════════════════════════════════════════════
# 6. 펀딩비
# ══════════════════════════════════════════════

def analyze_funding_rate(funding_data: Optional[dict]) -> dict:
    if funding_data is None:
        return {"rate":0.0,"rate_pct":0.0,"long_score":50.0,"short_score":50.0,
                "bias":"neutral","strength":"neutral","available":False}
    rate = float(funding_data.get("rate",0.0))
    if rate<=config.FUNDING_LONG_STRONG:
        ls=90+min(10,abs(rate-config.FUNDING_LONG_STRONG)/abs(config.FUNDING_LONG_STRONG)*10); ss=10; bias,st="long_favorable","strong"
    elif rate<=config.FUNDING_LONG_MILD:
        ratio=(rate-config.FUNDING_LONG_MILD)/(config.FUNDING_LONG_STRONG-config.FUNDING_LONG_MILD)
        ls=65+ratio*25; ss=35-ratio*25; bias,st="long_favorable","mild"
    elif rate>=config.FUNDING_SHORT_STRONG:
        ss=90+min(10,(rate-config.FUNDING_SHORT_STRONG)/config.FUNDING_SHORT_STRONG*10); ls=10; bias,st="short_favorable","strong"
    elif rate>=config.FUNDING_SHORT_MILD:
        ratio=(rate-config.FUNDING_SHORT_MILD)/(config.FUNDING_SHORT_STRONG-config.FUNDING_SHORT_MILD)
        ss=65+ratio*25; ls=35-ratio*25; bias,st="short_favorable","mild"
    else:
        t=rate/config.FUNDING_LONG_MILD if rate<0 else rate/config.FUNDING_SHORT_MILD
        ls=50-t*15; ss=50+t*15; bias,st="neutral","neutral"
    ls=round(min(100,max(0,ls)),2); ss=round(min(100,max(0,ss)),2)
    logger.info(f"[FundingRate] {rate*100:+.4f}% [{bias}] 롱:{ls:.1f} 숏:{ss:.1f}")
    return {"rate":rate,"rate_pct":round(rate*100,6),"long_score":ls,"short_score":ss,
            "bias":bias,"strength":st,"available":True}


# ══════════════════════════════════════════════
# 7. 롱숏 비율 (국면별 해석)
# ══════════════════════════════════════════════

def analyze_long_short_ratio(ls_data: dict, regime_name: str = "RANGING") -> dict:
    """
    LS 비율 국면별 해석:
    TRENDING : trend-follow — 포지션 쏠림이 추세 방향을 확인
    RANGING  : contrarian  — 극단적 쏠림은 반전 신호
    """
    if not ls_data or not ls_data.get("available"):
        return {"long_score":50,"short_score":50,"bias":"neutral","long_pct":0.5,"short_pct":0.5,"available":False}
    long_pct  = ls_data.get("long_pct",  0.5)
    short_pct = ls_data.get("short_pct", 0.5)

    if regime_name == "TRENDING":
        if long_pct >= 0.60:           ls, ss, bias = 80, 20, "long_momentum"
        elif long_pct >= 0.52:         ls, ss, bias = 62, 38, "long_lean"
        elif short_pct >= 0.60:        ls, ss, bias = 20, 80, "short_momentum"
        elif short_pct >= 0.52:        ls, ss, bias = 38, 62, "short_lean"
        else:                          ls, ss, bias = 50, 50, "neutral"
    else:
        if long_pct >= config.LS_LONG_EXTREME:
            ls, ss, bias = 10, 90, "short_extreme"
        elif long_pct >= config.LS_LONG_HIGH:
            r = (long_pct-config.LS_LONG_HIGH)/(config.LS_LONG_EXTREME-config.LS_LONG_HIGH)
            ss = 70+r*20; ls = 100-ss; bias = "short_favorable"
        elif short_pct >= config.LS_SHORT_EXTREME:
            ls, ss, bias = 90, 10, "long_extreme"
        elif short_pct >= config.LS_SHORT_HIGH:
            r = (short_pct-config.LS_SHORT_HIGH)/(config.LS_SHORT_EXTREME-config.LS_SHORT_HIGH)
            ls = 70+r*20; ss = 100-ls; bias = "long_favorable"
        else:
            t = (long_pct-0.5)*2; ss = 50+t*10; ls = 100-ss; bias = "neutral"

    ls = round(min(100,max(0,ls)),2); ss = round(min(100,max(0,ss)),2)
    logger.info(f"[LS비율] 롱:{long_pct*100:.1f}% [{bias}/{regime_name}] 롱pt:{ls} 숏pt:{ss}")
    return {"long_score":ls,"short_score":ss,"bias":bias,
            "long_pct":long_pct,"short_pct":short_pct,"available":True}


# ══════════════════════════════════════════════
# F: Taker Buy/Sell Volume
# ══════════════════════════════════════════════

def analyze_taker_volume(taker_data: dict) -> dict:
    if not taker_data or not taker_data.get("available"):
        return {"long_score":50.0,"short_score":50.0,"bias":"neutral","strength":"neutral","available":False}

    buy_ratio  = taker_data.get("buy_ratio",  0.5)
    sell_ratio = taker_data.get("sell_ratio", 0.5)
    bias       = taker_data.get("bias",       "neutral")
    strength   = taker_data.get("strength",   "neutral")

    if buy_ratio >= config.TAKER_STRONG_BUY:
        ls = 85 + (buy_ratio-config.TAKER_STRONG_BUY)/(1-config.TAKER_STRONG_BUY)*10; ss = 15
    elif buy_ratio >= 0.55:
        ls = 65 + (buy_ratio-0.55)/(config.TAKER_STRONG_BUY-0.55)*20; ss = 100-ls
    elif sell_ratio >= config.TAKER_STRONG_SELL:
        ss = 85 + (sell_ratio-config.TAKER_STRONG_SELL)/(1-config.TAKER_STRONG_SELL)*10; ls = 15
    elif sell_ratio >= 0.55:
        ss = 65 + (sell_ratio-0.55)/(config.TAKER_STRONG_SELL-0.55)*20; ls = 100-ss
    else:
        ls = 50 + (buy_ratio-0.5)*80; ss = 100-ls

    ls = round(min(100,max(0,ls)),2); ss = round(min(100,max(0,ss)),2)
    logger.info(f"[Taker] 매수:{buy_ratio*100:.1f}% [{bias}/{strength}] 롱:{ls:.1f} 숏:{ss:.1f}")
    return {"long_score":ls,"short_score":ss,"bias":bias,"strength":strength,
            "buy_ratio":buy_ratio,"sell_ratio":sell_ratio,"available":True}


# ══════════════════════════════════════════════
# E: 강제청산 프록시
# ══════════════════════════════════════════════

def analyze_liquidations(liq_data: dict, df_15m=None) -> dict:
    _empty = {
        "long_score": 50, "short_score": 50,
        "signal": "none", "is_large": False,
        "long_liq_proxy": 0.0, "short_liq_proxy": 0.0,
        "available": False,
    }
    if df_15m is None or len(df_15m) < 15:
        return _empty

    close  = df_15m["close"].astype(float)
    high   = df_15m["high"].astype(float)
    low    = df_15m["low"].astype(float)
    open_  = df_15m["open"].astype(float)
    volume = df_15m["volume"].astype(float)

    avg_vol = float(volume.iloc[-21:-1].mean()) if len(df_15m) >= 21 else float(volume.iloc[:-1].mean())
    if avg_vol <= 0:
        return _empty

    long_liq_score = 0.0; short_liq_score = 0.0

    for i in range(-5, 0):
        c = float(close.iloc[i]); h = float(high.iloc[i])
        l = float(low.iloc[i]);   o = float(open_.iloc[i])
        v = float(volume.iloc[i])
        candle_range = h - l
        if candle_range < 1e-9: continue
        body_top    = max(o, c); body_bottom = min(o, c)
        lower_wick  = body_bottom - l; upper_wick  = h - body_top
        lw_pct = lower_wick/candle_range; uw_pct = upper_wick/candle_range
        vol_ratio = v/avg_vol
        if lw_pct > 0.35 and vol_ratio > 1.5:
            long_liq_score  = max(long_liq_score,  min(1.0, lw_pct*vol_ratio/2.0))
        if uw_pct > 0.35 and vol_ratio > 1.5:
            short_liq_score = max(short_liq_score, min(1.0, uw_pct*vol_ratio/2.0))

    price_chg = 0.0
    if len(df_15m) >= 7:
        p0 = float(close.iloc[-7]); p1 = float(close.iloc[-1])
        price_chg = abs(p1-p0)/p0 if p0>0 else 0.0
    is_large = price_chg > 0.02 and (long_liq_score > 0.25 or short_liq_score > 0.25)

    signal = "none"
    if long_liq_score  > short_liq_score and long_liq_score  > 0.15: signal = "long_liq_detected"
    elif short_liq_score > long_liq_score and short_liq_score > 0.15: signal = "short_liq_detected"

    if signal == "long_liq_detected":
        ls = round(60+long_liq_score*30,  2); ss = round(40-long_liq_score*10, 2)
        if is_large: ls = min(100, ls+10); logger.info(f"[청산프록시] 💥 롱청산 감지 {'대규모' if is_large else ''}")
    elif signal == "short_liq_detected":
        ss = round(60+short_liq_score*30, 2); ls = round(40-short_liq_score*10, 2)
        if is_large: ss = min(100, ss+10); logger.info(f"[청산프록시] 💥 숏청산 감지 {'대규모' if is_large else ''}")
    else:
        ls, ss = 50, 50

    return {"long_score":round(min(100,max(0,ls)),2),"short_score":round(min(100,max(0,ss)),2),
            "signal":signal,"is_large":is_large,
            "long_liq_proxy":round(long_liq_score,4),"short_liq_proxy":round(short_liq_score,4),
            "available":True}


# ══════════════════════════════════════════════
# 8. OI 변화율
# ══════════════════════════════════════════════

def analyze_oi_change(oi_data: dict, df_15m: Optional[pd.DataFrame]) -> dict:
    if not oi_data or not oi_data.get("available"):
        return {"long_score":50,"short_score":50,"interpretation":"no_data","available":False}
    change_pct    = oi_data.get("change_pct",0.0)
    oi_increasing = change_pct >  config.OI_CHANGE_MILD
    oi_decreasing = change_pct < -config.OI_CHANGE_MILD
    price_change  = 0.0
    if df_15m is not None and len(df_15m)>=5:
        cur=float(df_15m["close"].iloc[-1]); prev=float(df_15m["close"].iloc[-5])
        price_change = (cur-prev)/prev if prev>0 else 0.0
    price_up=price_change>0.001; price_down=price_change<-0.001
    if oi_increasing and price_up:
        strength=min(1.0,abs(change_pct)/config.OI_CHANGE_STRONG)
        ls=65+strength*20; ss=35-strength*10; interp="bullish_trend_confirm"
    elif oi_increasing and price_down:
        strength=min(1.0,abs(change_pct)/config.OI_CHANGE_STRONG)
        ss=65+strength*20; ls=35-strength*10; interp="bearish_trend_confirm"
    elif oi_decreasing and price_up:
        ls,ss=55,45; interp="short_covering"
    elif oi_decreasing and price_down:
        ls,ss=58,42; interp="long_liquidation"
    else:
        ls,ss=50,50; interp="neutral"
    return {"long_score":round(min(100,max(0,ls)),2),"short_score":round(min(100,max(0,ss)),2),
            "change_pct":round(change_pct*100,4),"interpretation":interp,
            "direction":"increasing" if oi_increasing else("decreasing" if oi_decreasing else "stable"),
            "available":True}


# ══════════════════════════════════════════════
# H: 시장 국면 분류
# ══════════════════════════════════════════════

def classify_market_regime(df_15m: pd.DataFrame, adx: dict, bb: dict) -> dict:
    if df_15m is None or len(df_15m) < 25 or not bb.get("available"):
        return {"regime":"UNKNOWN","threshold":60,"description":"데이터 부족","icon":"❓"}

    adx_val = adx.get("adx", 0.0)
    bw      = bb.get("band_width", 0.0)
    avg_bw  = bb.get("avg_band_width", bw)
    squeeze = bb.get("squeeze", False)
    bw_ratio= bw/avg_bw if avg_bw>0 else 1.0

    ma20_cross_count = 0; efficiency_ratio = 1.0
    try:
        close     = df_15m["close"].astype(float)
        ma20_full = close.rolling(20).mean()
        lookback  = min(40, len(close)-1)
        seg_close = close.iloc[-lookback-1:].values
        seg_ma20  = ma20_full.iloc[-lookback-1:].values
        for i in range(1, len(seg_close)):
            if pd.isna(seg_ma20[i]) or pd.isna(seg_ma20[i-1]): continue
            if (seg_close[i-1]>seg_ma20[i-1]) != (seg_close[i]>seg_ma20[i]):
                ma20_cross_count += 1
        seg     = close.iloc[-lookback:].values
        net_chg = abs(float(seg[-1])-float(seg[0]))
        total_chg = sum(abs(seg[i]-seg[i-1]) for i in range(1,len(seg)))
        efficiency_ratio = round(net_chg/total_chg,4) if total_chg>0 else 1.0
    except Exception:
        pass

    is_ranging_by_cross = (
        (ma20_cross_count >= 2 and efficiency_ratio < 0.35) or
        (efficiency_ratio < 0.15)
    )

    if squeeze and adx_val < config.REGIME_TREND_ADX:
        regime="SQUEEZE"; desc=f"BB 스퀴즈+ADX낮음({adx_val:.0f}) — 큰 움직임 대기"; icon="🔄"
    elif adx_val >= config.REGIME_STRONG_ADX and bw_ratio >= 1.2:
        regime="EXPLOSIVE"; desc=f"ADX강({adx_val:.0f})+BB확장({bw_ratio:.1f}x) — 변동성 폭발"; icon="💥"
    elif is_ranging_by_cross:
        regime="RANGING"; desc=f"MA20 교차 {ma20_cross_count}회 + ER:{efficiency_ratio:.2f} — 박스권 횡보 (ADX:{adx_val:.0f})"; icon="↔️"
    elif adx_val >= config.REGIME_TREND_ADX:
        regime="TRENDING"; desc=f"ADX추세({adx_val:.0f}) — 추세 진행 중"; icon="📈"
    else:
        regime="RANGING"; desc=f"ADX낮음({adx_val:.0f})+BB평행 — 박스권 횡보"; icon="↔️"

    threshold = config.REGIME_THRESHOLDS.get(regime, 60)
    logger.info(f"[국면] {icon} {regime} — {desc} (임계값:{threshold}pt, MA20교차:{ma20_cross_count}회)")
    return {"regime":regime,"threshold":threshold,"description":desc,"icon":icon,
            "adx":adx_val,"bw_ratio":round(bw_ratio,3),"squeeze":squeeze,
            "ma20_cross_count":ma20_cross_count,"efficiency_ratio":efficiency_ratio}


# ══════════════════════════════════════════════
# 9. 게이팅
# ══════════════════════════════════════════════

def evaluate_gates(direction: str, funding: dict, ls_ratio_result: dict) -> dict:
    funding_bias = funding.get("bias","neutral")
    ls_bias      = ls_ratio_result.get("bias","neutral")
    penalty_factor=1.0; penalty_reason=None
    if direction=="long":
        fa = funding_bias=="short_favorable"
        la = ls_bias in ("short_favorable","short_extreme")
    else:
        fa = funding_bias=="long_favorable"
        la = ls_bias in ("long_favorable","long_extreme")
    if fa and la:
        penalty_factor=0.80
        penalty_reason=f"펀딩비·롱숏비율 모두 {direction} 불리 — 점수 80% 적용"
        logger.info(f"[Gate] ⚠️ {direction.upper()} 복합 페널티 — {penalty_reason}")
    else:
        logger.info(f"[Gate] ✅ {direction.upper()} 통과")
    return {"passed":True,"funding_penalty":penalty_factor,"block_reason":None,"penalty_reason":penalty_reason}


# ══════════════════════════════════════════════
# 트레이더 업그레이드 — 신규 분석 함수 3개
# ══════════════════════════════════════════════

def analyze_candle_pattern(df: pd.DataFrame) -> dict:
    """캔들 패턴 감지 — 롱/숏 완전 대칭"""
    _empty = {
        "long_score": 50, "short_score": 50, "patterns": [],
        "bearish_pin": False, "bullish_pin": False,
        "bearish_engulf": False, "bullish_engulf": False,
        "consecutive_bear": False, "consecutive_bull": False,
    }
    if df is None or len(df) < 4:
        return _empty
    try:
        c = df["close"].astype(float).values
        o = df["open"].astype(float).values
        h = df["high"].astype(float).values
        l = df["low"].astype(float).values

        body  = np.abs(c - o)
        upper = h - np.maximum(c, o)
        lower = np.minimum(c, o) - l
        rng   = h - l

        min_rng = float(np.mean(rng[-20:])) * 0.3

        cur_rng = rng[-1]
        bearish_pin = (cur_rng > min_rng and
                       upper[-1] > body[-1] * 2.0 and
                       lower[-1] < upper[-1] * 0.3 and
                       c[-1] < o[-1])

        bullish_pin = (cur_rng > min_rng and
                       lower[-1] > body[-1] * 2.0 and
                       upper[-1] < lower[-1] * 0.3 and
                       c[-1] > o[-1])

        bearish_engulf = (c[-1] < o[-1] and c[-2] > o[-2] and
                          o[-1] >= c[-2]*0.999 and c[-1] <= o[-2]*1.001 and
                          body[-1] > body[-2])

        bullish_engulf = (c[-1] > o[-1] and c[-2] < o[-2] and
                          o[-1] <= c[-2]*1.001 and c[-1] >= o[-2]*0.999 and
                          body[-1] > body[-2])

        consecutive_bear = all(c[-i] < o[-i] for i in range(1, 4))
        consecutive_bull = all(c[-i] > o[-i] for i in range(1, 4))
        doji = body[-1] < cur_rng * 0.10 if cur_rng > 0 else False

        patterns = []
        short_score, long_score = 50, 50

        if bearish_pin:      short_score += 20; patterns.append("베어리시핀바")
        if bearish_engulf:   short_score += 18; patterns.append("베어리시인걸핑")
        if consecutive_bear and not bearish_pin: short_score += 8; patterns.append("연속음봉3")
        if bullish_pin:      long_score  += 20; patterns.append("불리시핀바")
        if bullish_engulf:   long_score  += 18; patterns.append("불리시인걸핑")
        if consecutive_bull and not bullish_pin: long_score += 8; patterns.append("연속양봉3")
        if doji:
            short_score = short_score * 0.85
            long_score  = long_score  * 0.85
            patterns.append("도지(방향약화)")

        if patterns:
            logger.info(f"[캔들패턴] {patterns} 롱:{long_score:.0f} 숏:{short_score:.0f}")

        return {
            "long_score":       round(min(100, max(0, long_score)),  2),
            "short_score":      round(min(100, max(0, short_score)), 2),
            "patterns":         patterns,
            "bearish_pin":      bearish_pin,
            "bullish_pin":      bullish_pin,
            "bearish_engulf":   bearish_engulf,
            "bullish_engulf":   bullish_engulf,
            "consecutive_bear": consecutive_bear,
            "consecutive_bull": consecutive_bull,
        }
    except Exception as e:
        logger.warning(f"[캔들패턴] 계산 오류: {e}")
        return _empty


def analyze_market_structure(df: pd.DataFrame) -> dict:
    """시장 구조 분석 — Lower High / Higher Low / 돌파 실패"""
    _empty = {
        "long_score": 50, "short_score": 50,
        "lower_high": False, "higher_low": False,
        "failed_breakout": False, "failed_breakdown": False,
    }
    if df is None or len(df) < 30:
        return _empty
    try:
        highs  = df["high"].astype(float).values
        lows   = df["low"].astype(float).values
        closes = df["close"].astype(float).values

        swing_highs, swing_lows = [], []
        for i in range(3, len(highs)-3):
            if highs[i] == max(highs[i-3:i+4]):
                swing_highs.append(highs[i])
            if lows[i] == min(lows[i-3:i+4]):
                swing_lows.append(lows[i])

        lower_high = False; higher_low = False
        failed_breakout = False; failed_breakdown = False

        if len(swing_highs) >= 2:
            lower_high = swing_highs[-1] < swing_highs[-2] * 0.998
        if len(swing_lows) >= 2:
            higher_low = swing_lows[-1] > swing_lows[-2] * 1.002

        lookback    = 20
        recent_high = max(highs[-lookback:-3])
        max_last5   = max(highs[-6:-1])
        current     = closes[-1]
        if max_last5 >= recent_high*0.99 and current < recent_high*0.98:
            failed_breakout = True

        recent_low  = min(lows[-lookback:-3])
        min_last5   = min(lows[-6:-1])
        if min_last5 <= recent_low*1.01 and current > recent_low*1.02:
            failed_breakdown = True

        short_score = 50 + (10 if lower_high else 0) + (16 if failed_breakout else 0)
        long_score  = 50 + (10 if higher_low else 0) + (16 if failed_breakdown else 0)

        sigs = []
        if lower_high:       sigs.append("LowerHigh")
        if higher_low:       sigs.append("HigherLow")
        if failed_breakout:  sigs.append("돌파실패")
        if failed_breakdown: sigs.append("붕괴실패")
        if sigs:
            logger.info(f"[시장구조] {sigs} 롱:{long_score:.0f} 숏:{short_score:.0f}")

        return {
            "long_score":        round(min(100, max(0, long_score)),  2),
            "short_score":       round(min(100, max(0, short_score)), 2),
            "lower_high":        lower_high,
            "higher_low":        higher_low,
            "failed_breakout":   failed_breakout,
            "failed_breakdown":  failed_breakdown,
        }
    except Exception as e:
        logger.warning(f"[시장구조] 계산 오류: {e}")
        return _empty


def analyze_vol_price_divergence(df: pd.DataFrame) -> dict:
    """거래량-가격 다이버전스 — 롱/숏 대칭"""
    _empty = {
        "long_score": 50, "short_score": 50,
        "bearish_vol_div": False, "bullish_vol_div": False,
    }
    if df is None or len(df) < 20:
        return _empty
    try:
        closes  = df["close"].astype(float).values[-20:]
        volumes = df["volume"].astype(float).values[-20:]
        half    = 10

        prev_c, curr_c = closes[:half], closes[half:]
        prev_v, curr_v = volumes[:half], volumes[half:]

        p_hi_idx = int(np.argmax(prev_c)); c_hi_idx = int(np.argmax(curr_c))
        p_lo_idx = int(np.argmin(prev_c)); c_lo_idx = int(np.argmin(curr_c))

        bearish_vol_div = (
            curr_c[c_hi_idx] > prev_c[p_hi_idx] * 1.003 and
            curr_v[c_hi_idx] < prev_v[p_hi_idx] * 0.75
        )
        bullish_vol_div = (
            curr_c[c_lo_idx] < prev_c[p_lo_idx] * 0.997 and
            curr_v[c_lo_idx] > prev_v[p_lo_idx] * 1.30
        )

        short_score = 50 + (18 if bearish_vol_div else 0)
        long_score  = 50 + (18 if bullish_vol_div else 0)

        if bearish_vol_div: logger.info("[거래량다이버] ★ 신고가+거래량감소 — 숏 신호")
        if bullish_vol_div: logger.info("[거래량다이버] ★ 신저가+거래량증가 — 롱 신호")

        return {
            "long_score":      round(min(100, max(0, long_score)),  2),
            "short_score":     round(min(100, max(0, short_score)), 2),
            "bearish_vol_div": bearish_vol_div,
            "bullish_vol_div": bullish_vol_div,
        }
    except Exception as e:
        logger.warning(f"[거래량다이버] 계산 오류: {e}")
        return _empty


# ══════════════════════════════════════════════
# 10. 전체 분석 통합
# ══════════════════════════════════════════════

def run_full_analysis(symbol: str, collected_data: dict) -> dict:
    import datetime
    logger.info(f"{'─'*50}")
    logger.info(f"🔬 분석: {symbol}")

    ohlcv        = collected_data.get("ohlcv", {})
    ticker       = collected_data.get("ticker") or {}
    funding_data = collected_data.get("funding_rate")
    ls_raw       = collected_data.get("ls_ratio", {})
    oi_raw       = collected_data.get("oi_change", {})
    taker_raw    = collected_data.get("taker_volume", {})
    liq_raw      = collected_data.get("liquidations", {})

    df_15m = ohlcv.get("15m")
    df_1h  = ohlcv.get("1h")
    df_4h  = ohlcv.get("4h")

    # ── 기술 지표 ──
    rsi      = analyze_mtf_rsi(df_15m, df_1h, df_4h)
    bb       = analyze_bollinger_bands(df_15m)
    adx_15m  = calculate_adx(df_15m)
    adx_1h   = calculate_adx(df_1h)
    funding  = analyze_funding_rate(funding_data)

    # H: 국면 분류 먼저 (LS 해석에 필요)
    regime   = classify_market_regime(df_15m, adx_15m, bb)

    # LS 비율 — 국면별 해석
    ls_ratio = analyze_long_short_ratio(ls_raw, regime.get("regime", "RANGING"))

    oi       = analyze_oi_change(oi_raw, df_15m)
    taker    = analyze_taker_volume(taker_raw)
    liq      = analyze_liquidations(liq_raw, df_15m)
    vol      = check_volume_confirmation(df_15m)
    atr      = get_atr_state(df_15m)

    # ── 트레이더 업그레이드 신규 분석 ──
    candle_pattern  = analyze_candle_pattern(df_15m)
    market_struct   = analyze_market_structure(df_15m)
    vol_price_div   = analyze_vol_price_divergence(df_15m)

    # A: EMA 배율 (롱·숏 각각)
    ema_long  = calculate_ema_multiplier(ohlcv, "long")
    ema_short = calculate_ema_multiplier(ohlcv, "short")

    # 게이팅
    gate_long  = evaluate_gates("long",  funding, ls_ratio)
    gate_short = evaluate_gates("short", funding, ls_ratio)

    logger.info(
        f"  MTF-RSI: 15m:{rsi['value']:.1f} 1h:{rsi.get('value_1h') or '-'} "
        f"4h:{rsi.get('value_4h') or '-'} [{rsi['state']}] | "
        f"BB:{bb['state']}(%B={bb['pct_b']:.2f}) | "
        f"EMA롱:{ema_long['multiplier']:.2f}x 숏:{ema_short['multiplier']:.2f}x | "
        f"ADX:{adx_15m['adx']:.1f}[{adx_15m['strength']}] | "
        f"국면:{regime['regime']} | "
        f"Taker:{taker.get('bias','?')} | "
        f"청산:{liq.get('signal','none')}"
    )
    if candle_pattern.get("patterns"):
        logger.info(f"  캔들패턴: {candle_pattern['patterns']}")

    return {
        "symbol":           symbol,
        "current_price":    ticker.get("last"),
        "rsi":              rsi,
        "bollinger":        bb,
        "ema_long":         ema_long,
        "ema_short":        ema_short,
        "adx_15m":          adx_15m,
        "adx_1h":           adx_1h,
        "funding_rate":     funding,
        "ls_ratio":         ls_ratio,
        "oi_change":        oi,
        "taker_volume":     taker,
        "liquidations":     liq,
        "volume":           vol,
        "atr":              atr,
        "regime":           regime,
        "gate_long":        gate_long,
        "gate_short":       gate_short,
        # 트레이더 업그레이드 신규 키
        "candle_pattern":   candle_pattern,
        "market_structure": market_struct,
        "vol_price_div":    vol_price_div,
        "analyzed_at":      datetime.datetime.utcnow().isoformat() + "Z",
    }
