"""OKX public-data downloader (V5 Phase 0, W2). stdlib only.
Daily bar = "1Dutc" (UTC-aligned; OKX "1D" is UTC+8 aligned).
ts < 2026-01-01Z -> data/ohlcv ; ts >= 2026-01-01Z -> data/holdout (SEALED: write-only, never read back;
stats come from .meta sidecars written at download time). Resumable via .meta/<SYM>_<BAR>.json.
"""
import gzip, hashlib, io, json, os, sys, time, threading, urllib.request, urllib.parse, calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

SYMS = ["BTC", "ETH", "SOL", "SUI", "XRP", "HYPE"]
BARS = {"5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1Dutc": 86_400_000}
def ms(s): return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000
START, END, SPLIT = ms("2023-01-01T00:00:00Z"), ms("2026-11-01T00:00:00Z"), ms("2026-01-01T00:00:00Z")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
D_OHLCV, D_HOLD, D_META = (os.path.join(ROOT, d) for d in ("ohlcv", "holdout", ".meta"))
BASE = "https://www.okx.com/api/v5/"
COLS = "ts_ms,open,high,low,close,vol,volCcy,volCcyQuote"
iso = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

_lock, _next = threading.Lock(), [0.0]
def get(path, **q):
    url = BASE + path + "?" + urllib.parse.urlencode(q)
    for attempt in range(10):
        with _lock:  # global throttle ~8 req/s
            now = time.time(); w = max(0, _next[0] - now); _next[0] = max(now, _next[0]) + 0.125
        time.sleep(w)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "v5-fetch"}), timeout=30) as r:
                j = json.load(r)
            if j.get("code") == "0": return j["data"]
            if j.get("code") in ("50011", "50013"): time.sleep(2 + 2 * attempt); continue
            raise RuntimeError(f"{j.get('code')} {j.get('msg')}")
        except Exception as e:
            if attempt == 9: raise
            time.sleep(min(30, 1.5 ** attempt))

def write_gz(path, header, lines):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g: g.write((header + "\n" + "".join(l + "\n" for l in lines)).encode())
    tmp = path + ".tmp"
    with open(tmp, "wb") as f: f.write(buf.getvalue())
    os.replace(tmp, path)
    return hashlib.sha256(buf.getvalue()).hexdigest()

def gaps(ts, iv):
    g = sorted(((b - a - iv) // iv, a + iv) for a, b in zip(ts, ts[1:]) if b - a != iv)
    return {"missing_bars": sum(n for n, _ in g), "n_gaps": len(g),
            "top5": [{"start": iso(s), "missing": n} for n, s in sorted(g, reverse=True)[:5]]}

def fetch_pair(sym, bar):
    meta_p = os.path.join(D_META, f"{sym}_{bar}.json")
    if os.path.exists(meta_p): return
    inst, rows, after = f"{sym}-USDT-SWAP", {}, END
    while True:
        d = get("market/history-candles", instId=inst, bar=bar, after=after, limit=100)
        if not d: break
        for r in d:
            if r[8] == "1" and START <= int(r[0]) < END: rows[int(r[0])] = r[:8]
        after = min(int(r[0]) for r in d)
        if after <= START: break
    ts = sorted(rows)
    meta = {"sym": sym, "bar": bar, "files": {}}
    for kind, d_, sel in (("ohlcv", D_OHLCV, [t for t in ts if t < SPLIT]), ("holdout", D_HOLD, [t for t in ts if t >= SPLIT])):
        if not sel: continue
        assert sel == sorted(set(sel))
        lines = [",".join(map(str, [t] + rows[t][1:])) for t in sel]
        if kind == "holdout": assert sel[0] >= SPLIT
        else:
            assert sel[-1] < SPLIT
            for t in sel:
                r = rows[t]; assert float(r[3]) <= min(float(r[1]), float(r[4])) and max(float(r[1]), float(r[4])) <= float(r[2]), ("OHLC", sym, bar, t)
        sha = write_gz(os.path.join(d_, f"{sym}_{bar}.csv.gz"), COLS, lines)
        m = {"rows": len(sel), "first_ts": iso(sel[0]), "last_ts": iso(sel[-1]), "sha256": sha}
        if kind == "ohlcv": m.update(gaps(sel, BARS[bar]))
        meta["files"][kind] = m
    with open(meta_p, "w") as f: json.dump(meta, f)
    print(f"done {sym} {bar} {({k: v['rows'] for k, v in meta['files'].items()})}", flush=True)

def funding(sym):
    p = os.path.join(ROOT, f"okx_funding_{sym}.json")
    if os.path.exists(p): return
    out, after = {}, None
    while True:
        q = dict(instId=f"{sym}-USDT-SWAP", limit=100); q.update({"after": after} if after else {})
        d = get("public/funding-rate-history", **q)
        if not d: break
        for r in d: out[int(r["fundingTime"])] = {k: r.get(k) for k in ("fundingTime", "fundingRate", "realizedRate")}
        after = min(int(r["fundingTime"]) for r in d)
    with open(p, "w") as f: json.dump([out[t] for t in sorted(out)], f)
    print(f"done funding {sym} n={len(out)}", flush=True)

def manifest():
    man = {"generated": iso(int(time.time() * 1000)), "daily_bar_code": "1Dutc", "files": {}}
    for fn in sorted(os.listdir(D_META)):
        meta = json.load(open(os.path.join(D_META, fn)))
        for kind, m in meta["files"].items():
            man["files"][f"{kind}/{meta['sym']}_{meta['bar']}.csv.gz"] = m
    json.dump(man, open(os.path.join(ROOT, "MANIFEST.json"), "w"), indent=1)
    return man

def selfcheck(man):
    for rel, m in man["files"].items():
        path = os.path.join(ROOT, rel)
        assert hashlib.sha256(open(path, "rb").read()).hexdigest() == m["sha256"], rel  # bytes only, no parsing
        if rel.startswith("holdout/"):
            assert m["first_ts"] >= iso(SPLIT), rel  # sealed: checked via meta, content not parsed
            continue
        assert m["last_ts"] < iso(SPLIT), rel
        prev, n = None, 0
        for line in gzip.open(path, "rt").read().splitlines()[1:]:
            t, o, h, l, c = line.split(",")[:5]; t = int(t)
            assert prev is None or t > prev, (rel, "order/dup"); prev = t; n += 1
            assert t < SPLIT and float(l) <= min(float(o), float(c)) and max(float(o), float(c)) <= float(h), (rel, t)
        assert n == m["rows"], rel
    print("selfcheck OK", len(man["files"]), "files")

if __name__ == "__main__":
    for d in (D_OHLCV, D_HOLD, D_META): os.makedirs(d, exist_ok=True)
    t0 = time.time()
    jobs = [(s, b) for b in BARS for s in SYMS]
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(lambda j: fetch_pair(*j), jobs)); list(ex.map(funding, SYMS))
    man = manifest(); selfcheck(man)
    print("rows", sum(m["rows"] for m in man["files"].values()), "elapsed_s", int(time.time() - t0))
