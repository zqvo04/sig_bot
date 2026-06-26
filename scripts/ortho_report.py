#!/usr/bin/env python3
"""
ortho_report.py — ORTHO 가상신호 R-기준 성과 리포트 (Stage 0: 증거 신뢰화)
════════════════════════════════════════════════════════════════════
PnL%는 코인별 변동성에 오염된다(BTC 0.2% 움직임 vs SOL 1% 움직임). 위험조정
성과는 반드시 R(= 손익 / SL거리)로 본다. 이 스크립트는 Notion에서 내보낸 CSV를
읽어 R-기준 기대값·코호트·캡처효율·손실 클러스터링을 출력한다.

Stage 0(증거 신뢰화) — 평균만으로는 우연과 엣지를 구분 못 한다. 다음을 추가:
  · 표본수 게이트   : 코호트 n<MIN_N 이면 '표본부족' (소표본 결론 차단)
  · 신뢰구간        : 승률 Wilson 구간 + 기대값(R) 부트스트랩 95% CI
                      → CI 하한>0 이라야 '엣지+'(0을 포함하면 노이즈)
  · 70/30 워크포워드: 시간순 앞70/뒤30 분할 — 양쪽 +R 인 코호트만 신뢰
  · 롱숏 대칭 패널  : 방향별 기대값 나란히 — 큰 비대칭 = 버그/레짐잔재 경고

  사용:  python3 scripts/ortho_report.py <notion_export.csv>
         (인자 생략 시 현재 폴더의 *_all.csv 중 최신 파일)

의존성 없음(표준 라이브러리). 봇 코드와 독립 — 데이터만 읽는다(과적합 표면 아님).
"""
import csv, sys, glob, os, re, math, random, datetime, statistics as st
from collections import defaultdict

MIN_N      = 30        # 코호트 결론 최소 표본(이 미만은 '표본부족'으로 차단)
BOOT_ITERS = 2000      # 부트스트랩 재표본 횟수
BOOT_SEED  = 12345     # 재현성(같은 CSV → 같은 CI)
Z          = 1.96      # 95% 정규 분위


