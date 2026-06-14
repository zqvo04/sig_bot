#!/usr/bin/env python3
"""
ortho_sweep.py — BE_TRIGGER × RR_MAX 민감도 스윕 (안정 구간 탐색)
════════════════════════════════════════════════════════════════════
청산 파라미터(A-1 본전스톱 트리거, A-4 TP 상한)를 격자로 훑어 기대값·낙폭이
"평탄한 고원(plateau)"에 있는지, 아니면 깨지기 쉬운 peak인지 본다. 단일 최적값을
좇지 않는다 — 과적합 경계.

방법론(중요): Notion CSV에는 거래당 4점(진입·MFE·MAE·청산)만 있고 봉별 경로가
없다. BE/캡 결과는 유리/불리 excursion의 '순서'에 좌우되므로 두 극단으로 시뮬한다:
  · fav (유리먼저) : BE가 달리는 승자를 최대로 조기청산 → BE에 비관적
  · adv (불리먼저) : 승자 보존 → 낙관적
진짜 값은 이 밴드 안. 밴드가 좁고(=순서에 강건) 격자상 평탄하며 두 순서 모두에서
좋은 영역만 '안정'으로 친다. 베이스라인(BE off / 캡 ∞)은 실측 기대값을 재현해야 한다.

  사용:  python3 scripts/ortho_sweep.py <notion_export.csv>

표준 라이브러리만 사용. 봇 코드와 독립(데이터만 읽음). BE_LOCK은 config 기본 0.05.
"""
import csv, sys, glob, os, statistics as st

INF = float('inf')
BE_LOCK = 0.05            # = ortho_config.BE_LOCK_R 기본값
BE_GRID = [INF, 1.5, 1.4, 1.3, 1.2, 1.1, 1.0, 0.9, 0.8]
RR_GRID = [2.5, 3.0, 3.5, 4.0, INF]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load(path):
    T = []
    for r in csv.DictReader(open(path, encoding='utf-8')):
        r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
        if r.get('Status') not in ('WIN', 'LOSS'):
            continue
        pnl, e, rd = _f(r.get('PnL %')), _f(r.get('Entry')), _f(r.get('R Dist'))
        mfe, mae, rr = _f(r.get('MFE R')), _f(r.get('MAE R')), _f(r.get('RR'))
        if None in (pnl, e, rd, mfe, mae, rr) or not rd:
            continue
        R = max(-mae, min(mfe, pnl / 100 * e / rd))   # 종가R은 [-MAE,+MFE] 안으로 일관화
        T.append((R, mfe, mae, rr))
    return T


def sim(R, mfe, mae, rro, betrig, rrmax, order):
    """새 배리어(BE 트리거, RR 상한) 하의 1거래 실현 R. SL = -1.0R(정의상)."""
    tp = min(rro, rrmax)
    if order == 'fav':                       # 유리→불리
        if mfe >= tp:        return tp
        if mfe >= betrig:    return BE_LOCK
        if mae >= 1.0:       return -1.0
        return R
    else:                                    # 불리→유리
        if mae >= 1.0:       return -1.0
        if mfe >= tp:        return tp
        if mfe >= betrig and R < BE_LOCK:
            return BE_LOCK
        return R


def E(T, betrig, rrmax, order):
    return sum(sim(*t, betrig, rrmax, order) for t in T) / len(T)


def blend(T, betrig, rrmax):
    return (E(T, betrig, rrmax, 'fav') + E(T, betrig, rrmax, 'adv')) / 2


def maxdd(xs):
    cum = peak = dd = 0.0
    for x in xs:
        cum += x; peak = max(peak, cum); dd = min(dd, cum - peak)
    return dd


def _hdr():
    return "BE\\RR  " + "".join(f"{('∞' if c == INF else c):>8}" for c in RR_GRID)


def _row(label, vals, fmt="{:>+8.3f}"):
    return f"{label:>6} " + "".join(fmt.format(v) for v in vals)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        c = sorted(glob.glob('*_all.csv') + glob.glob('*.csv'), key=os.path.getmtime)
        path = c[-1] if c else None
    if not path or not os.path.exists(path):
        print("사용: python3 scripts/ortho_sweep.py <export.csv>"); sys.exit(1)

    T = load(path)
    if not T:
        print("해소(WIN/LOSS) 신호 없음"); sys.exit(0)
    base = blend(T, INF, INF)
    print(f"표본 {len(T)}건 | 베이스라인 E(BE off/캡 ∞)={base:+.3f}R  ← 실측과 일치해야 정상\n")

    print("【표1】 기대값 E (R/거래, fav·adv 평균)   ※클수록 좋음")
    print(_hdr())
    for b in BE_GRID:
        print(_row('∞(off)' if b == INF else b, [blend(T, b, c) for c in RR_GRID]))

    print("\n【표2】 비관 E_fav (승자 조기청산 최대)   ※이 값도 +면 견고")
    print(_hdr())
    for b in BE_GRID:
        print(_row('∞(off)' if b == INF else b, [E(T, b, c, 'fav') for c in RR_GRID]))

    print("\n【표3】 순서민감도 |E_adv−E_fav|   ※작을수록 경로가정에 강건")
    print(_hdr())
    for b in BE_GRID:
        print(_row('∞(off)' if b == INF else b,
                   [abs(E(T, b, c, 'adv') - E(T, b, c, 'fav')) for c in RR_GRID],
                   fmt="{:>8.3f}"))

    print("\n【표4】 최대낙폭 maxDD (R) — fav 순서 (보수)   ※0에 가까울수록 좋음")
    print(_hdr())
    for b in BE_GRID:
        print(_row('∞(off)' if b == INF else b,
                   [maxdd([sim(*t, b, c, 'fav') for t in T]) for c in RR_GRID],
                   fmt="{:>8.1f}"))

    print("\n해석: 두 순서 모두 +이고(표1·표2), 순서민감도가 작고(표3), 격자상 평탄한")
    print("      영역만 '안정'. 단일 peak는 소표본 우연일 수 있으니 신뢰 금물.")


if __name__ == "__main__":
    main()
