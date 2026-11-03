"""Phase 0 pass criterion ③: harness R vs Notion-logged R on 50 random V4 rows (seed 12345).
logged R = PnL% / 100 * Entry / R Dist. Pass iff |dR| < 0.01 for every row.
Reports only the per-row comparison; deliberately prints no aggregate performance (means, win rates).
Candles come from ReplayExchange (OKX REST windows), never from the sealed holdout dir."""
import csv, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h

CSV = os.path.join(h.DATA, "notion_all.csv")
TOL, N, SEED, SID = 0.01, 50, 12345, "ORTHO-4.SIM0"


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def likely_cause(row, r, logged):
    if not r["resolved"]:
        return "harness unresolved: replay window short/missing candles (OKX gap, delisting, or window not elapsed)"
    causes = []
    if (row.get("Status") or "").upper() != r["status"]:
        causes.append(f"status differs (log {row.get('Status')} vs {r['status']})")
    bte = num(row.get("Bars To Exit"))
    if bte is not None and int(bte) != r["bars"]:
        causes.append(f"exit bar differs (log {int(bte)} vs {r['bars']}): live resolver saw a different candle set "
                      "(unconfirmed last candle at resolve time, or OKX revised a candle)")
    if r["exit_reason"] == "TIME":
        causes.append("TIME exit: live close may have come from a still-forming 5m candle")
    return "; ".join(causes) or "unknown (check signaled_at timezone / bars_limit / BE config)"


def main():
    if not os.path.exists(CSV):
        print(f"validation pending: {CSV} not found"); return 2
    assert not h.oc.BE_TRIGGER_R, "BE active (ORTHO_V4_ENABLED=false?) -- V4 rows were scored without BE"
    with open(CSV, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("Strategy ID") == SID
                and (r.get("Status") or "").upper() in ("WIN", "LOSS") and num(r.get("PnL %")) is not None]
    rows.sort(key=lambda r: (r["Signaled At (ISO)"], r["Symbol"], r["Direction"]))
    pick = random.Random(SEED).sample(rows, min(N, len(rows)))
    ex = h.ReplayExchange()
    bad = []
    print(f"{'#':>2} {'symbol':<10}{'dir':<6}{'signaled_at':<27}{'log_R':>8}{'harn_R':>8}{'dR':>9}  exit")
    for i, row in enumerate(pick):
        entry, r_dist = float(row["Entry"]), float(row["R Dist"])
        e = dict(symbol=row["Symbol"], direction=row["Direction"], signaled_at=row["Signaled At (ISO)"],
                 entry=entry, tp=float(row["TP"]), sl=float(row["SL"]), r_dist=r_dist,
                 bars_limit=int(float(row.get("Bars Limit") or 0)) or None)
        r = h.score([e], ex)[0]
        logged = float(row["PnL %"]) / 100 * entry / r_dist
        d = None if r["gross_r"] is None else r["gross_r"] - logged
        ok = d is not None and abs(d) < TOL
        print(f"{i:>2} {row['Symbol']:<10}{row['Direction']:<6}{row['Signaled At (ISO)']:<27}{logged:>8.3f}"
              f"{(r['gross_r'] if r['gross_r'] is not None else float('nan')):>8.3f}"
              f"{(d if d is not None else float('nan')):>9.4f}  {r['exit_reason']}/{r['bars']} {'' if ok else 'MISMATCH'}")
        if not ok:
            bad.append((i, row, r, logged))
    print(f"\nrows compared: {len(pick)} (eligible {len(rows)}) | mismatches |dR|>={TOL}: {len(bad)}")
    for i, row, r, logged in bad:
        print(f"  #{i} {row['Symbol']} {row['Direction']} {row['Signaled At (ISO)']}: {likely_cause(row, r, logged)}")
    print("PASS" if not bad and len(pick) == N else "FAIL")
    return 0 if not bad and len(pick) == N else 1


if __name__ == "__main__":
    sys.exit(main())
