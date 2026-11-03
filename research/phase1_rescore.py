#!/usr/bin/env python3
"""Phase 1 비용 현실화 재채점 — docs/v5/01_PREREG_PHASE1.md (v1.0) 그대로 구현. 표준 라이브러리만.
임계·시나리오·CI 방식은 사전등록 고정값이며 여기서 조정하지 않는다(조정=v1.1 사후변경).
사용: python3 research/phase1_rescore.py   (입력 research/data/notion_all.csv, okx_funding_<SYM>.json)"""
import sys, os, json, math, random, datetime, glob, statistics as st
from collections import defaultdict, Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ortho_report as orp  # noqa: E402  (load, bootstrap_ci, wilson, integrity_reasons, _grade 재사용)

# ── 사전등록 고정 상수 ───────────────────────────────────────────────
MAKER, TAKER, SLIP_R = 0.0002, 0.0005, 0.05
TICK = {"BTC": 0.1, "ETH": 0.01, "HYPE": 0.001, "SOL": 0.01, "SUI": 0.0001, "XRP": 0.0001}
MAXRATE = {"BTC": .00375, "ETH": .0075, "SOL": .0075, "XRP": .0075, "HYPE": .01, "SUI": .01}
SYMS = list(TICK)
ITERS, SEED, MIN_N = 2000, 12345, 30
EXPECT = dict(rows=1907, v3=946, v4=961, v3_status=(502, 444), v4_status=(591, 370))  # (LOSS, WIN)
KST = datetime.timedelta(hours=9)
V4_ID = "ORTHO-4.SIM0"


# ── 비용 ────────────────────────────────────────────────────────────
def cost_R(entry, rdist, x, scen, tick, maker=MAKER, taker=TAKER, slip=SLIP_R):
    """펀딩 제외 비용(R). scen A/B/C. 스케일프리: tick/entry, 1/u 만 사용."""
    h = 0.5 * tick / entry
    if scen == "A":
        frac = 2 * maker if x == "TP" else maker + taker + h
        extra = 0.0
    else:
        frac = 2 * taker + 2 * h
        extra = slip if (scen == "C" and x in ("SL", "BE")) else 0.0
    return frac * entry / rdist + extra


def settlements(S, E):
    """S < t_f <= E 인 00/08/16 UTC 정산시각(naive UTC datetime)."""
    t = S.replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    while t <= E:
        for h in (0, 8, 16):
            tf = t + datetime.timedelta(hours=h)
            if S < tf <= E:
                out.append(tf)
        t += datetime.timedelta(days=1)
    return out


def _ms(dt):
    return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def _bnd(S, E):
    """경계 일치(t_f==S 또는 ==E) 개수 — 포함 규칙(S 제외·E 포함) 민감도 가시화용."""
    return sum(1 for t in (S, E) if t.hour % 8 == 0 and t.minute == 0 and t.second == 0)


def fund_R(d, u, S, E, rates):
    """fund_R = d*Σrate/u. rates={epoch_ms: rate}. 반환 (fund_R, n_cross, n_missing, n_boundary)."""
    tfs = settlements(S, E)
    miss = sum(1 for t in tfs if _ms(t) not in rates)
    tot = sum(rates.get(_ms(t), 0.0) for t in tfs)
    return d * tot / u, len(tfs), miss, _bnd(S, E)


def load_rates(path):
    out = {}
    for r in json.load(open(path)):
        rr = r.get("realizedRate")
        out[int(r["fundingTime"])] = float(rr if rr not in (None, "") else r["fundingRate"])
    return out


