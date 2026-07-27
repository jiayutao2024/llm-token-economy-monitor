import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runner", ROOT / "scripts" / "run_storage_intel.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ontology = json.loads((ROOT / "config" / "ontology.json").read_text(encoding="utf-8"))
        cls.entities = json.loads((ROOT / "config" / "entities.json").read_text(encoding="utf-8"))["entities"]
        cls.now = datetime(2026, 7, 22, 12, tzinfo=timezone(timedelta(hours=8)))

    def analyze(self, title):
        item = {"title": title, "url": "https://www.amd.com/news", "summary_raw": "", "published_at": self.now.isoformat(), "publisher": "AMD", "feed_id": "test", "feed_name": "test", "feed_tier": 1}
        return runner.analyze(item, self.ontology, self.entities, {"amd.com": 1}, self.now)

    def test_hbm_order(self):
        row = self.analyze("AMD signs HBM4 supply agreement with Samsung Electronics")
        self.assertIn("HBM", row["products"])
        self.assertIn("Order/Contract", row["event_types"])
        self.assertIn("AMD", row["entities"])
        self.assertIn("Samsung Electronics", row["entities"])

    def test_sample_is_not_contract(self):
        row = self.analyze("SK hynix ships HBM4E samples for customer qualification")
        self.assertIn("Qualification/Sample", row["event_types"])
        self.assertNotIn("Order/Contract", row["event_types"])

    def test_capacity_implication(self):
        row = self.analyze("Micron raises DRAM capacity and capex for new fab")
        self.assertIn("Capacity/Capex", row["event_types"])
        self.assertIn("中期供给增加", row["first_order_effect"])

    def test_retail_deal_is_excluded(self):
        row = self.analyze("Save on an Amazon SSD deal with a 62% off coupon")
        self.assertTrue(row["excluded_as_retail_noise"])

    def test_cross_language_event_merge(self):
        first = self.analyze("CXMT plans DDR6 and LPDDR6 to capture the memory shortage")
        second = self.analyze("长鑫存储提前布局DDR6和LPDDR6")
        clustered = runner.cluster_events([first, second])
        self.assertEqual(len(clustered), 1)
        self.assertEqual(clustered[0]["merged_count"], 2)


if __name__ == "__main__":
    unittest.main()