# ════════════════════════════════════════════════════════════════════
# 통계 헬퍼 (표준 라이브러리만 — 과적합과 무관한 '측정 신뢰도' 도구)
# ════════════════════════════════════════════════════════════════════
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def wilson(k, n, z=Z):
    """승률(이항비율) Wilson 점수구간 → (lo%, hi%). 소표본에서도 안정."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return ((center - half) * 100.0, (center + half) * 100.0)


def bootstrap_ci(xs, iters=BOOT_ITERS, alpha=0.05):
    """평균의 퍼센타일 부트스트랩 CI → (lo, hi). 분포 가정 없음."""
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n == 0:
        return (None, None)
    if n == 1:
        return (xs[0], xs[0])
    rnd = random.Random(BOOT_SEED)
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += xs[rnd.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * iters)]
    hi = means[min(int((1 - alpha / 2) * iters), iters - 1)]
    return (lo, hi)


def _verdict(n, rlo, rhi):
    """기대값 CI 기반 판정: 표본부족 / 엣지+ / 엣지- / 노이즈."""
    if n < MIN_N:
        return "표본부족"
    if rlo is not None and rlo > 0:
        return "✓엣지+"
    if rhi is not None and rhi < 0:
        return "✗엣지-"
    return "~노이즈"


def _fmt(x):
    return f"{x:+.3f}" if x is not None else "  n/a "


def _both_plus(a, b):
    if a is None or b is None:
        return "—표본부족"
    return "✓두구간+" if (a > 0 and b > 0) else "✗불일치"


def _asym_warn(al, as_):
    if al is None or as_ is None:
        return "—표본부족"
    if (al > 0) != (as_ > 0):
        return "⚠부호반대(비대칭)"
    if abs(al - as_) > 0.30:
        return "⚠격차큼"
    return "대칭OK"


# ════════════════════════════════════════════════════════════════════
# 파싱
# ════════════════════════════════════════════════════════════════════
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse_dt(s):
    m = re.search(r'(\d+)년 (\d+)월 (\d+)일 (오전|오후) (\d+):(\d+)', s or '')
    if not m:
        return None
    y, mo, d, ap, h, mi = (int(g) if g.isdigit() else g for g in m.groups())
    if ap == '오후' and h != 12:
        h += 12
    if ap == '오전' and h == 12:
        h = 0
    return datetime.datetime(y, mo, d, h, mi)


def _polarity(r):
    """폴라리티 복원: Polarity 칼럼 → Engine(ORTHO-X) → Reason 첫 토큰 순."""
    p = (r.get('Polarity') or '').strip()
    if p:
        return p
    eng = (r.get('Engine') or '').strip()
    if eng.startswith('ORTHO-') and len(eng) > 6:
        return eng[6:]
    reason = (r.get('Reason') or '').strip()
    tok = reason.split()
    if tok and tok[0] in ('REV', 'CONT', 'BREAKOUT'):
        return tok[0]
    return '?'


def _regime(r):
    """레짐 복원: Reason/Note/Signal의 'RG=' 또는 'RGxxx' 토큰에서 추출."""
    blob = ' '.join(str(r.get(k) or '') for k in ('Reason', 'Note', 'Signal'))
    m = re.search(r'RG[=]?([A-Z]+)', blob)
    return m.group(1) if m else '?'


def load(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            r['pol'] = _polarity(r)
            r['regime'] = _regime(r)
            r['pnl'] = _f(r.get('PnL %'))
            r['mfe'] = _f(r.get('MFE R'))
            r['mae'] = _f(r.get('MAE R'))
            r['rr'] = _f(r.get('RR'))
            r['entry'] = _f(r.get('Entry'))
            r['rdist'] = _f(r.get('R Dist'))
            r['b2e'] = _f(r.get('Bars To Exit'))
            r['blocked_by'] = (r.get('Blocked By') or '').strip()   # Shadow DB 전용(없으면 '')
            r['t'] = _parse_dt(r.get('Signaled At'))
            # 실현 R = (PnL%/100) * Entry / R거리.  PnL%·Entry·R거리만으로 복원 가능.
            r['R'] = ((r['pnl'] / 100.0) * r['entry'] / r['rdist']
                      if None not in (r['pnl'], r['entry'], r['rdist']) and r['rdist'] else None)
            rows.append(r)
    return rows


# ════════════════════════════════════════════════════════════════════
# 코호트 라인 (Stage 0: n게이트 + Wilson승률CI + 부트스트랩 기대값CI + 판정)
# ════════════════════════════════════════════════════════════════════
def _line(label, g):
    n = len(g)
    if n == 0:
        return
    Rs = [r['R'] for r in g if r['R'] is not None]
    w = sum(1 for r in g if r['Status'] == 'WIN')
    avgR = _mean(Rs)
    wlo, whi = wilson(w, n)
    rlo, rhi = bootstrap_ci(Rs)
    flag = '!' if n < MIN_N else ' '
    ci = f"[{rlo:+.2f},{rhi:+.2f}]" if rlo is not None else "[  --  ]"
    print(f"  {label:16} n={n:3}{flag} win={w/n*100:4.0f}%[{wlo:3.0f}-{whi:3.0f}] "
          f"avgR={_fmt(avgR)} CI{ci:>15}  {_verdict(n, rlo, rhi)}")


def cohort(res, key, title):
    print(f"\n--- by {title} ---")
    d = defaultdict(list)
    for r in res:
        d[key(r)].append(r)
    for k in sorted(d, key=lambda x: str(x)):
        _line(str(k), d[k])


# ════════════════════════════════════════════════════════════════════
# Stage 0 — 워크포워드 70/30 (시간순) · 롱숏 대칭 패널
# ════════════════════════════════════════════════════════════════════
def walk_forward(res, split=0.7):
    print("\n[워크포워드 70/30] 시간순 분할 — 앞·뒤 양쪽 +R 인 코호트만 신뢰")
    ts = sorted([r for r in res if r['t'] is not None], key=lambda r: r['t'])
    if len(ts) < len(res):
        print(f"  ※ 시각 파싱 가능 {len(ts)}/{len(res)}건만 분할 대상")
    if len(ts) < 2 * MIN_N:
        print(f"  표본 {len(ts)}건 — 70/30 의미분할에 부족(권장 ≥{2*MIN_N}). 누적 후 재평가 권장.")
    if len(ts) < 4:
        return
    k = int(len(ts) * split)
    tr, te = ts[:k], ts[k:]

    def avg(group):
        return _mean([r['R'] for r in group if r['R'] is not None])

    print(f"  분할: 학습(앞) {len(tr)}건 / 검증(뒤) {len(te)}건")
    print(f"  {'전체':10} 학습 avgR={_fmt(avg(tr))}  검증 avgR={_fmt(avg(te))}  {_both_plus(avg(tr), avg(te))}")
    for p in sorted({r['pol'] for r in ts}):
        gtr = [r for r in tr if r['pol'] == p]
        gte = [r for r in te if r['pol'] == p]
        print(f"  {p:10} 학습 avgR={_fmt(avg(gtr))}({len(gtr):2})  "
              f"검증 avgR={_fmt(avg(gte))}({len(gte):2})  {_both_plus(avg(gtr), avg(gte))}")


def _fn_verdict(n, rlo, rhi):
    """Shadow(차단된) 코호트 판정 — 거부권 관점 뒤집힘:
       막힌 셋업이 *이겼을* 것(R CI 하한>0) → 그 게이트는 승자를 거름 = FN 생성기(롤백검토).
       막힌 셋업이 *졌을* 것(R CI 상한<0)   → 패자를 정확히 제거 = 정당한 거부권."""
    if n < MIN_N:
        return "표본부족(판정보류)"
    if rlo is not None and rlo > 0:
        return "✗FN 생성기(승자제거)→롤백검토"
    if rhi is not None and rhi < 0:
        return "✓정당(패자제거)"
    return "~노이즈(중립)"


def fn_panel(res):
    """FN 측정 패널 — Shadow DB 내보내기(Blocked By 보유)에서만 의미.
    각 차단 카테고리별로 'would-be 성과'를 보고, 그 게이트가 FN을 만드는지 판정한다."""
    shadow = [r for r in res if r.get('blocked_by')]
    if not shadow:
        return
    print("\n" + "=" * 70)
    print(f"[FN 측정 — Shadow 코호트] 차단된 셋업의 would-be 성과 ({len(shadow)}건 해소)")
    print("  해석: 막힌 거래가 이겼을 것(엣지+)이면 그 게이트가 '승자 제거'=FN. 졌을 것이면 정당.")
    print("-" * 70)
    d = defaultdict(list)
    for r in shadow:
        d[r['blocked_by']].append(r)
    # 라이브(차단 안 된) 비교 기준선 — 같은 CSV에 섞여 있을 때만
    live = [r for r in res if not r.get('blocked_by')]
    if live:
        lR = [r['R'] for r in live if r['R'] is not None]
        print(f"  {'(KEPT 라이브)':18} n={len(live):3}  avgR={_fmt(_mean(lR))}  ← 비교 기준선")
    for k in sorted(d, key=lambda x: str(x)):
        g = d[k]
        n = len(g)
        Rs = [r['R'] for r in g if r['R'] is not None]
        w = sum(1 for r in g if r['Status'] == 'WIN')
        rlo, rhi = bootstrap_ci(Rs)
        flag = '!' if n < MIN_N else ' '
        ci = f"[{rlo:+.2f},{rhi:+.2f}]" if rlo is not None else "[  --  ]"
        print(f"  {k:18} n={n:3}{flag} win={w/n*100:4.0f}% avgR={_fmt(_mean(Rs))} "
              f"CI{ci:>15}  {_fn_verdict(n, rlo, rhi)}")
    print("  ※ MACRO_FRESH 코호트가 n≥30에서 '엣지+'면 → ORTHO_MACRO_FRESH=false 롤백 근거.")


def symmetry_panel(res):
    print("\n[롱숏 대칭] 방향별 기대값 — 큰 비대칭(부호반대/격차>0.3R) = 버그·레짐잔재 경고")

    def side(group, d):
        gg = [r for r in group if (r.get('Direction') or '').upper() == d]
        return len(gg), _mean([r['R'] for r in gg if r['R'] is not None])

    groups = [("전체", res)] + [(p, [r for r in res if r['pol'] == p])
                                for p in sorted({r['pol'] for r in res})]
    for label, g in groups:
        nl, al = side(g, 'LONG')
        ns, as_ = side(g, 'SHORT')
        print(f"  {label:10} LONG avgR={_fmt(al)}({nl:2})   SHORT avgR={_fmt(as_)}({ns:2})   {_asym_warn(al, as_)}")


# ════════════════════════════════════════════════════════════════════
def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        cands = sorted(glob.glob('*_all.csv') + glob.glob('*.csv'), key=os.path.getmtime)
        path = cands[-1] if cands else None
    if not path or not os.path.exists(path):
        print("CSV 경로를 지정하세요: python3 scripts/ortho_report.py <export.csv>")
        sys.exit(1)

    rows = load(path)
    res = [r for r in rows if r['Status'] in ('WIN', 'LOSS')]
    wins = [r for r in res if r['Status'] == 'WIN']
    loss = [r for r in res if r['Status'] == 'LOSS']
    if not res:
        print("해소된(WIN/LOSS) 신호가 없습니다."); sys.exit(0)

    Rs = [r['R'] for r in res if r['R'] is not None]
    wR = [r['R'] for r in wins if r['R'] is not None]
    lR = [r['R'] for r in loss if r['R'] is not None]
    eR = _mean(Rs)
    elo, ehi = bootstrap_ci(Rs)
    print("=" * 70)
    print(f"ORTHO R-리포트 — {os.path.basename(path)}  (해소 {len(res)}건)")
    print("=" * 70)
    print(f"표기: ! = n<{MIN_N} 표본부족 · win[..] = 승률 Wilson95% · CI[..] = 기대값 부트스트랩95%")
    print(f"      엣지+ = 기대값 CI 하한>0 (0을 포함하면 ~노이즈, 우연일 수 있음)")
    print("-" * 70)
    print(f"승률              {len(wins)/len(res)*100:.1f}%  ({len(wins)}W / {len(loss)}L)  "
          f"Wilson95%[{wilson(len(wins), len(res))[0]:.0f}-{wilson(len(wins), len(res))[1]:.0f}]")
    ci = f"[{elo:+.3f}, {ehi:+.3f}]" if elo is not None else "[--]"
    print(f"기대값            {_fmt(eR)} R/거래   95%CI{ci}   {_verdict(len(res), elo, ehi)}  ← 핵심지표")
    if wR and lR:
        print(f"평균 승/패        +{st.mean(wR):.3f}R / {st.mean(lR):.3f}R   "
              f"(손익비 {st.mean(wR)/abs(st.mean(lR)):.2f})")
    gw = sum(r['pnl'] for r in wins if r['pnl'] is not None)
    gl = abs(sum(r['pnl'] for r in loss if r['pnl'] is not None))
    if gl > 0:
        print(f"Profit Factor     {gw/gl:.2f} (PnL%)   순손익 {gw-gl:+.2f}%")
    if wR and lR:
        be_wr = abs(st.mean(lR)) / (st.mean(wR) + abs(st.mean(lR))) * 100
        print(f"본전 승률         {be_wr:.1f}%   (현재 손익비에서 본전이 되는 승률)")
    if len(res) < MIN_N:
        print(f"\n⚠ 전체 표본 {len(res)}건 < {MIN_N} — 아래 결론은 모두 잠정. 단일변수 보정 전 추가 축적 필요.")

    # 캡처 효율(문제①)
    print("\n[캡처 효율] 이긴 거래가 유리한 움직임을 얼마나 실현했나")
    wmfe = _mean([r['mfe'] for r in wins if r['mfe'] is not None])
    if wmfe and wmfe > 0 and wR:
        print(f"  승리 평균 MFE {wmfe:.2f}R → 실현 {st.mean(wR):.2f}R = 캡처 {st.mean(wR)/wmfe*100:.0f}%")
    giveback = [r for r in loss if r['mfe'] is not None and r['mfe'] >= 1.0]
    if loss:
        print(f"  +1.0R 갔다가 풀손실로 되돌아온 패배: {len(giveback)}/{len(loss)}  ← 본전스톱 후보")
    if res and res[0].get('b2e') is not None:
        te = sum(1 for r in res if r['b2e'] == 8)
        print(f"  타임스톱(8봉) 종료: {te}/{len(res)} ({te/len(res)*100:.0f}%)")

    # Stage 0 핵심 — 신뢰성 분해
    walk_forward(res)
    symmetry_panel(res)
    fn_panel(res)   # Shadow 내보내기(Blocked By)일 때만 출력 — FN 측정 패널

    # 코호트 (각 라인에 n게이트·CI·판정 내장)
    cohort(res, lambda r: r['pol'], "Polarity")          # 폴라리티(REV/CONT/BREAKOUT)
    cohort(res, lambda r: r['regime'], "Regime")         # 레짐(RANGE/TREND/EXP)
    cohort(res, lambda r: f"{r['regime']}/{r['Direction']}", "Regime×Direction")
    cohort(res, lambda r: r['Direction'], "Direction")
    cohort(res, lambda r: r.get('MacroTag'), "MacroTag")
    cohort(res, lambda r: r.get('S_state'), "S_state")
    cohort(res, lambda r: r['Symbol'], "Symbol")
    cohort(res, lambda r: ('RR<1.5' if r['rr'] and r['rr'] < 1.5 else
                           'RR1.5-3' if r['rr'] and r['rr'] < 3 else
                           'RR3-5' if r['rr'] and r['rr'] < 5 else 'RR>=5'), "RR bucket")

    # 손실 클러스터링(문제②)
    print("\n[손실 클러스터링] 동시·연속 상관 노출")
    batch = defaultdict(list)
    for r in res:
        batch[r.get('Signaled At')].append(r)
    multi = [g for g in batch.values() if len(g) >= 2]
    homog = sum(1 for g in multi if len({r['Status'] for r in g}) == 1)
    print(f"  멀티신호 배치 {len(multi)}개 중 전승/전패(동질) {homog}개 "
          f"= {homog/len(multi)*100:.0f}%" if multi else "  멀티신호 배치 없음")
    ser = sorted(res, key=lambda r: (r['t'] or datetime.datetime.min))
    longest = cur = 0
    for r in ser:
        cur = cur + 1 if r['Status'] == 'LOSS' else 0
        longest = max(longest, cur)
    print(f"  최장 연속손실: {longest}연패")


if __name__ == "__main__":
    main()