# ── 통계 ────────────────────────────────────────────────────────────
def block_ci(vals, days, iters=ITERS, alpha=0.05, seed=SEED):
    """KST일 블록 부트스트랩(일 복원추출 → 거래 이어붙여 평균). 퍼센타일 CI."""
    g = defaultdict(list)
    for v, d in zip(vals, days):
        g[d].append(v)
    sums = [sum(x) for x in g.values()]
    cnts = [len(x) for x in g.values()]
    D = len(sums)
    if D == 0:
        return (None, None)
    rnd = random.Random(seed)
    means = []
    for _ in range(iters):
        idx = [rnd.randrange(D) for _ in range(D)]
        means.append(sum(sums[i] for i in idx) / sum(cnts[i] for i in idx))
    means.sort()
    return (means[int(alpha / 2 * iters)], means[min(int((1 - alpha / 2) * iters), iters - 1)])


def verdict(n, lo, hi):
    if n < MIN_N or lo is None:
        return "표본부족"
    if hi < 0:
        return "구조적 음수 확정 (강한 NO-GO)"
    if lo > 0:
        return "필요조건만 충족 — GO 아님"
    return "보류(측정 불가)"


def breakeven_wr(nets):
    w = [x for x in nets if x > 0]
    l = [-x for x in nets if x <= 0]
    if not w or not l:
        return None
    aw, al = sum(w) / len(w), sum(l) / len(l)
    return al / (aw + al)


def stats(T, scen="A", ci=True):
    n = len(T)
    s = dict(n=n, small=n < MIN_N)
    if n == 0:
        return s
    net = [t["net"][scen] for t in T]
    s["gross"] = st.mean(t["gross"] for t in T)
    s["net"] = st.mean(net)
    s["cost"] = st.mean(t["cost"][scen] + t["fund"] for t in T)
    s["cost_nofund"] = st.mean(t["cost"][scen] for t in T)
    if ci and n >= MIN_N:
        s["ci_pct"] = orp.bootstrap_ci(net)
        s["ci_blk"] = block_ci(net, [t["day"] for t in T])
        s["ci"] = (min(s["ci_pct"][0], s["ci_blk"][0]), max(s["ci_pct"][1], s["ci_blk"][1]))
    w = sum(1 for t in T if t["status"] == "WIN")
    s["win"] = (w / n, orp.wilson(w, n))
    nw = sum(1 for x in net if x > 0)
    s["nwin"] = (nw / n, orp.wilson(nw, n))
    s["bewr"] = breakeven_wr(net)
    return s


# ── 행 → 거래 ───────────────────────────────────────────────────────
def build_trades(rows, rates):
    """반환 (trades, funding_missing_v4). 시각: KST 입력 → UTC(−9h)."""
    trades, miss_v4 = [], 0
    for i, r in enumerate(rows):
        base = (r.get("Symbol") or "").split("/")[0]
        if base not in TICK:
            raise SystemExit(f"중단: 알 수 없는 심볼 {r.get('Symbol')!r} (tickSz 표 없음)")
        d = {"LONG": 1, "SHORT": -1}.get((r.get("Direction") or "").upper())
        if d is None or r["R"] is None:
            raise SystemExit(f"중단: 방향/R 결측 행 {i} {r.get('Signal')}")
        sid = (r.get("Strategy ID") or "").strip()
        coh = "V4" if sid == V4_ID else ("V3" if sid == "" else "?")
        S = r["t"] - KST
        Rt = orp._parse_dt(r.get("Resolved At"))
        E = Rt - KST if Rt else S + datetime.timedelta(minutes=15 * (r["b2e"] or 0))
        u = r["rdist"] / r["entry"]
        x, inferred = r["grade"], False
        if x is None:
            inferred = True
            if r["Status"] == "WIN" and r["rr"] is not None and abs(r["R"] - r["rr"]) <= 0.05:
                x = "TP"
            elif r["Status"] == "LOSS" and abs(r["R"] + 1) <= 0.05:
                x = "SL"
            else:
                x = "TIME"
        # V3 펀딩 = 확인 불가 → 0 (사전등록). V4 는 캐시로 계산.
        if coh == "V4":
            f, ncross, miss, bnd = fund_R(d, u, S, E, rates[base])
            miss_v4 += miss
        else:
            f = 0.0
            ncross, bnd = len(settlements(S, E)), _bnd(S, E)
        cost = {sc: cost_R(r["entry"], r["rdist"], x, sc, TICK[base]) for sc in "ABC"}
        t = dict(i=i, coh=coh, sym=base, d=d, dir=r["Direction"].upper(), status=r["Status"], u=u, S=S, E=E,
                 snap=orp._parse_dt(r.get("Snapshot At")) or r["t"], x=x, inferred=inferred, gross=r["R"],
                 cost=cost, fund=f, ncross=ncross, bnd=bnd, net={sc: r["R"] - cost[sc] - f for sc in "ABC"},
                 day=(r["t"]).date().isoformat(), month=r["t"].strftime("%Y-%m"), regime=r["regime"],
                 reasons=orp.integrity_reasons(r), rec_grade=r["grade"], row=r)
        trades.append(t)
    return trades, miss_v4


