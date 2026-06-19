#!/usr/bin/env python3
"""
ortho_report.py — ORTHO 가상신호 R-기준 성과 리포트 (C-1 평가 전면화)
════════════════════════════════════════════════════════════════════
PnL%는 코인별 변동성에 오염된다(BTC 0.2% 움직임 vs SOL 1% 움직임). 위험조정
성과는 반드시 R(= 손익 / SL거리)로 본다. 이 스크립트는 Notion에서 내보낸 CSV를
읽어 R-기준 기대값·코호트·캡처효율·손실 클러스터링을 출력한다.

  사용:  python3 scripts/ortho_report.py <notion_export.csv>
         (인자 생략 시 현재 폴더의 *_all.csv 중 최신 파일)

의존성 없음(표준 라이브러리). 봇 코드와 독립 — 데이터만 읽는다.
"""
import csv, sys, glob, os, re, datetime, statistics as st
from collections import defaultdict


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
            r['t'] = _parse_dt(r.get('Signaled At'))
            # 실현 R = (PnL%/100) * Entry / R거리.  PnL%·Entry·R거리만으로 복원 가능.
            r['R'] = ((r['pnl'] / 100.0) * r['entry'] / r['rdist']
                      if None not in (r['pnl'], r['entry'], r['rdist']) and r['rdist'] else None)
            rows.append(r)
    return rows


def _line(label, g):
    n = len(g)
    w = sum(1 for r in g if r['Status'] == 'WIN')
    avgR = st.mean([r['R'] for r in g if r['R'] is not None]) if g else 0.0
    spnl = sum(r['pnl'] for r in g if r['pnl'] is not None)
    print(f"  {label:14} n={n:3}  win%={w/n*100:5.1f}  avgR={avgR:+.3f}  sumPnL%={spnl:+.2f}")


def cohort(res, key, title):
    print(f"\n--- by {title} ---")
    d = defaultdict(list)
    for r in res:
        d[key(r)].append(r)
    for k in sorted(d, key=lambda x: str(x)):
        _line(str(k), d[k])


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
    print("=" * 64)
    print(f"ORTHO R-리포트 — {os.path.basename(path)}  (해소 {len(res)}건)")
    print("=" * 64)
    print(f"승률              {len(wins)/len(res)*100:.1f}%  ({len(wins)}W / {len(loss)}L)")
    print(f"기대값            {st.mean(Rs):+.3f} R/거래   ← 위험조정 핵심지표")
    print(f"평균 승/패        +{st.mean(wR):.3f}R / {st.mean(lR):.3f}R   "
          f"(손익비 {st.mean(wR)/abs(st.mean(lR)):.2f})")
    gw = sum(r['pnl'] for r in wins if r['pnl'] is not None)
    gl = abs(sum(r['pnl'] for r in loss if r['pnl'] is not None))
    print(f"Profit Factor     {gw/gl:.2f} (PnL%)   순손익 {gw-gl:+.2f}%")
    be_wr = abs(st.mean(lR)) / (st.mean(wR) + abs(st.mean(lR))) * 100
    print(f"본전 승률         {be_wr:.1f}%   (현재 손익비에서 본전이 되는 승률)")

    # 캡처 효율(문제①)
    print("\n[캡처 효율] 이긴 거래가 유리한 움직임을 얼마나 실현했나")
    wmfe = st.mean([r['mfe'] for r in wins if r['mfe'] is not None])
    print(f"  승리 평균 MFE {wmfe:.2f}R → 실현 {st.mean(wR):.2f}R = 캡처 {st.mean(wR)/wmfe*100:.0f}%")
    giveback = [r for r in loss if r['mfe'] is not None and r['mfe'] >= 1.0]
    print(f"  +1.0R 갔다가 풀손실로 되돌아온 패배: {len(giveback)}/{len(loss)}  ← 본전스톱 후보")
    if res and res[0].get('b2e') is not None:
        te = sum(1 for r in res if r['b2e'] == 8)
        print(f"  타임스톱(8봉) 종료: {te}/{len(res)} ({te/len(res)*100:.0f}%)")

    cohort(res, lambda r: r['pol'], "Polarity")          # R7 폴라리티 코호트(REV/CONT)
    cohort(res, lambda r: r['regime'], "Regime")         # R7 레짐 코호트(RANGE/TREND/EXP)
    cohort(res, lambda r: f"{r['regime']}/{r['Direction']}", "Regime×Direction")
    cohort(res, lambda r: r['Direction'], "Direction")
    cohort(res, lambda r: r['MacroTag'], "MacroTag")
    cohort(res, lambda r: r['S_state'], "S_state")
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
