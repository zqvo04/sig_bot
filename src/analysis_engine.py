"""
analysis_engine.py — 분석 엔진 (v3.5)
────────────────────────────────────────────────────────────────────
[v3.5 변경]

★ check_volume_confirmation: 거래량 baseline 방식 변경
  기존: cur_vol=15m[-2], avg_vol=15m 48개 평균 (12h)
  수정: cur_vol=15m[-2], baseline=1h 120개 평균 / 4 (120h→15m 환산)
  효과:
    - 1h 합산으로 15m 단일 노이즈 제거 (~4배 안정)
    - 120h(5일) = 평일 Mon-Fri 사이클 완전 포함
    - 주말/세션 편향 대폭 감소
    - 허위 15m 스파이크 baseline 오염 제거
  폴백: df_1h 없거나 데이터 부족 시 기존 15m 로직 자동 전환

★ run_full_analysis: df_1h를 check_volume_confirmation에 전달
  기존: check_volume_confirmation(df_15m)
  수정: check_volume_confirmation(df_15m, df_1h=df_1h)

[v3.3] Volume 스코어 정규화, iloc[-2] 기준 캔들
[v3.2] evaluate_gates 단일 불리 패널티
[이전] analyze_oi_change 제거, Hidden Divergence 유지
────────────────────────────────────────────────────────────────────
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

def calculate_atr(df, period=None):
    if df is None or df.empty or "high" not in df.columns:
        return pd.Series(dtype=float)
    period = period or config.ATR_PERIOD
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/period, adjust=False).mean()


def get_atr_state(df):
    if df is None or len(df) < config.ATR_PERIOD + 5:
        return {"current":0.0,"pct":0.0,"expanding":False,"ratio":1.0}
    atr = calculate_atr(df)
    cur = float(atr.iloc[-1])
    avg = float(atr.iloc[-20:].mean()) if len(atr)>=20 else float(atr.mean())
    price = float(df["close"].iloc[-1])
    ratio = cur/avg if avg>0 else 1.0
    return {"current":round(cur,6),"pct":round(cur/price*100,4),"expanding":bool(ratio>1.3),"ratio":round(ratio,3)}


def check_volume_confirmation(df_15m, df_1h=None):
    """
    [v3.5] 거래량 baseline: 1h 캔들 120개 평균 / 4

    formula:
      baseline = mean(df_1h[-122:-2]) / 4  (120개 1h 완성 캔들 평균 → 15m 환산)
      cur_vol  = df_15m[-2]               (직전 완성 15m 캔들)
      ratio    = cur_vol / baseline

    왜 120h(5일)?
      - 평일(Mon-Fri) 사이클 완전 포함 → 요일별 편향 제거
      - 주말 저거래량이 baseline보다 낮게 측정 → 페널티 정확 발동
      - CANDLE_LIMITS["1h"]=210 → 122개 필요 → 여유 88개

    폴백: df_1h 없거나 부족 시 기존 15m 로직 (VOLUME_CONFIRM_LOOKBACK=48)
    """
    _empty = {"confirmed":False,"strong":False,"ratio":0.0,"score":50.0,
              "current_vol":0.0,"avg_vol":0.0,"baseline_method":"none"}

    n   = config.VOLUME_1H_BASELINE_CANDLES  # 120
    req = n + 4                               # 124

    if df_1h is not None and len(df_1h) >= req:
        avg_1h_vol = float(df_1h["volume"].iloc[-(n+2):-2].mean())
        baseline   = avg_1h_vol / 4
        if df_15m is None or len(df_15m) < 3:
            return _empty
        cur_vol = float(df_15m["volume"].iloc[-2])
        method  = f"1h_{n}h"
    else:
        lb = config.VOLUME_CONFIRM_LOOKBACK
        if df_15m is None or len(df_15m) < lb + 3:
            return _empty
        cur_vol    = float(df_15m["volume"].iloc[-2])
        baseline   = float(df_15m["volume"].iloc[-(lb+2):-2].mean())
        avg_1h_vol = None
        method     = "15m_fallback"
        logger.debug(f"[Volume] 1h 데이터 부족 → 15m 폴백 (df_1h: {len(df_1h) if df_1h is not None else None}개)")

    if baseline <= 0:
        return _empty

    ratio     = cur_vol / baseline
    confirmed = ratio >= config.VOLUME_SPIKE_MULTIPLIER
    strong    = ratio >= config.VOLUME_STRONG_MULTIPLIER

    if   ratio <= 0:   score = 0.0
    elif ratio <= 0.5: score = (ratio / 0.5) * 25.0
    elif ratio <= 1.0: score = 25.0 + ((ratio - 0.5) / 0.5) * 25.0
    elif ratio <= 1.5: score = 50.0 + ((ratio - 1.0) / 0.5) * 20.0
    elif ratio <= 2.5: score = 70.0 + ((ratio - 1.5) / 1.0) * 20.0
    else:              score = min(100.0, 90.0 + (ratio - 2.5) * 4.0)

    if avg_1h_vol is not None:
        logger.debug(f"[Volume/{method}] cur_15m:{cur_vol:.1f} 1h_avg:{avg_1h_vol:.1f} baseline:{baseline:.1f} ratio:{ratio:.3f}x → score:{score:.1f}pt")
    else:
        logger.debug(f"[Volume/{method}] cur:{cur_vol:.1f} avg:{baseline:.1f} ratio:{ratio:.3f}x → score:{score:.1f}pt")

    return {
        "confirmed":       confirmed,
        "strong":          strong,
        "ratio":           round(ratio, 3),
        "score":           round(min(100.0, max(0.0, score)), 2),
        "current_vol":     round(cur_vol, 2),
        "avg_vol":         round(baseline, 2),
        "baseline_method": method,
    }


# ══════════════════════════════════════════════
# 2. 멀티TF RSI + 다이버전스
# ══════════════════════════════════════════════

def calculate_rsi(df, period=None):
    period = period or config.RSI_PERIOD
    close = df["close"].astype(float)
    delta = close.diff()
    gain, loss = delta.clip(lower=0), (-delta).clip(lower=0)
    alpha = 1.0/period
    ag = gain.ewm(alpha=alpha, adjust=False).mean()
    al = loss.ewm(alpha=alpha, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return (100 - (100/(1+rs))).fillna(50)

def _rsi_to_score(v):
    if v <= 20:   ls = 95
    elif v <= 30: ls = 85 - (v-20)/10*10
    elif v <= 50: ls = 75 - (v-30)/20*25
    elif v <= 70: ls = 50 - (v-50)/20*30
    else:         ls = max(5, 20 - (v-70)*1.5)
    return round(min(100,max(0,ls)),2), round(min(100,max(0,100-ls)),2)

def _detect_bull_div(df, rsi, lb=6):
    if df is None or len(df)<lb*2: return False
    c=df["close"].values; r=rsi.values
    return bool(c[-lb:].min()<c[-lb*2:-lb].min() and r[-lb:].min()>r[-lb*2:-lb].min())

def _detect_bear_div(df, rsi, lb=6):
    if df is None or len(df)<lb*2: return False
    c=df["close"].values; r=rsi.values
    return bool(c[-lb:].max()>c[-lb*2:-lb].max() and r[-lb:].max()<r[-lb*2:-lb].max())

def _detect_hidden_bull_div(df, rsi, lb=8):
    if df is None or len(df)<lb*2: return False
    c=df["close"].values; r=rsi.values
    return bool(c[-lb:].min()>c[-lb*2:-lb].min() and r[-lb:].min()<r[-lb*2:-lb].min())

def _detect_hidden_bear_div(df, rsi, lb=8):
    if df is None or len(df)<lb*2: return False
    c=df["close"].values; r=rsi.values
    return bool(c[-lb:].max()<c[-lb*2:-lb].max() and r[-lb:].max()>r[-lb*2:-lb].max())


def analyze_mtf_rsi(df_15m, df_1h, df_4h):
    def _get(df):
        if df is None or len(df)<config.RSI_PERIOD+1: return None
        return float(calculate_rsi(df).iloc[-1])
    v_15m=_get(df_15m); v_1h=_get(df_1h); v_4h=_get(df_4h)
    weights=[(v_15m,0.50),(v_1h,0.30),(v_4h,0.20)]
    available=[(v,w) for v,w in weights if v is not None]
    if not available: return _empty_rsi()
    total_w=sum(w for _,w in available)
    v_weighted=sum(v*w for v,w in available)/total_w
    v_entry=v_15m if v_15m is not None else v_weighted
    state=("oversold" if v_entry<=config.RSI_OVERSOLD else "overbought" if v_entry>=config.RSI_OVERBOUGHT else "neutral")
    long_score_raw,short_score_raw=_rsi_to_score(v_weighted)
    pls=(v_1h is not None and v_1h>58 and v_15m is not None and v_15m<40)
    plw=(v_1h is not None and v_1h>52 and v_15m is not None and v_15m<44 and not pls)
    # [B] 눌림목 미세 롱 조건 강화: 1h>48,15m<42 (구:1h>45,15m<45)
    plm=(v_1h is not None and v_1h>48 and v_15m is not None and v_15m<42 and not pls and not plw)
    pl=pls or plw or plm
    pss=(v_1h is not None and v_1h<42 and v_15m is not None and v_15m>60)
    psw=(v_1h is not None and v_1h<48 and v_15m is not None and v_15m>56 and not pss)
    # [B] 눌림목 미세 숏 조건 강화: 1h<52,15m>58 (구:1h<55,15m>55)
    psm=(v_1h is not None and v_1h<52 and v_15m is not None and v_15m>58 and not pss and not psw)
    ps=pss or psw or psm
    macro_bull=v_4h is not None and v_4h>52
    macro_bear=v_4h is not None and v_4h<48
    pla=(14 if pls else 9 if plw else 5 if plm else 0)
    psa=(14 if pss else 9 if psw else 5 if psm else 0)
    if pl:
        long_score_raw=min(100,long_score_raw+pla); short_score_raw=max(0,short_score_raw-pla)
    if ps:
        short_score_raw=min(100,short_score_raw+psa); long_score_raw=max(0,long_score_raw-psa)
    if macro_bull and long_score_raw>50:  long_score_raw=min(100,long_score_raw+5)
    if macro_bear and short_score_raw>50: short_score_raw=min(100,short_score_raw+5)
    long_score=round(min(100,max(0,long_score_raw)),2)
    short_score=round(min(100,max(0,short_score_raw)),2)
    rsi_15m_s=calculate_rsi(df_15m) if df_15m is not None and len(df_15m)>=12 else None
    bull_div=bool(_detect_bull_div(df_15m,rsi_15m_s)) if rsi_15m_s is not None else False
    bear_div=bool(_detect_bear_div(df_15m,rsi_15m_s)) if rsi_15m_s is not None else False
    hbd=bool(_detect_hidden_bull_div(df_15m,rsi_15m_s)) if rsi_15m_s is not None and len(df_15m)>=16 else False
    hsd=bool(_detect_hidden_bear_div(df_15m,rsi_15m_s)) if rsi_15m_s is not None and len(df_15m)>=16 else False
    v15s=f"{v_15m:.1f}" if v_15m is not None else "N/A"
    v1hs=f"{v_1h:.1f}" if v_1h is not None else "N/A"
    v4hs=f"{v_4h:.1f}" if v_4h is not None else "N/A"
    pb_tag=((" ★눌림목롱(강)" if pls else " ★눌림목롱(약)" if plw else " ★눌림목롱(미)" if plm else "")+
            (" ★눌림목숏(강)" if pss else " ★눌림목숏(약)" if psw else " ★눌림목숏(미)" if psm else ""))
    div_tag=((" 📊히든롱다이버" if hbd else "")+(" 📊히든숏다이버" if hsd else ""))
    logger.info(f"[MTF-RSI] 15m:{v15s} 1h:{v1hs} 4h:{v4hs} 가중:{v_weighted:.1f} [{state}] 롱:{long_score:.1f}pt 숏:{short_score:.1f}pt"+pb_tag+div_tag)
    return {"value":round(v_entry,2),"value_1h":round(v_1h,2) if v_1h is not None else None,
            "value_4h":round(v_4h,2) if v_4h is not None else None,"value_weighted":round(v_weighted,2),
            "state":state,"long_score":long_score,"short_score":short_score,
            "bullish_divergence":bull_div,"bearish_divergence":bear_div,
            "hidden_bull_div":hbd,"hidden_bear_div":hsd,
            "pullback_long":pl,"pullback_short":ps,
            "pullback_long_strong":pls,"pullback_long_weak":plw,"pullback_long_micro":plm,
            "pullback_short_strong":pss,"pullback_short_weak":psw,"pullback_short_micro":psm}

def _empty_rsi():
    return {"value":50.0,"value_1h":None,"value_4h":None,"value_weighted":50.0,
            "state":"neutral","long_score":50.0,"short_score":50.0,
            "bullish_divergence":False,"bearish_divergence":False,
            "hidden_bull_div":False,"hidden_bear_div":False,
            "pullback_long":False,"pullback_short":False,
            "pullback_long_strong":False,"pullback_long_weak":False,"pullback_long_micro":False,
            "pullback_short_strong":False,"pullback_short_weak":False,"pullback_short_micro":False}

def get_rsi_signal(df):
    if df is None or len(df)<config.RSI_PERIOD+1: return _empty_rsi()
    rsi_s=calculate_rsi(df); v=float(rsi_s.iloc[-1])
    state="oversold" if v<=config.RSI_OVERSOLD else ("overbought" if v>=config.RSI_OVERBOUGHT else "neutral")
    ls,ss=_rsi_to_score(v)
    return {"value":round(v,2),"value_1h":None,"value_4h":None,"value_weighted":round(v,2),
            "state":state,"long_score":ls,"short_score":ss,
            "bullish_divergence":bool(_detect_bull_div(df,rsi_s)),
            "bearish_divergence":bool(_detect_bear_div(df,rsi_s)),
            "hidden_bull_div":False,"hidden_bear_div":False,
            "pullback_long":False,"pullback_short":False,
            "pullback_long_strong":False,"pullback_long_weak":False,"pullback_long_micro":False,
            "pullback_short_strong":False,"pullback_short_weak":False,"pullback_short_micro":False}


# ══════════════════════════════════════════════
# 3-11. 나머지 분석 함수
# ══════════════════════════════════════════════

def analyze_bollinger_bands(df):
    period=config.BOLLINGER_PERIOD; std_dev=config.BOLLINGER_STD
    if df is None or len(df)<period+1: return _empty_bb()
    close=df["close"].astype(float)
    mid=close.rolling(period).mean(); std=close.rolling(period).std()
    upper=mid+std_dev*std; lower=mid-std_dev*std
    bw_s=(upper-lower)/mid.replace(0,np.nan)
    cur_bw=float(bw_s.iloc[-1]) if not pd.isna(bw_s.iloc[-1]) else 0.0
    avg_bw=(float(bw_s.iloc[-50:].mean()) if len(bw_s)>=50 else (float(bw_s.iloc[-20:].mean()) if len(bw_s)>=20 else cur_bw))
    squeeze=bool(cur_bw<avg_bw*config.REGIME_SQUEEZE_RATIO and avg_bw>0)
    c_close=float(close.iloc[-1]); c_upper=float(upper.iloc[-1]); c_lower=float(lower.iloc[-1]); c_mid=float(mid.iloc[-1])
    band_range=c_upper-c_lower
    if band_range<=0: return _empty_bb()
    pct_b=(c_close-c_lower)/band_range
    if pct_b<=0.0:   ls,ss,state=92,8,"lower_breakout"
    elif pct_b<=0.15:ls,ss,state=82,18,"near_lower"
    elif pct_b<=0.35:ls,ss,state=65,35,"lower_zone"
    elif pct_b<=0.65:ls,ss,state=50,50,"middle"
    elif pct_b<=0.85:ls,ss,state=35,65,"upper_zone"
    elif pct_b<=1.0: ls,ss,state=18,82,"near_upper"
    else:            ls,ss,state=8,92,"upper_breakout"
    pctb_s=(close-lower)/(upper-lower).replace(0,np.nan).fillna(0.5)
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

def _empty_bb():
    return {"long_score":50,"short_score":50,"pct_b":0.5,"squeeze":False,"state":"unknown",
            "upper":0,"lower":0,"mid":0,"band_width":0,"avg_band_width":0,
            "lower_streak":0,"upper_streak":0,"available":False}


def _calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def _ema_direction(df):
    if df is None or len(df)<config.EMA_SLOW+1: return "neutral"
    close=df["close"].astype(float)
    ema_fast=float(_calc_ema(close,config.EMA_FAST).iloc[-1])
    ema_slow=float(_calc_ema(close,config.EMA_SLOW).iloc[-1])
    gap_pct=abs(ema_fast-ema_slow)/ema_slow if ema_slow>0 else 0
    if gap_pct<0.0005: return "neutral"
    return "bullish" if ema_fast>ema_slow else "bearish"

def calculate_ema_multiplier(ohlcv_dict, direction, regime="UNKNOWN"):
    tf_signals={"15m":_ema_direction(ohlcv_dict.get("15m")),"1h":_ema_direction(ohlcv_dict.get("1h")),"4h":_ema_direction(ohlcv_dict.get("4h"))}
    opposite="bearish" if direction=="long" else "bullish"
    reverse_count=sum(1 for sig in tf_signals.values() if sig==opposite)
    same_count=sum(1 for sig in tf_signals.values() if sig==("bullish" if direction=="long" else "bearish"))
    regime_mult_table=config.REGIME_EMA_MULTIPLIERS.get(regime,config.EMA_MULTIPLIER)
    multiplier=regime_mult_table.get(reverse_count,1.0)
    if same_count==3:    ema_dir="bullish" if direction=="long" else "bearish"
    elif reverse_count==3:ema_dir="bearish" if direction=="long" else "bullish"
    else:                 ema_dir="mixed"
    logger.info(f"[EMA배율/{direction.upper()}] {tf_signals} → ×{multiplier:.2f}  [{regime}]")
    return {"tf_signals":tf_signals,"same_count":same_count,"reverse_count":reverse_count,
            "multiplier":multiplier,"direction":ema_dir,"regime":regime,
            "reason":f"EMA {same_count}/3 {direction}방향 일치 (역방향:{reverse_count}개 → ×{multiplier:.2f}) [{regime}]"}


def calculate_adx(df, period=None):
    period=period or config.ADX_PERIOD
    _n={"adx":0.0,"plus_di":0.0,"minus_di":0.0,"trend_dir":"neutral","strength":"none","multiplier":1.0,"available":False}
    if df is None or len(df)<period*2+1: return _n
    high=df["high"].astype(float); low=df["low"].astype(float); close=df["close"].astype(float)
    prev_close=close.shift(1)
    tr=pd.concat([high-low,(high-prev_close).abs(),(low-prev_close).abs()],axis=1).max(axis=1)
    up_move=high-high.shift(1); down_move=low.shift(1)-low
    plus_dm=up_move.where((up_move>down_move)&(up_move>0),0.0)
    minus_dm=down_move.where((down_move>up_move)&(down_move>0),0.0)
    alpha=1.0/period
    atr_ema=tr.ewm(alpha=alpha,adjust=False).mean()
    plus_ema=plus_dm.ewm(alpha=alpha,adjust=False).mean()
    minus_ema=minus_dm.ewm(alpha=alpha,adjust=False).mean()
    plus_di=100*plus_ema/atr_ema.replace(0,np.nan)
    minus_di=100*minus_ema/atr_ema.replace(0,np.nan)
    dx=100*(plus_di-minus_di).abs()/(plus_di+minus_di).replace(0,np.nan)
    adx=dx.ewm(alpha=alpha,adjust=False).mean()
    c_adx=round(float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0,2)
    c_pdi=round(float(plus_di.iloc[-1]) if not pd.isna(plus_di.iloc[-1]) else 0.0,2)
    c_mdi=round(float(minus_di.iloc[-1]) if not pd.isna(minus_di.iloc[-1]) else 0.0,2)
    if c_adx<config.ADX_NO_TREND:    strength,mult="none",0.70
    elif c_adx<config.ADX_WEAK_TREND:strength,mult="weak",0.85
    elif c_adx<config.ADX_STRONG:    strength,mult="normal",1.00
    else:                             strength,mult="strong",1.00
    trend_dir="bullish" if c_pdi>c_mdi else ("bearish" if c_mdi>c_pdi else "neutral")
    return {"adx":c_adx,"plus_di":c_pdi,"minus_di":c_mdi,"trend_dir":trend_dir,
            "strength":strength,"multiplier":mult,"available":True}


def analyze_funding_rate(funding_data):
    if funding_data is None:
        return {"rate":0.0,"rate_pct":0.0,"long_score":50.0,"short_score":50.0,"bias":"neutral","strength":"neutral","available":False}
    rate=float(funding_data.get("rate",0.0))
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
    return {"rate":rate,"rate_pct":round(rate*100,6),"long_score":ls,"short_score":ss,"bias":bias,"strength":st,"available":True}


def analyze_long_short_ratio(ls_data, regime_name="RANGING"):
    if not ls_data or not ls_data.get("available"):
        return {"long_score":50,"short_score":50,"bias":"neutral","long_pct":0.5,"short_pct":0.5,"available":False}
    long_pct=ls_data.get("long_pct",0.5); short_pct=ls_data.get("short_pct",0.5)
    if regime_name=="TRENDING":
        if long_pct>=0.60:    ls,ss,bias=80,20,"long_momentum"
        elif long_pct>=0.52:  ls,ss,bias=62,38,"long_lean"
        elif short_pct>=0.60: ls,ss,bias=20,80,"short_momentum"
        elif short_pct>=0.52: ls,ss,bias=38,62,"short_lean"
        else:                 ls,ss,bias=50,50,"neutral"
    else:
        if long_pct>=config.LS_LONG_EXTREME:     ls,ss,bias=10,90,"short_extreme"
        elif long_pct>=config.LS_LONG_HIGH:
            r=(long_pct-config.LS_LONG_HIGH)/(config.LS_LONG_EXTREME-config.LS_LONG_HIGH)
            ss=70+r*20; ls=100-ss; bias="short_favorable"
        elif short_pct>=config.LS_SHORT_EXTREME: ls,ss,bias=90,10,"long_extreme"
        elif short_pct>=config.LS_SHORT_HIGH:
            r=(short_pct-config.LS_SHORT_HIGH)/(config.LS_SHORT_EXTREME-config.LS_SHORT_HIGH)
            ls=70+r*20; ss=100-ls; bias="long_favorable"
        else:
            t=(long_pct-0.5)*2; ss=50+t*10; ls=100-ss; bias="neutral"
    ls=round(min(100,max(0,ls)),2); ss=round(min(100,max(0,ss)),2)
    logger.info(f"[LS비율] 롱:{long_pct*100:.1f}% [{bias}/{regime_name}] 롱pt:{ls} 숏pt:{ss}")
    return {"long_score":ls,"short_score":ss,"bias":bias,"long_pct":long_pct,"short_pct":short_pct,"available":True}


def analyze_taker_volume(taker_data):
    if not taker_data or not taker_data.get("available"):
        return {"long_score":50.0,"short_score":50.0,"bias":"neutral","strength":"neutral","available":False}
    buy_ratio=taker_data.get("buy_ratio",0.5); sell_ratio=taker_data.get("sell_ratio",0.5)
    bias=taker_data.get("bias","neutral"); strength=taker_data.get("strength","neutral")
    if buy_ratio>=config.TAKER_STRONG_BUY:
        ls=85+(buy_ratio-config.TAKER_STRONG_BUY)/(1-config.TAKER_STRONG_BUY)*10; ss=15
    elif buy_ratio>=0.55:
        ls=65+(buy_ratio-0.55)/(config.TAKER_STRONG_BUY-0.55)*20; ss=100-ls
    elif sell_ratio>=config.TAKER_STRONG_SELL:
        ss=85+(sell_ratio-config.TAKER_STRONG_SELL)/(1-config.TAKER_STRONG_SELL)*10; ls=15
    elif sell_ratio>=0.55:
        ss=65+(sell_ratio-0.55)/(config.TAKER_STRONG_SELL-0.55)*20; ls=100-ss
    else:
        ls=50+(buy_ratio-0.5)*80; ss=100-ls
    ls=round(min(100,max(0,ls)),2); ss=round(min(100,max(0,ss)),2)
    logger.info(f"[Taker] 매수:{buy_ratio*100:.1f}% [{bias}/{strength}] 롱:{ls:.1f} 숏:{ss:.1f}")
    return {"long_score":ls,"short_score":ss,"bias":bias,"strength":strength,"buy_ratio":buy_ratio,"sell_ratio":sell_ratio,"available":True}


def analyze_liquidations(liq_data, df_15m=None):
    _empty={"long_score":50,"short_score":50,"signal":"none","is_large":False,
            "long_liq_proxy":0.0,"short_liq_proxy":0.0,"favorable_direction":None,"display_hint":None,"available":False}
    if df_15m is None or len(df_15m)<15: return _empty
    close=df_15m["close"].astype(float); high=df_15m["high"].astype(float)
    low=df_15m["low"].astype(float); open_=df_15m["open"].astype(float); volume=df_15m["volume"].astype(float)
    avg_vol=float(volume.iloc[-21:-1].mean()) if len(df_15m)>=21 else float(volume.iloc[:-1].mean())
    if avg_vol<=0: return _empty
    long_liq_score=0.0; short_liq_score=0.0
    for i in range(-5,0):
        c=float(close.iloc[i]); h=float(high.iloc[i]); l=float(low.iloc[i]); o=float(open_.iloc[i]); v=float(volume.iloc[i])
        cr=h-l
        if cr<1e-9: continue
        bt=max(o,c); bb=min(o,c); lw=bb-l; uw=h-bt
        lw_pct=lw/cr; uw_pct=uw/cr; vr=v/avg_vol
        if lw_pct>0.35 and vr>1.5: long_liq_score=max(long_liq_score,min(1.0,lw_pct*vr/2.0))
        if uw_pct>0.35 and vr>1.5: short_liq_score=max(short_liq_score,min(1.0,uw_pct*vr/2.0))
    price_chg=0.0
    if len(df_15m)>=7:
        p0=float(close.iloc[-7]); p1=float(close.iloc[-1])
        price_chg=abs(p1-p0)/p0 if p0>0 else 0.0
    is_large=price_chg>0.02 and (long_liq_score>0.25 or short_liq_score>0.25)
    signal="none"
    if long_liq_score>short_liq_score and long_liq_score>0.15:    signal="long_liq_detected"
    elif short_liq_score>long_liq_score and short_liq_score>0.15: signal="short_liq_detected"
    ls,ss=50,50
    if signal=="long_liq_detected":
        ls=round(60+long_liq_score*30,2); ss=round(40-long_liq_score*10,2)
        if is_large: ls=min(100,ls+10)
        logger.info(f"[청산프록시] 💥 롱청산 감지 {'대규모' if is_large else ''} → 반등 기대")
    elif signal=="short_liq_detected":
        ss=round(60+short_liq_score*30,2); ls=round(40-short_liq_score*10,2)
        if is_large: ss=min(100,ss+10)
        logger.info(f"[청산프록시] 💥 숏청산 감지 {'대규모' if is_large else ''} → 되돌림 기대")
    _liq_display={"long_liq_detected":("long","롱청산 감지 → 반등 기대"),"short_liq_detected":("short","숏청산 감지 → 되돌림 기대")}
    fav_dir,display_hint=_liq_display.get(signal,(None,None))
    return {"long_score":round(min(100,max(0,ls)),2),"short_score":round(min(100,max(0,ss)),2),
            "signal":signal,"is_large":is_large,"long_liq_proxy":round(long_liq_score,4),
            "short_liq_proxy":round(short_liq_score,4),"favorable_direction":fav_dir,"display_hint":display_hint,"available":True}


def classify_market_regime(df_15m, adx, bb):
    if df_15m is None or len(df_15m)<25 or not bb.get("available"):
        return {"regime":"UNKNOWN","threshold":63,"description":"데이터 부족","icon":"❓"}
    adx_val=adx.get("adx",0.0); bw=bb.get("band_width",0.0); avg_bw=bb.get("avg_band_width",bw)
    squeeze=bb.get("squeeze",False); bw_ratio=bw/avg_bw if avg_bw>0 else 1.0
    ma20_cross_count=0; efficiency_ratio=1.0
    try:
        close=df_15m["close"].astype(float); ma20_full=close.rolling(20).mean()
        lookback=min(40,len(close)-1)
        seg_close=close.iloc[-lookback-1:].values; seg_ma20=ma20_full.iloc[-lookback-1:].values
        for i in range(1,len(seg_close)):
            if pd.isna(seg_ma20[i]) or pd.isna(seg_ma20[i-1]): continue
            if (seg_close[i-1]>seg_ma20[i-1])!=(seg_close[i]>seg_ma20[i]): ma20_cross_count+=1
        seg=close.iloc[-lookback:].values
        net_chg=abs(float(seg[-1])-float(seg[0]))
        total_chg=sum(abs(seg[i]-seg[i-1]) for i in range(1,len(seg)))
        efficiency_ratio=round(net_chg/total_chg,4) if total_chg>0 else 1.0
    except: pass
    is_ranging_by_cross=((ma20_cross_count>=2 and efficiency_ratio<0.35) or (efficiency_ratio<0.15))
    if squeeze and adx_val<config.REGIME_TREND_ADX:
        regime="SQUEEZE"; desc=f"BB 스퀴즈+ADX낮음({adx_val:.0f}) — 큰 움직임 대기"; icon="🔄"
    elif adx_val>=config.REGIME_STRONG_ADX and bw_ratio>=1.2:
        regime="EXPLOSIVE"; desc=f"ADX강({adx_val:.0f})+BB확장({bw_ratio:.1f}x) — 변동성 폭발"; icon="💥"
    elif is_ranging_by_cross:
        regime="RANGING"; desc=f"MA20 교차 {ma20_cross_count}회 + ER:{efficiency_ratio:.2f} — 박스권 횡보 (ADX:{adx_val:.0f})"; icon="↔️"
    elif adx_val>=config.REGIME_TREND_ADX:
        regime="TRENDING"; desc=f"ADX추세({adx_val:.0f}) — 추세 진행 중"; icon="📈"
    else:
        regime="RANGING"; desc=f"ADX낮음({adx_val:.0f})+BB평행 — 박스권 횡보"; icon="↔️"
    threshold=config.REGIME_THRESHOLDS.get(regime,63)
    logger.info(f"[국면] {icon} {regime} — {desc} (임계값:{threshold}pt)")
    return {"regime":regime,"threshold":threshold,"description":desc,"icon":icon,
            "adx":adx_val,"bw_ratio":round(bw_ratio,3),"squeeze":squeeze,
            "ma20_cross_count":ma20_cross_count,"efficiency_ratio":efficiency_ratio}


def evaluate_gates(direction, funding, ls_ratio_result):
    funding_bias=funding.get("bias","neutral"); ls_bias=ls_ratio_result.get("bias","neutral")
    penalty_factor=1.0; penalty_reason=None
    if direction=="long":
        fr_bad=(funding_bias=="short_favorable"); ls_bad=(ls_bias in ("short_favorable","short_extreme"))
    else:
        fr_bad=(funding_bias=="long_favorable");  ls_bad=(ls_bias in ("long_favorable","long_extreme"))
    if fr_bad and ls_bad:
        penalty_factor=config.GATE_PENALTY_DUAL
        penalty_reason=f"펀딩비·롱숏비율 모두 {direction} 불리 — 복합 패널티 ×{penalty_factor}"
        logger.info(f"[Gate] ⚠️ {direction.upper()} 복합 패널티 (둘 다 불리)")
    elif fr_bad:
        penalty_factor=config.GATE_PENALTY_SINGLE
        penalty_reason=f"펀딩비 {direction} 불리 — 단일 패널티 ×{penalty_factor}"
        logger.info(f"[Gate] ⚠️ {direction.upper()} 펀딩비 불리 패널티")
    elif ls_bad:
        penalty_factor=config.GATE_PENALTY_SINGLE
        penalty_reason=f"롱숏비율 {direction} 불리 — 단일 패널티 ×{penalty_factor}"
        logger.info(f"[Gate] ⚠️ {direction.upper()} 롱숏비율 불리 패널티")
    else:
        logger.info(f"[Gate] ✅ {direction.upper()} 통과")
    return {"passed":True,"funding_penalty":penalty_factor,"block_reason":None,"penalty_reason":penalty_reason}


def detect_fvg(df, lookback=30):
    _empty={"in_bullish_fvg":False,"in_bearish_fvg":False,"bullish_fvg_count":0,"bearish_fvg_count":0,"nearest_bullish_fvg":None,"nearest_bearish_fvg":None}
    if df is None or len(df)<5: return _empty
    try:
        lb=min(lookback,len(df)); high=df["high"].astype(float).values[-lb:]
        low=df["low"].astype(float).values[-lb:]; close=df["close"].astype(float).values[-lb:]
        current=close[-1]; bullish_fvgs=[]; bearish_fvgs=[]
        for i in range(2,lb-2):
            if high[i-2]<low[i]:  bullish_fvgs.append((high[i-2],low[i]))
            if low[i-2]>high[i]:  bearish_fvgs.append((high[i],low[i-2]))
        active_bull=[(b,t) for b,t in bullish_fvgs if current>=b*0.99]
        active_bear=[(b,t) for b,t in bearish_fvgs if current<=t*1.01]
        in_bullish_fvg=any(b<=current<=t for b,t in active_bull)
        in_bearish_fvg=any(b<=current<=t for b,t in active_bear)
        nearest_bull=(min(active_bull,key=lambda x:abs((x[0]+x[1])/2-current)) if active_bull else None)
        nearest_bear=(min(active_bear,key=lambda x:abs((x[0]+x[1])/2-current)) if active_bear else None)
        if in_bullish_fvg: logger.info("[FVG] ★ 강세 FVG 내부 — 기관 매수 주문 구간 (롱 유리)")
        if in_bearish_fvg: logger.info("[FVG] ★ 약세 FVG 내부 — 기관 매도 주문 구간 (숏 유리)")
        return {"in_bullish_fvg":in_bullish_fvg,"in_bearish_fvg":in_bearish_fvg,
                "bullish_fvg_count":len(active_bull),"bearish_fvg_count":len(active_bear),
                "nearest_bullish_fvg":(round(nearest_bull[0],4),round(nearest_bull[1],4)) if nearest_bull else None,
                "nearest_bearish_fvg":(round(nearest_bear[0],4),round(nearest_bear[1],4)) if nearest_bear else None}
    except Exception as e:
        logger.warning(f"[FVG] 오류: {e}"); return _empty


def detect_bos_choch(df, lookback=60, n=3):
    _empty={"bos_bullish":False,"bos_bearish":False,"choch_bullish":False,"choch_bearish":False,"last_swing_high":None,"last_swing_low":None}
    if df is None or len(df)<max(20,n*4): return _empty
    try:
        lb=min(lookback,len(df)-1); highs=df["high"].astype(float).values[-lb:]
        lows=df["low"].astype(float).values[-lb:]; closes=df["close"].astype(float).values[-lb:]
        s_highs=[]; s_lows=[]
        for i in range(n,lb-n-1):
            wh=highs[max(0,i-n):i+n+1]; wl=lows[max(0,i-n):i+n+1]
            if len(wh)==2*n+1:
                if highs[i]==max(wh): s_highs.append((i,highs[i]))
                if lows[i] ==min(wl): s_lows.append((i,lows[i]))
        current_close=closes[-1]
        bos_bullish=bos_bearish=choch_bullish=choch_bearish=False
        last_sh=s_highs[-1][1] if s_highs else None; last_sl=s_lows[-1][1] if s_lows else None
        if last_sh and current_close>last_sh: bos_bullish=True
        if last_sl and current_close<last_sl: bos_bearish=True
        if not bos_bearish and len(s_highs)>=2 and len(s_lows)>=1:
            sh1,sh2=s_highs[-2],s_highs[-1]
            if sh2[1]>sh1[1]:
                il=[sl for sl in s_lows if sh1[0]<sl[0]<sh2[0]]
                if il and current_close<min(sl[1] for sl in il): choch_bearish=True
        if not bos_bullish and len(s_lows)>=2 and len(s_highs)>=1:
            sl1,sl2=s_lows[-2],s_lows[-1]
            if sl2[1]<sl1[1]:
                ih=[sh for sh in s_highs if sl1[0]<sh[0]<sl2[0]]
                if ih and current_close>max(sh[1] for sh in ih): choch_bullish=True
        if bos_bullish:   logger.info("[BOS] ★ 상승 BOS — 상승 추세 지속 확증")
        if bos_bearish:   logger.info("[BOS] ★ 하락 BOS — 하락 추세 지속 확증")
        if choch_bullish: logger.info("[CHoCH] ⚠️ 상승전환 경고 — 하락→상승 전환 신호")
        if choch_bearish: logger.info("[CHoCH] ⚠️ 하락전환 경고 — 상승→하락 전환 신호")
        return {"bos_bullish":bos_bullish,"bos_bearish":bos_bearish,
                "choch_bullish":choch_bullish,"choch_bearish":choch_bearish,
                "last_swing_high":round(last_sh,4) if last_sh else None,
                "last_swing_low": round(last_sl,4) if last_sl else None}
    except Exception as e:
        logger.warning(f"[BOS/CHoCH] 오류: {e}"); return _empty


def check_fibonacci_levels(df):
    _empty={"in_golden_pocket_long":False,"near_key_level_long":False,"long_retracement":None,
            "in_golden_pocket_short":False,"near_key_level_short":False,"short_retracement":None,
            "swing_high":None,"swing_low":None}
    if df is None or len(df)<config.FIB_LOOKBACK//2: return _empty
    try:
        lb=min(config.FIB_LOOKBACK,len(df)); closes=df["close"].astype(float).values[-lb:]
        highs=df["high"].astype(float).values[-lb:]; lows=df["low"].astype(float).values[-lb:]
        current=closes[-1]; end=lb-5
        sh_idx=int(np.argmax(highs[:end])); sl_idx=int(np.argmin(lows[:end]))
        swing_high=highs[sh_idx]; swing_low=lows[sl_idx]
        swing_low_for_long=min(lows[:sh_idx+1]) if sh_idx>0 else swing_low
        swing_high_for_short=max(highs[:sl_idx+1]) if sl_idx>0 else swing_high
        long_range=swing_high-swing_low_for_long; short_range=swing_high_for_short-swing_low
        long_retr=short_retr=None
        if long_range/swing_high>=config.FIB_MIN_SWING_PCT and current<swing_high:
            long_retr=(swing_high-current)/long_range
        if short_range/swing_high_for_short>=config.FIB_MIN_SWING_PCT and current>swing_low:
            short_retr=(current-swing_low)/short_range
        TOL=config.FIB_TOLERANCE
        def _gp(r): return r is not None and 0.618<=r<=0.650
        def _kl(r): return r is not None and any(abs(r-l)<=TOL for l in [0.382,0.500,0.786])
        in_gp_long=_gp(long_retr); in_gp_short=_gp(short_retr)
        near_l=_kl(long_retr) and not in_gp_long; near_s=_kl(short_retr) and not in_gp_short
        if in_gp_long:  logger.info(f"[피보] ★ 롱 황금포켓 {long_retr*100:.1f}%")
        elif near_l:    logger.info(f"[피보] 롱 주요레벨 {long_retr*100:.1f}%")
        if in_gp_short: logger.info(f"[피보] ★ 숏 황금포켓 {short_retr*100:.1f}%")
        elif near_s:    logger.info(f"[피보] 숏 주요레벨 {short_retr*100:.1f}%")
        return {"in_golden_pocket_long":in_gp_long,"near_key_level_long":near_l,
                "long_retracement":round(long_retr*100,1) if long_retr else None,
                "in_golden_pocket_short":in_gp_short,"near_key_level_short":near_s,
                "short_retracement":round(short_retr*100,1) if short_retr else None,
                "swing_high":round(swing_high,4),"swing_low":round(swing_low,4)}
    except Exception as e:
        logger.warning(f"[피보나치] 오류: {e}"); return _empty


def analyze_candle_pattern(df):
    _empty={"long_score":50,"short_score":50,"patterns":[],"bearish_pin":False,"bullish_pin":False,
            "bearish_engulf":False,"bullish_engulf":False,"consecutive_bear":False,"consecutive_bull":False}
    if df is None or len(df)<4: return _empty
    try:
        c=df["close"].astype(float).values; o=df["open"].astype(float).values
        h=df["high"].astype(float).values;  l=df["low"].astype(float).values
        body=np.abs(c-o); upper=h-np.maximum(c,o); lower=np.minimum(c,o)-l; rng=h-l
        min_rng=float(np.mean(rng[-20:]))*0.3; cur_rng=rng[-1]
        bearish_pin=(cur_rng>min_rng and upper[-1]>body[-1]*2.0 and lower[-1]<upper[-1]*0.3 and c[-1]<o[-1])
        bullish_pin=(cur_rng>min_rng and lower[-1]>body[-1]*2.0 and upper[-1]<lower[-1]*0.3 and c[-1]>o[-1])
        bearish_engulf=(c[-1]<o[-1] and c[-2]>o[-2] and o[-1]>=c[-2]*0.999 and c[-1]<=o[-2]*1.001 and body[-1]>body[-2])
        bullish_engulf=(c[-1]>o[-1] and c[-2]<o[-2] and o[-1]<=c[-2]*1.001 and c[-1]>=o[-2]*0.999 and body[-1]>body[-2])
        consecutive_bear=all(c[-i]<o[-i] for i in range(1,4))
        consecutive_bull=all(c[-i]>o[-i] for i in range(1,4))
        doji=body[-1]<cur_rng*0.10 if cur_rng>0 else False
        patterns=[]; short_score,long_score=50,50
        if bearish_pin:    short_score+=20; patterns.append("베어리시핀바")
        if bearish_engulf: short_score+=18; patterns.append("베어리시인걸핑")
        if consecutive_bear and not bearish_pin: short_score+=8; patterns.append("연속음봉3")
        if bullish_pin:    long_score+=20; patterns.append("불리시핀바")
        if bullish_engulf: long_score+=18; patterns.append("불리시인걸핑")
        if consecutive_bull and not bullish_pin: long_score+=8; patterns.append("연속양봉3")
        if doji: short_score*=0.85; long_score*=0.85; patterns.append("도지(방향약화)")
        if patterns: logger.info(f"[캔들패턴] {patterns}")
        return {"long_score":round(min(100,max(0,long_score)),2),"short_score":round(min(100,max(0,short_score)),2),
                "patterns":patterns,"bearish_pin":bearish_pin,"bullish_pin":bullish_pin,
                "bearish_engulf":bearish_engulf,"bullish_engulf":bullish_engulf,
                "consecutive_bear":consecutive_bear,"consecutive_bull":consecutive_bull}
    except Exception as e:
        logger.warning(f"[캔들패턴] 오류: {e}"); return _empty


def analyze_market_structure(df):
    _empty={"long_score":50,"short_score":50,"lower_high":False,"higher_low":False,"failed_breakout":False,"failed_breakdown":False}
    if df is None or len(df)<30: return _empty
    try:
        highs=df["high"].astype(float).values; lows=df["low"].astype(float).values; closes=df["close"].astype(float).values
        swing_highs=[]; swing_lows=[]
        for i in range(3,len(highs)-3):
            if highs[i]==max(highs[i-3:i+4]): swing_highs.append(highs[i])
            if lows[i] ==min(lows[i-3:i+4]):  swing_lows.append(lows[i])
        lower_high=higher_low=failed_breakout=failed_breakdown=False
        THRESH=config.MARKET_STRUCT_SWING_THRESHOLD
        if len(swing_highs)>=2: lower_high=swing_highs[-1]<swing_highs[-2]*(1-THRESH)
        if len(swing_lows) >=2: higher_low=swing_lows[-1] >swing_lows[-2] *(1+THRESH)
        lookback=20
        recent_high=max(highs[-lookback:-3]); max_last5=max(highs[-6:-1]); current=closes[-1]
        if max_last5>=recent_high*0.99 and current<recent_high*0.98: failed_breakout=True
        recent_low=min(lows[-lookback:-3]); min_last5=min(lows[-6:-1])
        if min_last5<=recent_low*1.01 and current>recent_low*1.02: failed_breakdown=True
        short_score=50+(10 if lower_high else 0)+(16 if failed_breakout else 0)
        long_score =50+(10 if higher_low else 0)+(16 if failed_breakdown else 0)
        sigs=[s for s,v in [("LowerHigh",lower_high),("HigherLow",higher_low),("돌파실패",failed_breakout),("붕괴실패",failed_breakdown)] if v]
        if sigs: logger.info(f"[시장구조] {sigs}")
        return {"long_score":round(min(100,max(0,long_score)),2),"short_score":round(min(100,max(0,short_score)),2),
                "lower_high":lower_high,"higher_low":higher_low,"failed_breakout":failed_breakout,"failed_breakdown":failed_breakdown}
    except Exception as e:
        logger.warning(f"[시장구조] 오류: {e}"); return _empty


def analyze_vol_price_divergence(df):
    _empty={"long_score":50,"short_score":50,"bearish_vol_div":False,"bullish_vol_div":False}
    if df is None or len(df)<20: return _empty
    try:
        closes=df["close"].astype(float).values[-20:]; volumes=df["volume"].astype(float).values[-20:]; half=10
        prev_c,curr_c=closes[:half],closes[half:]; prev_v,curr_v=volumes[:half],volumes[half:]
        p_hi=int(np.argmax(prev_c)); c_hi=int(np.argmax(curr_c))
        p_lo=int(np.argmin(prev_c)); c_lo=int(np.argmin(curr_c))
        P_THRESH=1+config.VOL_DIV_PRICE_THRESHOLD; V_BULL=config.VOL_DIV_BULL_VOLUME_RATIO; V_BEAR=config.VOL_DIV_BEAR_VOLUME_RATIO
        bearish_vol_div=(curr_c[c_hi]>prev_c[p_hi]*P_THRESH and curr_v[c_hi]<prev_v[p_hi]*V_BEAR)
        bullish_vol_div=(curr_c[c_lo]<prev_c[p_lo]*(2-P_THRESH) and curr_v[c_lo]>prev_v[p_lo]*V_BULL)
        short_score=50+(18 if bearish_vol_div else 0); long_score=50+(18 if bullish_vol_div else 0)
        if bearish_vol_div: logger.info("[거래량다이버] ★ 신고가+거래량감소 — 숏 신호")
        if bullish_vol_div: logger.info("[거래량다이버] ★ 신저가+거래량증가 — 롱 신호")
        return {"long_score":round(min(100,max(0,long_score)),2),"short_score":round(min(100,max(0,short_score)),2),
                "bearish_vol_div":bearish_vol_div,"bullish_vol_div":bullish_vol_div}
    except Exception as e:
        logger.warning(f"[거래량다이버] 오류: {e}"); return _empty


# ══════════════════════════════════════════════
# 전체 분석 통합
# ══════════════════════════════════════════════

def run_full_analysis(symbol, collected_data):
    import datetime
    logger.info(f"{chr(8213)*50}")
    logger.info(f"🔬 분석: {symbol}")

    ohlcv        = collected_data.get("ohlcv", {})
    ticker       = collected_data.get("ticker") or {}
    funding_data = collected_data.get("funding_rate")
    ls_raw       = collected_data.get("ls_ratio", {})
    taker_raw    = collected_data.get("taker_volume", {})
    liq_raw      = collected_data.get("liquidations", {})

    df_15m = ohlcv.get("15m")
    df_1h  = ohlcv.get("1h")
    df_4h  = ohlcv.get("4h")

    rsi     = analyze_mtf_rsi(df_15m, df_1h, df_4h)
    bb      = analyze_bollinger_bands(df_15m)
    adx_15m = calculate_adx(df_15m)
    adx_1h  = calculate_adx(df_1h)
    funding = analyze_funding_rate(funding_data)
    regime  = classify_market_regime(df_15m, adx_15m, bb)
    regime_name = regime.get("regime", "UNKNOWN")

    ls_ratio = analyze_long_short_ratio(ls_raw, regime_name)
    taker    = analyze_taker_volume(taker_raw)
    liq      = analyze_liquidations(liq_raw, df_15m)

    # [v3.5] df_1h 전달 → 1h 120개 평균/4 baseline 사용
    vol = check_volume_confirmation(df_15m, df_1h=df_1h)

    atr = get_atr_state(df_15m)

    candle_pattern = analyze_candle_pattern(df_15m)
    market_struct  = analyze_market_structure(df_15m)
    vol_price_div  = analyze_vol_price_divergence(df_15m)

    fvg       = detect_fvg(df_15m)
    bos_choch = detect_bos_choch(df_15m)
    fibonacci = check_fibonacci_levels(df_15m)

    ema_long  = calculate_ema_multiplier(ohlcv, "long",  regime_name)
    ema_short = calculate_ema_multiplier(ohlcv, "short", regime_name)

    gate_long  = evaluate_gates("long",  funding, ls_ratio)
    gate_short = evaluate_gates("short", funding, ls_ratio)

    logger.info(
        f"  MTF-RSI: 15m:{rsi['value']:.1f} 1h:{rsi.get('value_1h') or '-'} "
        f"4h:{rsi.get('value_4h') or '-'} [{rsi['state']}] | "
        f"BB:{bb['state']}(%B={bb['pct_b']:.2f}) | "
        f"ADX:{adx_15m['adx']:.1f}[{adx_15m['strength']}] | "
        f"국면:{regime_name} | "
        f"Vol:{vol['ratio']:.2f}x({vol['score']:.0f}pt) [{vol.get('baseline_method','?')}] | "
        f"Taker:{taker.get('bias','?')} | 청산:{liq.get('signal','none')}"
    )
    if bos_choch.get("bos_bullish") or bos_choch.get("bos_bearish"):
        logger.info(f"  BOS: 상승={bos_choch['bos_bullish']} 하락={bos_choch['bos_bearish']}")
    if fvg.get("in_bullish_fvg") or fvg.get("in_bearish_fvg"):
        logger.info(f"  FVG: 강세={fvg['in_bullish_fvg']} 약세={fvg['in_bearish_fvg']}")

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
        "oi_change":        {"available": False},
        "taker_volume":     taker,
        "liquidations":     liq,
        "volume":           vol,
        "atr":              atr,
        "regime":           regime,
        "gate_long":        gate_long,
        "gate_short":       gate_short,
        "candle_pattern":   candle_pattern,
        "market_structure": market_struct,
        "vol_price_div":    vol_price_div,
        "fvg":              fvg,
        "bos_choch":        bos_choch,
        "fibonacci":        fibonacci,
        "analyzed_at":      datetime.datetime.utcnow().isoformat() + "Z",
    }