# ── 중복·민감도 ─────────────────────────────────────────────────────
def overlaps(T):
    """(a) 같은 심볼 보유창 겹침(방향 무관, 접촉은 비겹침). 반환 (쌍 수, 겹친 거래 수)."""
    pairs, hit = 0, set()
    by = defaultdict(list)
    for t in T:
        by[t["sym"]].append(t)
    for L in by.values():
        for a in range(len(L)):
            for b in range(a + 1, len(L)):
                if L[b]["S"] < L[a]["E"] and L[a]["S"] < L[b]["E"]:
                    pairs += 1
                    hit.update((L[a]["i"], L[b]["i"]))
    return pairs, len(hit)


def dedup_overlap(T):
    kept, last = [], {}
    for t in sorted(T, key=lambda t: (t["S"], t["i"])):
        if t["sym"] not in last or t["S"] >= last[t["sym"]]:
            kept.append(t)
            last[t["sym"]] = t["E"]
    return kept


def clusters(T):
    g = defaultdict(list)
    for t in T:
        g[t["snap"]].append(t)
    return g


def dedup_cluster(T):
    return [sorted(L, key=lambda t: (t["sym"], t["i"]))[0] for L in clusters(T).values()]


# ── 보고서 ──────────────────────────────────────────────────────────
def f3(x, small=False):
    return "—" if x is None else f"{x:+.3f}" + ("!" if small else "")


def pct(x):
    return "—" if x is None else f"{x*100:.1f}%"


def row_md(label, s):
    if s["n"] == 0:
        return f"| {label} | 0 |" + " — |" * 9
    sm = s["small"]
    ci = lambda c: "—" if c is None else f"[{c[0]:+.3f}, {c[1]:+.3f}]"
    w, nw = s["win"], s["nwin"]
    return (f"| {label} | {s['n']}{'!' if sm else ''} | {f3(s['gross'], sm)} | {f3(s['net'], sm)} | {ci(s.get('ci_pct'))} | "
            f"{ci(s.get('ci_blk'))} | **{ci(s.get('ci'))}** | {w[0]*100:.1f}% [{w[1][0]:.1f}-{w[1][1]:.1f}] | "
            f"{nw[0]*100:.1f}% [{nw[1][0]:.1f}-{nw[1][1]:.1f}] | {pct(s['bewr'])} | {s['cost']:.3f} / {s['cost_nofund']:.3f} |")


HDR = ("| 분할 | n | Gross | Net_A | CI(퍼센타일) | CI(일블록) | **판정 CI** | 승률(Wilson%) | 순승률(Wilson%) | 손익분기 승률 | "
       "cost_R 펀딩포함/제외 |\n|---|---|---|---|---|---|---|---|---|---|---|")


def splits(T):
    """코호트 하나에 대한 (제목, [(라벨, 거래들)]) 목록."""
    def grp(key, order=None):
        d = defaultdict(list)
        for t in T:
            d[key(t)].append(t)
        return [(str(k), d[k]) for k in sorted(d)]
    out = [("전체", [("전체", T)]), ("방향", grp(lambda t: t["dir"])), ("월별(KST)", grp(lambda t: t["month"])),
           ("심볼별", grp(lambda t: t["sym"])),
           ("청산유형별", grp(lambda t: t["x"] + ("(추론)" if t["inferred"] else "")))]
    return out


