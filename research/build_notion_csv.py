"""notion_raw.jsonl -> notion_all.csv (PREREG §9). Dates: UTC ISO -> KST, ortho_report._parse_dt format."""
import csv, json, sys, datetime as dt
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from ortho_report import _parse_dt

D = ROOT / 'research' / 'data'
COLS = ['url', 'Signal', 'Symbol', 'Direction', 'Status', 'Strategy ID', 'Engine', 'Polarity', 'Entry', 'SL', 'TP',
        'R Dist', 'RR', 'Bars Limit', 'Bars To Exit', 'PnL %', 'Gross R', 'Net R', 'Cost Mode', 'Fill State',
        'V4 Stage', 'Entry Drift R', 'Estimated Cost R', 'Realized Cost R', 'MFE R', 'MAE R', 'Note', 'Reason']
DATES = ['Signaled At', 'Resolved At', 'Snapshot At']


def kst(iso):
    if not iso:
        return ''
    assert iso.endswith('Z'), iso  # shortcut: only UTC 'Z' stamps seen; anything else must be inspected
    t = dt.datetime.strptime(iso, '%Y-%m-%d %H:%M:%SZ') + dt.timedelta(hours=9)
    h = t.hour % 12 or 12
    return f"{t.year}년 {t.month}월 {t.day}일 {'오전' if t.hour < 12 else '오후'} {h}:{t.minute:02d}"


def cell(v):
    return '' if v is None else v


rows = [json.loads(l) for l in open(D / 'notion_raw.jsonl', encoding='utf-8')]
header = COLS[:]
for c in DATES:
    header += [c, f'{c} (ISO)']
with open(D / 'notion_all.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(header)
    for r in rows:
        out = [cell(r[c]) for c in COLS]
        for c in DATES:
            iso = r[f'date:{c}:start']
            out += [kst(iso), cell(iso)]
        w.writerow(out)

# self-check
back = list(csv.DictReader(open(D / 'notion_all.csv', encoding='utf-8')))
assert len(back) == len(rows) == 1907, len(back)
assert len({r['url'] for r in back}) == len(back), 'duplicate url'
assert all(_parse_dt(r['Signaled At']) for r in back), 'unparseable Signaled At'
assert all(r['Status'] in ('WIN', 'LOSS') for r in back)
assert kst('2026-06-10 15:00:00Z') == '2026년 6월 11일 오전 12:00' and kst('2026-06-10 03:05:00Z') == '2026년 6월 10일 오후 12:05'
print('ok', len(back))
