import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ortho_config as oc
import ortho_engine as engine
import ortho_notion as notion
import ortho_v4 as v4


class _Response:
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {"id": "page-v4-test"}


class OrthoV4ContractTests(unittest.TestCase):
    def setUp(self):
        self.sig = {
            "symbol": "BTC/USDT",
            "polarity": "CONT",
            "direction": "long",
            "entry": 100.0,
            "tp": 102.0,
            "sl": 99.0,
            "r_dist": 1.0,
            "rr": 2.0,
            "bars_limit": 8,
            "l_pct": 35.0,
            "f_pct": 78.0,
            "s_state": "up3/3",
            "macro_tag": "UPLEG",
            "regime": "TREND",
            "signaled_at": "2026-08-15T12:00:00+09:00",
            "risk_quote": 100.0,
            "reason": "CONT LONG",
        }

    def test_stage_classification_keeps_alpha_and_execution_separate(self):
        self.assertEqual(v4.classify_stage("MACRO_FRESH", materialized=True),
                         ("ALPHA_SHADOW", "ALPHA", "MACRO_FRESH"))
        self.assertEqual(v4.classify_stage("spread(7.2bps)", materialized=True),
                         ("EXEC_REJECT", "EXECUTION", "SPREAD"))
        self.assertEqual(v4.classify_stage("EXPLORE:DROP_F", materialized=True),
                         ("APERTURE", "APERTURE", "EXPLORE"))
        self.assertEqual(v4.classify_stage(None, materialized=False),
                         ("ARMED", "NONE", ""))

    def test_cost_zero_live_contract_is_deterministic(self):
        first = v4.enrich_signal(dict(self.sig), oc, materialized=False)
        second = v4.enrich_signal(dict(self.sig), oc, materialized=False)
        self.assertEqual(first["v4_stage"], "ARMED")
        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(first["market_snapshot_hash"], second["market_snapshot_hash"])
        self.assertEqual(first["cost_mode"], "SIM_COST_0")
        self.assertEqual(first["estimated_cost_r"], 0.0)
        self.assertEqual(first["realized_cost_r"], 0.0)

        live = v4.enrich_signal(dict(first), oc, materialized=True)
        self.assertEqual(live["v4_stage"], "LIVE")
        self.assertEqual(live["fill_state"], "SIM_FILLED")
        self.assertEqual(live["net_rr"], self.sig["rr"])

    def test_cost_zero_outcome_equals_gross_and_net(self):
        ledger = v4.outcome_fields(1.25, oc)
        self.assertEqual(ledger["gross_r"], 1.25)
        self.assertEqual(ledger["net_r"], 1.25)
        self.assertEqual(ledger["realized_cost_r"], 0.0)

    def test_sim_cost_zero_disables_only_spread_veto(self):
        empty_context = {"ls_ratio": {"available": False}, "taker": {"available": False}}
        with patch.object(oc, "V4_ENABLED", True):
            self.assertIsNone(engine.context_veto("long", empty_context, 999.0))
        with patch.object(oc, "V4_ENABLED", False):
            self.assertEqual(engine.context_veto("long", empty_context, 999.0), "spread(999.0bps)")

    def test_notion_payload_writes_v4_fields(self):
        with patch.object(oc, "NOTION_TOKEN", "token"), \
             patch.object(oc, "NOTION_DATABASE_ID", "db"), \
             patch("ortho_notion.requests.post", return_value=_Response()) as post:
            page_id = notion.log_signal(dict(self.sig))

        self.assertEqual(page_id, "page-v4-test")
        payload = post.call_args.kwargs["json"]
        props = payload["properties"]
        self.assertEqual(props["V4 Stage"]["select"]["name"], "LIVE")
        self.assertEqual(props["Cost Mode"]["select"]["name"], "SIM_COST_0")
        self.assertEqual(props["Estimated Cost R"]["number"], 0.0)
        self.assertEqual(props["Realized Cost R"]["number"], 0.0)
        self.assertEqual(props["Veto Class"]["select"]["name"], "NONE")
        self.assertEqual(props["Risk Budget"]["number"], 100.0)

    def test_notion_outcome_writes_gross_and_net_r(self):
        with patch.object(oc, "NOTION_TOKEN", "token"), \
             patch.object(oc, "NOTION_DATABASE_ID", "db"), \
             patch("ortho_notion.requests.patch", return_value=_Response()) as patch_request:
            ok = notion.update_outcome("page-v4-test", "WIN", pnl_r=1.5, exit_reason="TP")

        self.assertTrue(ok)
        props = patch_request.call_args.kwargs["json"]["properties"]
        self.assertEqual(props["Gross R"]["number"], 1.5)
        self.assertEqual(props["Net R"]["number"], 1.5)
        self.assertEqual(props["Realized Cost R"]["number"], 0.0)


if __name__ == "__main__":
    unittest.main()