def run(csv_path, fund_dir, out_md, out_json, expect=EXPECT):
    rows = orp.load(csv_path)
    res = {"inputs": {"csv": csv_path}}
    dev = []
    # Step 0 검증 (원본·격리 전)
    v3r = [r for r in rows if (r.get("Strategy ID") or "") == ""]
    v4r = [r for r in rows if (r.get("Strategy ID") or "") == V4_ID]
    cnt = lambda L: (sum(1 for r in L if r["Status"] == "LOSS"), sum(1 for r in L if r["Status"] == "WIN"))
    chk = {"V-1": (len(rows), expect["rows"]), "V-2": ((len(v3r), len(v4r)), (expect["v3"], expect["v4"])),
           "V-3": ((cnt(v3r), cnt(v4r)), (expect["v3_status"], expect["v4_status"]))}
    res["step0"] = {k: dict(got=str(a), want=str(b), ok=(a == b)) for k, (a, b) in chk.items()}
    u_mean = lambda L: st.mean(r["rdist"] / r["entry"] for r in L) * 100 if L else float("nan")
    m4 = st.mean(r["R"] for r in v4r) if v4r else float("nan")
    gaps = [abs(r["R"] - float(r["Gross R"])) for r in v4r if orp._f(r.get("Gross R")) is not None]
    res["step0"]["V-4"] = dict(got=f"{m4:.4f}", want="-0.003", ok=round(m4, 3) == -0.003)
    res["step0"]["V-5"] = dict(got=f"V4 {u_mean(v4r):.3f} / V3 {u_mean(v3r):.3f}", want="V4 0.506 / V3 0.437",
                               ok=(round(u_mean(v4r), 3) == 0.506 and round(u_mean(v3r), 3) == 0.437))
    res["step0"]["V-6"] = dict(got=f"Gross R 보유 {len(gaps)}건, |Δ|>0.01 {sum(g>0.01 for g in gaps)}건, 최대차 {max(gaps, default=0):.4f}",
                               want="0건", ok=all(g <= 0.01 for g in gaps) and bool(gaps))
    hard_fail = [k for k in ("V-1", "V-2", "V-3") if not res["step0"][k]["ok"]]
    if hard_fail:
        res["halt"] = f"Step 0 {hard_fail} 미통과 — 분석 중단"
        json.dump(res, open(out_json, "w"), ensure_ascii=False, indent=1, default=str)
        print(res["halt"], json.dumps(res["step0"], ensure_ascii=False, indent=1))
        return res
    rates = {}
    for s in SYMS:
        rates[s] = load_rates(os.path.join(fund_dir, f"okx_funding_{s}.json"))
    trades, miss_v4 = build_trades(rows, rates)
    if miss_v4:
        res["halt"] = f"중단: V4 펀딩 캐시 결측 정산 {miss_v4}건(0이어야 함)"
        json.dump(res, open(out_json, "w"), ensure_ascii=False, indent=1, default=str)
        print(res["halt"])
        return res
    if any(t["coh"] == "?" for t in trades):
        raise SystemExit("중단: 알 수 없는 Strategy ID")
    # V-4 격리 후 값
    q = [t for t in trades if t["reasons"]]
    ok = [t for t in trades if not t["reasons"]]
    m4q = st.mean(t["gross"] for t in ok if t["coh"] == "V4")
    res["step0"]["V-4"]["격리후"] = f"{m4q:.4f} (반올림 {round(m4q,3)})"
    # ISO 교차검증(보조)
    iso_bad = None
    if rows and "Signaled At (ISO)" in rows[0]:
        iso_bad = 0
        for t in trades:
            s = (t["row"].get("Signaled At (ISO)") or "")[:16]
            try:
                iso = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
                iso_bad += abs((iso - t["S"]).total_seconds()) > 60
            except ValueError:
                iso_bad += 1
    else:
        dev.append("`Signaled At (ISO)` 열 없음 — KST→UTC 변환만 사용(교차검증 생략)")
    COH = {"V4": [t for t in ok if t["coh"] == "V4"], "V3": [t for t in ok if t["coh"] == "V3"], "합본": ok}
    allT = {"V4": [t for t in trades if t["coh"] == "V4"], "V3": [t for t in trades if t["coh"] == "V3"], "합본": trades}

    # 메인 표
    main_tabs, J = {}, {}
    for c, T in COH.items():
        J[c] = {}
        main_tabs[c] = []
        for title, grps in splits(T):
            ss = [(lab, stats(g)) for lab, g in grps]
            lines = [row_md(lab, s) for lab, s in ss]
            J[c][title] = dict(ss)
            main_tabs[c].append((title, lines))
        if c == "V4":
            h = len(T) // 2
            Ts = sorted(T, key=lambda t: (t["S"], t["i"]))
            half = [("전반 50%", Ts[:h]), ("후반 50%", Ts[h:])]
            hs = {l: stats(g) for l, g in half}
            main_tabs[c].append(("시간 50/50 분할 (서술용, 판정 불참)", [row_md(l, s) for l, s in hs.items()]))
            J[c]["half"] = hs
    v4s = J["V4"]["전체"]["전체"]
    lo, hi = v4s["ci"]
    ver = verdict(v4s["n"], lo, hi)

    # S1 시나리오
    s1 = {}
    for c, T in COH.items():
        for lab, g in (("전체", T), ("LONG", [t for t in T if t["dir"] == "LONG"]), ("SHORT", [t for t in T if t["dir"] == "SHORT"])):
            s1[(c, lab)] = {sc: stats(g, sc) for sc in "ABC"}
    # S2 중복
    s2 = {}
    for c, T in COH.items():
        p, h = overlaps(T)
        cl = [L for L in clusters(T).values() if len({t["sym"] for t in L}) >= 2]
        homog = sum(1 for L in cl if len({t["status"] for t in L}) == 1)
        sizes = Counter(len(L) for L in cl)
        sens = {"(a) 겹침 후속 제거": dedup_overlap(T), "(b) 군집당 1건": dedup_cluster(T)}
        s2[c] = dict(pairs=p, hit=h, ncl=len(cl), homog=homog, sizes=dict(sorted(sizes.items())),
                     sens={k: stats(g) for k, g in sens.items()})
    # S3 격리·청산유형·V3 펀딩
    s3 = {}
    for c in COH:
        A = allT[c]
        n = len(A)
        s3[c] = dict(n=n, quar=[(t["sym"], t["reasons"]) for t in A if t["reasons"]],
                     rec=sum(1 for t in A if not t["inferred"]) / n, inf=sum(1 for t in A if t["inferred"]) / n,
                     bnd=sum(t["bnd"] for t in A))
    v3 = COH["V3"]
    v3x = {}
    for sym in SYMS + ["전체"]:
        g = [t for t in v3 if sym == "전체" or t["sym"] == sym]
        if g:
            v3x[sym] = dict(n=len(g), cross=sum(1 for t in g if t["ncross"] > 0) / len(g))
    bound = st.mean(t["ncross"] * MAXRATE[t["sym"]] / t["u"] for t in v3) if v3 else 0.0
    v3net = st.mean(t["net"]["A"] for t in v3) if v3 else None
    obs_max = {s: max(abs(v) for v in rates[s].values()) for s in SYMS}

    # ── 마크다운 ──
    L = []
    P = L.append
    P("# Phase 1 비용 현실화 재채점 보고서\n")
    P(f"> 사전등록: `docs/v5/01_PREREG_PHASE1.md` v1.0 · 생성 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} · 가설 장부 k=1 (α=0.05, 95% CI)\n")
    P("## 결론\n")
    P(f"**판정: {ver}** — V4 (n={v4s['n']}) Net_A = {v4s['net']:+.3f} R, 판정 CI [{lo:+.3f}, {hi:+.3f}] "
      f"(퍼센타일 [{v4s['ci_pct'][0]:+.3f}, {v4s['ci_pct'][1]:+.3f}] / 일블록 [{v4s['ci_blk'][0]:+.3f}, {v4s['ci_blk'][1]:+.3f}]).\n")
    inrange = -0.16 <= v4s["net"] <= -0.10
    P(f"- 사전 예측(Net_A −0.10~−0.16R, 판정 '구조적 음수 확정') 대비: 점추정 {'범위 안' if inrange else '**범위 밖**'}, "
      f"판정 {'일치' if hi < 0 else '**상이**'}.")
    P(f"- Gross(V4) = {v4s['gross']:+.3f} R (사전 관측 ≈ −0.003 — 예측 대상 아님). 평균 비용 A = {v4s['cost']:.3f} R (펀딩 제외 {v4s['cost_nofund']:.3f}).")
    sc = s1[("V4", "전체")]
    P(f"- 보조 민감도(판정 불참) V4 전체: Net_A {sc['A']['net']:+.3f} · Net_B {sc['B']['net']:+.3f} · Net_C {sc['C']['net']:+.3f}.")
    sens_flag = []
    for k, v in s2["V4"]["sens"].items():
        z = verdict(v["n"], *(v.get("ci") or (None, None)))
        if z != ver:
            sens_flag.append(f"{k}: {z}")
    P(f"- 중복 민감도가 판정 구역을 바꾸는가: {'**예** — ' + '; '.join(sens_flag) if sens_flag else '아니오(두 민감도 모두 동일 구역)'}.")
    if s3["V4"]["inf"] > 0.05:
        P(f"- **경고**: V4 청산유형 추론 비율 {s3['V4']['inf']*100:.1f}% > 5% — maker/taker 배분이 추론에 의존.")
    P("- A는 비용 하한(낙관). **숨은 낙관 가정**: ① 메이커 진입 100% 체결 ② 역선택 0 ③ SL 슬리피지 0 ④ 스프레드 = 반틱. "
      "수수료율(0.02%/0.05%, VIP0)은 2차 출처, 미검증.\n")
    P("## Step 0 검증 (원본·격리 전)\n")
    P("| # | got | want | 통과 |\n|---|---|---|---|")
    for k, v in res["step0"].items():
        P(f"| {k} | {v['got']}{' · 격리후 ' + v['격리후'] if '격리후' in v else ''} | {v['want']} | {'OK' if v['ok'] else '**불일치**'} |")
    P("")
    P(f"- V-4 격리 전 {m4:+.4f} / 격리 후 {m4q:+.4f} → 기대치(−0.003)와 일치한 쪽: "
      f"{'격리 전' if round(m4,3)==-0.003 else ''}{'·' if round(m4,3)==-0.003 and round(m4q,3)==-0.003 else ''}{'격리 후' if round(m4q,3)==-0.003 else ''}"
      f"{'없음' if round(m4,3)!=-0.003 and round(m4q,3)!=-0.003 else ''}.")
    if iso_bad is not None:
        P(f"- KST→UTC 변환 vs `Signaled At (ISO)` 불일치(>1분): {iso_bad}건.")
    P("")
    P("## ① 표본·기간·레짐 커버리지\n")
    P("| 코호트 | 해소 행 | 격리 | 분석 n | 기간(KST Signaled) | 심볼 수 | 레짐 토큰 분포 |\n|---|---|---|---|---|---|---|")
    for c in COH:
        T = COH[c]
        ts = sorted(t["row"]["t"] for t in T)
        rg = Counter(t["regime"] for t in T)
        P(f"| {c} | {len(allT[c])} | {len(allT[c])-len(T)} | {len(T)} | {ts[0]:%Y-%m-%d} ~ {ts[-1]:%Y-%m-%d} | {len({t['sym'] for t in T})} | "
          f"{', '.join(f'{k}:{v}' for k, v in rg.most_common())} |")
    P("\n월별·심볼별 분포는 §② 표의 n 열 참조. 단일 구간(약 5개월, 레짐 회전 미포함)이다.\n")
    P("## ② Gross / Net_A + CI (표준·블록)\n")
    P("판정 CI = 퍼센타일·일블록 중 넓은 쪽. n<30 칸은 `!`(판정·결론 금지, CI 생략). 순서: 주(V4) → 보조(V3, 판정 불참) → 참고(합본, 판정 근거 금지).\n")
    for c in ("V4", "V3", "합본"):
        P(f"### 코호트 {c}" + ("  (주 — 판정)" if c == "V4" else "  (판정 불참)") + "\n")
        for title, lines in main_tabs[c]:
            P(f"**{title}**\n\n{HDR}")
            L.extend(lines)
            P("")
    P("### 롱/숏 대칭 패널 (Net_A, `ortho_report._asym_warn` 기준: 부호 반대 또는 격차>0.3R 경고)\n")
    P("| 코호트 | LONG | SHORT | 판정 |\n|---|---|---|---|")
    for c in COH:
        a, b = (J[c]["방향"].get(k) for k in ("LONG", "SHORT"))
        av = a["net"] if a and not a["small"] else None
        bv = b["net"] if b and not b["small"] else None
        P(f"| {c} | {f3(av)} | {f3(bv)} | {orp._asym_warn(av, bv)} |")
    P("\n## ③ 승 / 본전 / 패 분해 + 손익분기 승률\n")
    P("기록 Status는 WIN/LOSS뿐(BE 청산 승은 소액 이익 WIN). 비용 후 순승률(Net>0)과 손익분기 승률은 위 표의 열 참조. "
      "BE 등급(기록) 건수: " + ", ".join(f"{c} {sum(1 for t in COH[c] if t['x']=='BE')}" for c in COH) + ".\n")
    P("## 보조 표 S1 — 시나리오 A/B/C (판정 불참)\n")
    P("| 코호트 | 분할 | n | Net_A [판정 CI] | Net_B [판정 CI] | Net_C [판정 CI] |\n|---|---|---|---|---|---|")
    for (c, lab), d in s1.items():
        cell = lambda s: f"{f3(s['net'], s['small'])} " + ("—" if "ci" not in s else f"[{s['ci'][0]:+.3f}, {s['ci'][1]:+.3f}]")
        P(f"| {c} | {lab} | {d['A']['n']} | {cell(d['A'])} | {cell(d['B'])} | {cell(d['C'])} |")
    P("\n## 보조 표 S2 — 중복·독립성\n")
    P("(a) 겹침=같은 심볼 `[S,E]` 교차(접촉 제외, 방향 무관). (b) 군집=동일 `Snapshot At`(없으면 `Signaled At`)에 ≥2 심볼. "
      "라이브는 코인별 OPEN 중복 차단이 있어 (a)=0 기대 — 0이 아니면 그 자체가 보고 사항.\n")
    P("| 코호트 | (a) 겹침 쌍 | (a) 겹친 거래 | (b) 군집 수 | 군집 크기 분포 | 전승/전패(동질) | 민감도 | n | Gross | Net_A | 판정 CI | 구역 |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c, d in s2.items():
        for k, v in d["sens"].items():
            ci = v.get("ci")
            P(f"| {c} | {d['pairs']} | {d['hit']} | {d['ncl']} | {d['sizes']} | {d['homog']} | {k} | {v['n']} | {f3(v['gross'])} | {f3(v['net'])} | "
              f"{'—' if not ci else f'[{ci[0]:+.3f}, {ci[1]:+.3f}]'} | {verdict(v['n'], *(ci or (None, None)))} |")
    P("\n## 보조 표 S3 — 격리·청산유형·V3 펀딩\n")
    P("| 코호트 | 해소 행 | 격리 | 청산유형 기록률 | 추론률 | 펀딩 경계일치 건수 |\n|---|---|---|---|---|---|")
    for c, d in s3.items():
        P(f"| {c} | {d['n']} | {len(d['quar'])} | {d['rec']*100:.1f}% | {d['inf']*100:.1f}% | {d['bnd']} |")
    P("")
    for c, d in s3.items():
        if d["quar"]:
            P(f"- 격리 {c}: 심볼 {dict(Counter(s for s,_ in d['quar']))} · 사유 {dict(Counter(r for _,rs in d['quar'] for r in rs))}")
    P(f"\n**V3 펀딩 = 확인 불가 → 0 가정.** 보유창이 정산시각을 ≥1회 포함하는 V3 거래 비율: "
      + ", ".join(f"{k} {v['cross']*100:.0f}%(n={v['n']})" for k, v in v3x.items()) + ".")
    if v3net is not None:
        P(f"V3 Net_A = {v3net:+.3f} R → 펀딩 민감도 구간 [{v3net-bound:+.3f}, {v3net+bound:+.3f}] (경계 ±{bound:.3f} R = 평균 n_cross·max|rate|/u; "
          f"max|rate| 사전표 사용. 92일 창 관측 최대: {', '.join(f'{s} {v:.5f}' for s, v in obs_max.items())}).")
    P("")
    P("## ④ 파라미터 수 · 장부 k\n")
    P("조정 파라미터 0개 (모든 값 사전등록 고정). 가설 장부 누적 k=1 → α=0.05.\n")
    P("## ⑤ 이 데이터로 알 수 없는 것\n")
    for s in ("메이커 체결 현실성(미체결·신호 편향) / 역선택 — A는 100% 체결 가정.", "2026-08-03 이전 펀딩 — V3 펀딩 확인 불가.",
              "`tickSz` 이력·실제 스프레드(호가 이력 없음) — 반틱은 하한.", "수수료 등급·요율 미검증(VIP0 가정).",
              "청산유형 복원은 `Note` 파싱·추론 의존 — 오분류는 maker/taker 배분을 바꿈.",
              "검정력: σ≈1.24R, n≈961에서 반폭≈±0.08R — |엣지|<~0.08R 구분 불가. 레짐 회전 미반영.",
              "V3 BE 승은 비용 후 뒤집힐 수 있어 V3 Gross/승률은 V4와 비교 부적합. 인트라바 순서는 재검증 안 함.",
              "호가 깊이·사이즈 슬리피지 없음. B/C는 방향 확인용이며 실전 근거 아님."):
        P(f"- {s}")
    P("\n## 사전등록 이탈\n")
    P("\n".join(f"- {d}" for d in dev) if dev else "없음.")
    open(out_md, "w", encoding="utf-8").write("\n".join(L) + "\n")
    res.update(verdict=ver, v4=dict(n=v4s["n"], gross=v4s["gross"], net_a=v4s["net"], ci_pct=v4s["ci_pct"], ci_blk=v4s["ci_blk"], ci=v4s["ci"]),
               cohorts=J, s1={f"{c}/{l}": d for (c, l), d in s1.items()}, s2=s2,
               s3=dict(cohorts={c: {k: v for k, v in d.items() if k != "quar"} | {"n_quar": len(d["quar"])} for c, d in s3.items()},
                       v3_cross=v3x, v3_bound=bound, v3_net_a=v3net), deviations=dev)
    json.dump(res, open(out_json, "w"), ensure_ascii=False, indent=1, default=str)
    return res


if __name__ == "__main__":
    D = os.path.join(ROOT, "research", "data")
    run(os.path.join(D, "notion_all.csv"), D, os.path.join(ROOT, "research", "reports", "phase1_report.md"),
        os.path.join(ROOT, "research", "reports", "phase1_results.json"))
