import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "unified", ROOT / "scripts" / "build_unified_data.py"
)
unified = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unified)


class DashboardV2Tests(unittest.TestCase):
    def test_stage_matrix(self):
        self.assertEqual(
            unified.determine_stage(80, 70)["stage_short"], "共振拥挤"
        )
        self.assertEqual(
            unified.determine_stage(75, 40)["stage_short"], "泡沫兑现风险"
        )
        self.assertEqual(
            unified.determine_stage(30, 30)["stage_short"], "起步验证"
        )
        self.assertEqual(
            unified.determine_stage(None, 70)["stage_short"], "证据不足"
        )

    def test_freshness(self):
        now = datetime(2026, 7, 27, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(
            unified.freshness("2026-07-20", 10, now)["status"], "fresh"
        )
        self.assertEqual(
            unified.freshness("2026-01-01", 10, now)["status"], "stale"
        )

    def test_normalized_metric_contract(self):
        row = unified.normalized_metric(
            "gpu_test", 2.5, "USD/GPU·小时", "海外", "2026-07-27",
            "Official", "https://example.com", 1, "official",
            "2026-07-27T07:30:00+08:00",
        )
        required = {
            "metric_id", "value", "unit", "currency", "region", "period",
            "source_name", "source_url", "source_tier", "evidence_status",
            "collected_at", "note",
        }
        self.assertTrue(required.issubset(row))

    def test_framework_ids_are_unique(self):
        data = json.loads(
            (ROOT / "data" / "framework_metrics.json").read_text(encoding="utf-8")
        )
        ids = []
        for key in ("macro_indicators", "industry_signals", "gpu_rental", "storage_price_signals"):
            ids.extend(row["metric_id"] for row in data[key])
        self.assertEqual(len(ids), len(set(ids)))

    def test_web_has_no_remote_runtime(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("https://", html)
        self.assertNotIn("cdn.", html.lower())
        self.assertNotIn("fetch(\"http", js.lower())


if __name__ == "__main__":
    unittest.main()
