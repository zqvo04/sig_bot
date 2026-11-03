"""Offline triple-barrier scoring harness (V5 Phase 0, W3). stdlib only.

Scoring is NOT reimplemented: `score()` calls `src/ortho_resolver.evaluate_outcome` unmodified and
injects a fake `exchange` whose `fetch_ohlcv` serves stored candles (master plan §3 Phase 0).

Two candle sources, deliberately separate:
  * CachedExchange  — research/data/ohlcv (2023-01-01 .. 2025-12-31). The holdout cache
    research/data/holdout (ts >= 2026-01-01, master §5.4) is SEALED: it is never read unless the
    caller passes allow_holdout=True, and that path appends a line to research/data/holdout_access.log.
  * ReplayExchange  — ONLY for replaying the logged ORTHO V4 signals (2026-08..11). That calendar
    window lies inside the holdout period, but it is already disclosed/contaminated for ORTHO-derived
    work (master §5.4 contamination note). Replay therefore never touches the sealed holdout dir: it
    fetches just the per-signal 5m windows from OKX public REST (confirmed candles only) and caches
    them under research/data/ortho_replay/. Do not use it as a back door for non-ORTHO hypotheses.

evaluate_outcome swallows every `Exception` from fetch_ohlcv and returns resolved=False. Harness
errors (sealed holdout, missing cache, failed download) therefore derive from BaseException so they
propagate instead of silently turning into "unresolved".
"""
import bisect, csv, gzip, inspect, json, math, os, random, sys, time, types, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(ROOT, "..", "src"))
try:
    import ccxt  # noqa: F401
except ImportError:  # ortho_data imports ccxt only for the live exchange; offline scoring never uses it
    sys.modules["ccxt"] = types.SimpleNamespace(okx=object)
import ortho_config as oc  # noqa: E402
import ortho_resolver  # noqa: E402

HOLDOUT_START_MS = 1767225600000  # 2026-01-01T00:00:00Z
TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
TF_FILE = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}  # fetch_okx.py file names


class HarnessError(BaseException):
    """BaseException on purpose: must not be swallowed by evaluate_outcome's `except Exception`."""


def _tf(timeframe):
    tf = timeframe if timeframe in ("5m", "15m") else timeframe.lower()
    if tf not in TF_MS:
        raise HarnessError(f"unsupported timeframe {timeframe}")
    return tf


def _base(symbol):  # "BTC/USDT:USDT" | "BTC/USDT" | "BTC" -> "BTC"
    return symbol.split("/")[0].split("-")[0].upper()


def _window(ts, rows, since, limit):
    """ccxt semantics: since given -> candles with open ts >= since, ascending, at most limit;
    since None -> the latest `limit` candles."""
    if since is None:
        return rows[-limit:] if limit else list(rows)
    i = bisect.bisect_left(ts, since)
    return rows[i:i + limit] if limit else rows[i:]


def _read_gz(path):
    with gzip.open(path, "rt") as f:
        r = csv.reader(f); next(r)
        return [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in r]


class CachedExchange:
    def __init__(self, data_dir=DATA, allow_holdout=False):
        self.data_dir, self.allow_holdout, self._mem = data_dir, allow_holdout, {}

    def _load(self, sym, tf):
        key = (sym, tf)
        if key not in self._mem:
            name = f"{sym}_{TF_FILE[tf]}.csv.gz"
            p = os.path.join(self.data_dir, "ohlcv", name)
            if not os.path.exists(p):
                raise HarnessError(f"missing cache {p}")
            rows = _read_gz(p)
            if self.allow_holdout:
                hp = os.path.join(self.data_dir, "holdout", name)
                _log_holdout_access(self.data_dir, [hp])
                if os.path.exists(hp):
                    rows += _read_gz(hp)
            ts = [r[0] for r in rows]
            if any(b <= a for a, b in zip(ts, ts[1:])):
                raise HarnessError(f"{name}: timestamps not strictly ascending")
            self._mem[key] = (ts, rows)
        return self._mem[key]

    def fetch_ohlcv(self, symbol, timeframe="5m", since=None, limit=None, params=None):
        tf = _tf(timeframe)
        if not self.allow_holdout and since is not None and since >= HOLDOUT_START_MS:
            raise HarnessError(f"holdout sealed: since={since} >= 2026-01-01 (pass allow_holdout=True)")
        ts, rows = self._load(_base(symbol), tf)
        return [list(c) for c in _window(ts, rows, since, limit)]


def _log_holdout_access(data_dir, files):
    fr = inspect.stack()
    caller = next((f"{f.filename}:{f.lineno}" for f in fr[1:] if f.filename != __file__), "?")
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "caller": caller, "files": files})
    with open(os.path.join(data_dir, "holdout_access.log"), "a") as f:
        f.write(line + "\n")


