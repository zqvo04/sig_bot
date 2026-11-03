"""V5 baseline controls (catalog 02 §3): C1 Donchian-20 trend, C2 Bollinger(20, 2σ) mean-reversion. Fixed params, no tuning.
Exit X0 (§2.1) via runner.x0_barrier_fn. tf: '1h' (H1 comparison, default) or '15m' (H2 comparison); H4 is 'not measurable'.
C1: close_t > max(high[t-20..t-1]) long / close_t < min(low[t-20..t-1]) short.
C2: close_t < SMA20 - 2*SD20 long / > SMA20 + 2*SD20 short (SMA/SD incl. bar t, SD = population, the Bollinger convention
-- catalog does not say; assumption). Decide at close of bar t, enter next open. Scale-free (prices vs own history only)."""
import os, statistics as st, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner as R  # noqa: E402

N, K = 20, 2.0


def c1_signal(candles):
    out = [None] * len(candles)
    for i in range(N, len(candles)):
        w, c = candles[i - N:i], candles[i][4]
        out[i] = "long" if c > max(x[2] for x in w) else "short" if c < min(x[3] for x in w) else None
    return out


def c2_signal(candles):
    out = [None] * len(candles)
    for i in range(N - 1, len(candles)):
        cl = [x[4] for x in candles[i - N + 1:i + 1]]
        m, sd = st.fmean(cl), st.pstdev(cl)
        out[i] = "long" if cl[-1] < m - K * sd else "short" if cl[-1] > m + K * sd else None
    return out


def _entries(sig_fn, data, symbol, params):
    tf = (params or {}).get("tf", "1h")
    C, tfm = data[tf], R.h.TF_MS[R.h._tf(tf)]
    bf = R.x0_barrier_fn(tf)
    out = []
    for i, d in enumerate(sig_fn(C)):
        b = d and bf(C, i, d)
        if b:
            out.append(dict(b, symbol=symbol, direction=d, signal_ts_ms=C[i][0] + tfm, meta=dict(tf=tf)))
    return out


def c1(data, symbol, params=None):
    return _entries(c1_signal, data, symbol, params)


def c2(data, symbol, params=None):
    return _entries(c2_signal, data, symbol, params)
