import os
import sys
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ortho_config as oc
import ortho_resolver as resolver


class _Ex:
    def __init__(self, candles):
        self.candles = candles

    def fetch_ohlcv(self, symbol, tf, since=None, limit=None):
        return self.candles


def _sig(since_ms):
    iso = datetime.fromtimestamp(since_ms / 1000, timezone.utc).isoformat()
    return {"symbol": "BTC/USDT", "direction": "long", "entry": 100.0, "tp": 110.0,
            "sl": 90.0, "r_dist": 10.0, "bars_limit": 8, "signaled_at": iso}


class ResolverClosedOnlyTests(unittest.TestCase):
    def setUp(self):
        # 24 x 5m candles; the last one opened 1 minute ago (still forming), price drifting up.
        now = int(time.time() * 1000)
        last_open = now - 60_000
        self.since = last_open - 23 * 300_000
        self.candles = [[self.since + i * 300_000, 100, 101, 99.5, 100 + 0.1 * i, 1] for i in range(24)]

    def test_default_off_keeps_legacy_time_exit_on_forming_candle(self):
        with patch.object(oc, "RESOLVER_CLOSED_ONLY", False):
            r = resolver.evaluate_outcome(_Ex(self.candles), _sig(self.since))
        self.assertTrue(r["resolved"])
        self.assertEqual(r["exit_reason"], "TIME")

    def test_on_waits_for_the_24th_candle_to_close(self):
        with patch.object(oc, "RESOLVER_CLOSED_ONLY", True):
            r = resolver.evaluate_outcome(_Ex(self.candles), _sig(self.since))
        self.assertFalse(r["resolved"])

    def test_on_still_catches_barrier_hits_in_closed_candles(self):
        c = [row[:] for row in self.candles]
        c[3][3] = 89.0  # closed candle touches SL
        with patch.object(oc, "RESOLVER_CLOSED_ONLY", True):
            r = resolver.evaluate_outcome(_Ex(c), _sig(self.since))
        self.assertEqual((r["status"], r["exit_reason"]), ("LOSS", "SL"))


if __name__ == "__main__":
    unittest.main()
