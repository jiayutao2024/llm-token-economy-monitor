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
price_spec = importlib.util.spec_from_file_location(
    "storage_prices", ROOT / "scripts" / "collect_storage_prices.py"
)
storage_prices = importlib.util.module_from_spec(price_spec)
price_spec.loader.exec_module(storage_prices)
macro_spec = importlib.util.spec_from_file_location(
    "macro", ROOT / "scripts" / "collect_macro_indicators.py"
)
macro = importlib.util.module_from_spec(macro_spec)
macro_spec.loader.exec_module(macro)


class DashboardV2Tests(unittest.TestCase):
    def test_percentile_rank(self):
        self.assertEqual(macro.percentile_rank([1, 2, 3, 4]), 100.0)
        self.assertEqual(macro.percentile_rank([1, 2, 3, 4], 2), 50.0)

    def test_kst_has_expected_warmup_and_direction(self):
        values = [100 + i for i in range(80)]
        result = macro.kst_series(values)
        self.assertIsNone(result[43])
        self.assertIsNotNone(result[44])
        self.assertGreater(result[-1], 0)

    def test_macro_snapshot_contract(self):
        path = ROOT / "data" / "macro_indicators_latest.json"
        if not path.exists():
            self.skipTest("macro snapshot is generated during refresh")
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["metrics"]
        self.assertEqual(len(rows), 8)
        self.assertEqual(len({row["metric_id"] for row in rows}), 8)
        for row in rows:
            self.assertIn("data_frequency", row)
            self.assertIn("source_date", row)
            self.assertGreater(row["sample_count"], 0)
            self.assertGreaterEqual(row["risk_percentile"], 0)
            self.assertLessEqual(row["risk_percentile"], 100)

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

    def test_public_pages_carry_owner_watermark(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("浙商证券研究所 · 陶嘉宇", html)
        self.assertIn('class="watermark-layer"', html)
        self.assertIn(".watermark-layer", css)
        self.assertIn("pointer-events:none", css)

    def test_public_storage_price_parser(self):
        parser = storage_prices.PricePageParser()
        parser.feed("""
        <div id="dram_spot" class="price-content">
          <div class="price-last-update"><p>Last Update 2026-07-27 18:10 (GMT+8)</p></div>
          <table><thead><tr><th>Item</th><th>Session Average</th><th>Session Change</th></tr></thead>
          <tbody><tr><td>DDR5 16Gb (2Gx8) 4800/5600</td><td>50.833</td><td>▲ 1.52 %</td></tr></tbody></table>
        </div>
        """)
        section = parser.tables["dram_spot"]
        self.assertIn("2026-07-27 18:10", section["last_update"])
        self.assertEqual(section["rows"][0]["Session Average"], "50.833")
        self.assertEqual(storage_prices.parse_number("▼ -0.61 %"), -0.61)

    def test_storage_price_history_deduplicates_daily_grain(self):
        metric = {
            "metric_id": "storage_test",
            "product": "DRAM",
            "segment": "DDR5",
            "quote_type": "现货",
            "price": 10.0,
            "change_pct": 1.0,
            "currency": "USD",
            "unit": "USD/官网报价单位",
            "observed_at": "2026-07-27T18:10+08:00",
            "source_url": "https://example.com",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            storage_prices.update_history(path, [metric])
            metric["price"] = 11.0
            rows = storage_prices.update_history(path, [metric])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 11.0)

    def test_storage_price_snapshot_quality(self):
        data = json.loads(
            (ROOT / "data" / "storage_prices_latest.json").read_text(encoding="utf-8")
        )
        rows = data["metrics"]
        ids = [row["metric_id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            self.assertGreater(row["price"], 0)
            self.assertLessEqual(row["low"], row["price"])
            self.assertGreaterEqual(row["high"], row["price"])
            self.assertEqual(row["source_tier"], 2)
            self.assertIn(row["freshness"]["status"], {"fresh", "stale"})


if __name__ == "__main__":
    unittest.main()