class ReplayExchange:
    """Per-signal OKX REST windows for ORTHO log replay (see module docstring). Confirmed candles only."""
    BASE = "https://www.okx.com/api/v5/market/history-candles"

    def __init__(self, cache_dir=os.path.join(DATA, "ortho_replay")):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _get(self, **q):
        url = self.BASE + "?" + urllib.parse.urlencode(q)
        for attempt in range(6):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "v5-harness"}),
                                            timeout=30) as r:
                    j = json.load(r)
                if j.get("code") == "0":
                    return j["data"]
            except OSError:
                pass
            time.sleep(1 + 2 * attempt)
        raise HarnessError(f"OKX fetch failed: {url}")

    def fetch_ohlcv(self, symbol, timeframe="5m", since=None, limit=100, params=None):
        tf = _tf(timeframe)
        if since is None or not limit:
            raise HarnessError("ReplayExchange needs since and limit")
        sym, dur = _base(symbol), TF_MS[tf]
        p = os.path.join(self.cache_dir, f"{sym}_{tf}_{since}_{limit}.csv.gz")
        if os.path.exists(p):
            return _read_gz(p)
        rows, after = {}, since + limit * dur  # after/before are exclusive bounds on OKX
        while True:
            d = self._get(instId=f"{sym}-USDT-SWAP", bar=TF_FILE[tf],
                          after=after, before=since - 1, limit=100)
            for r in d:
                if r[8] == "1":
                    rows[int(r[0])] = [int(r[0])] + [float(x) for x in r[1:6]]
            if len(d) < 100:
                break
            after = min(int(r[0]) for r in d)
        out = [rows[t] for t in sorted(rows) if t >= since][:limit]
        if since + limit * dur <= time.time() * 1000 - dur:  # cache only fully-elapsed windows
            with gzip.open(p, "wt") as f:
                f.write("ts_ms,open,high,low,close,vol\n" + "".join(",".join(map(repr, c)) + "\n" for c in out))
        return out


# ── scoring ──────────────────────────────────────────────────────────
def score(entries, exchange):
    """entries: dicts with symbol, direction, signaled_at (ISO of the entry instant = open of the first
    bar after the closed signal bar), entry, tp, sl, r_dist, bars_limit (15m units).
    Returns evaluate_outcome's dict per entry + gross_r (unrounded, from exit_price; None if unresolved)."""
    out = []
    for e in entries:
        sig = dict(e, direction=str(e["direction"]).lower())
        res = ortho_resolver.evaluate_outcome(exchange, sig)
        g = None
        if res["exit_price"] is not None:
            d = res["exit_price"] - sig["entry"]
            g = (d if sig["direction"] == "long" else -d) / sig["r_dist"]
        out.append(dict(res, gross_r=g))
    return out


def wilder_atr(candles, n=14):
    """ATR used by X0: Wilder ATR(n) of the entry-TF bars. TR_i = max(H-L, |H-C_{i-1}|, |L-C_{i-1}|)
    (TR_0 = H-L); seed = SMA of the first n TRs at index n-1, then ATR_i = (ATR_{i-1}*(n-1)+TR_i)/n.
    ATR[i] uses bars <= i only (value is known at the CLOSE of bar i). None during warm-up."""
    out, atr, trs = [None] * len(candles), None, []
    for i, c in enumerate(candles):
        h, l = c[2], c[3]
        tr = h - l if i == 0 else max(h - l, abs(h - candles[i - 1][4]), abs(l - candles[i - 1][4]))
        if atr is None:
            trs.append(tr)
            if len(trs) == n:
                atr = sum(trs) / n
        else:
            atr = (atr * (n - 1) + tr) / n
        out[i] = atr
    return out


def x0_barriers(direction, entry, atr, sl_atr=1.5, rr=1.5):
    """Catalog §2.1 X0: SL = entry -/+ 1.5*ATR14(entry TF), TP = +1.5R (=2.25 ATR), no BE.
    `atr` must be ATR at the close of the signal bar; `entry` the next bar's open."""
    r = sl_atr * atr
    s = 1 if direction.lower() == "long" else -1
    return {"entry": entry, "sl": entry - s * r, "tp": entry + s * rr * r, "r_dist": r}


def bars_limit_15m(n_bars, tf):
    """Holding limit of n_bars in TF `tf` -> evaluate_outcome's bars_limit (15m units, int).
    evaluate_outcome does int(bars_limit)*3 five-minute candles, so only whole 15m multiples are exact."""
    minutes = n_bars * TF_MS[_tf(tf)] / 60_000
    if minutes % 15:
        raise ValueError(f"{n_bars}x{tf} = {minutes} min is not a whole number of 15m bars")
    return int(minutes // 15)


# ── lookahead guard ──────────────────────────────────────────────────
def assert_no_lookahead(signal_fn, candles, decision_idx=None, seed=0):
    """signal_fn(candles) -> sequence aligned with candles (out[i] = decision at close of bar i).
    For each decision index i, every candle j > i is randomly perturbed (OHLC stays consistent);
    out[:i+1] must be unchanged. Raises AssertionError at the first leaking index."""
    rng = random.Random(seed)
    base = list(signal_fn([list(c) for c in candles]))
    idxs = range(len(candles)) if decision_idx is None else decision_idx
    for i in idxs:
        pert = [list(c) for c in candles[:i + 1]]
        for c in candles[i + 1:]:
            k = math.exp(rng.uniform(-0.2, 0.2))
            o, cl = c[1] * k * math.exp(rng.uniform(-0.05, 0.05)), c[4] * k * math.exp(rng.uniform(-0.05, 0.05))
            hi = max(o, cl, c[2] * k); lo = min(o, cl, c[3] * k)
            pert.append([c[0], o, hi, lo, cl] + [v * rng.uniform(0.5, 2.0) for v in c[5:]])
        got = list(signal_fn(pert))
        if got[:i + 1] != base[:i + 1]:
            j = next(j for j in range(i + 1) if got[j] != base[j])
            raise AssertionError(f"lookahead: signal at bar {j} changed when bars > {i} were perturbed")
