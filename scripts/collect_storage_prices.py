#!/usr/bin/env python3
"""Collect bounded public storage-price snapshots from TrendForce price pages."""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
USER_AGENT = "Mozilla/5.0 (compatible; ZSZQ-Storage-Monitor/2.0; public research)"
SOURCES = {
    "dram": "https://www.trendforce.com/price/dram/module_spot",
    "flash": "https://www.trendforce.com/price/flash/ssd_street",
}

# Public snapshots only. Unit is deliberately kept as the publisher's quote
# unit because chip, module, wafer and finished-drive prices are not additive.
METRIC_SPECS = [
    {
        "metric_id": "storage_dram_ddr5_16gb_spot",
        "source": "dram",
        "section": "dram_spot",
        "item": "DDR5 16Gb (2Gx8) 4800/5600",
        "product": "DRAM",
        "segment": "DDR5",
        "quote_type": "现货",
    },
    {
        "metric_id": "storage_dram_ddr4_16gb_spot",
        "source": "dram",
        "section": "dram_spot",
        "item": "DDR4 16Gb (2Gx8) 3200",
        "product": "DRAM",
        "segment": "DDR4",
        "quote_type": "现货",
    },
    {
        "metric_id": "storage_dram_ddr5_rdimm_32gb",
        "source": "dram",
        "section": "module_spot",
        "item": "DDR5 RDIMM 32GB 4800/5600",
        "product": "DRAM",
        "segment": "服务器RDIMM",
        "quote_type": "模组现货",
    },
    {
        "metric_id": "storage_dram_ddr5_sodimm_8gb_contract",
        "source": "dram",
        "section": "dram_contract",
        "item": "DDR5 8GB SO-DIMM",
        "product": "DRAM",
        "segment": "PC DRAM",
        "quote_type": "合约",
    },
    {
        "metric_id": "storage_gddr6_8gb_spot",
        "source": "dram",
        "section": "gddr_spot",
        "item": "GDDR6 8Gb",
        "product": "GDDR",
        "segment": "GDDR6",
        "quote_type": "现货",
    },
    {
        "metric_id": "storage_nand_mlc_64gb_spot",
        "source": "flash",
        "section": "flash_spot",
        "item": "MLC 64Gb 8GBx8",
        "product": "NAND",
        "segment": "MLC",
        "quote_type": "现货",
    },
    {
        "metric_id": "storage_nand_tlc_512gb_spot",
        "source": "flash",
        "section": "wafer_spot",
        "item": "512Gb TLC",
        "product": "NAND",
        "segment": "TLC",
        "quote_type": "晶圆现货",
    },
    {
        "metric_id": "storage_nand_tlc_256gb_spot",
        "source": "flash",
        "section": "wafer_spot",
        "item": "256Gb TLC",
        "product": "NAND",
        "segment": "TLC",
        "quote_type": "晶圆现货",
    },
    {
        "metric_id": "storage_nand_128gb_contract",
        "source": "flash",
        "section": "flash_contract",
        "item": "NAND 128Gb 16Gx8 MLC",
        "product": "NAND",
        "segment": "MLC",
        "quote_type": "合约",
    },
    {
        "metric_id": "storage_ssd_samsung_990pro_1tb",
        "source": "flash",
        "section": "ssd_street",
        "contains": ["Samsung", "990 Pro", "1 TB"],
        "product": "SSD",
        "segment": "客户端SSD",
        "quote_type": "终端零售",
    },
]


class PricePageParser(HTMLParser):
    """Extract price tables grouped by the page's public price-content IDs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.section: str | None = None
        self.section_depth: int | None = None
        self.last_update = ""
        self.in_update = False
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.cell_text: list[str] = []
        self.row: list[str] = []
        self.headers: list[str] = []
        self.tables: dict[str, dict[str, Any]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag == "div":
            self.div_depth += 1
            classes = (attrs_map.get("class") or "").split()
            if self.section is None and "price-content" in classes and attrs_map.get("id"):
                self.section = attrs_map["id"]
                self.section_depth = self.div_depth
                self.last_update = ""
                self.tables.setdefault(self.section, {"last_update": "", "headers": [], "rows": []})
            if self.section and "price-last-update" in classes:
                self.in_update = True
        if self.section and tag == "table" and not self.in_table:
            self.in_table = True
            self.headers = []
        elif self.in_table and tag == "tr":
            self.in_row = True
            self.row = []
        elif self.in_row and tag in {"th", "td"}:
            self.in_cell = True
            self.cell_text = []

    def handle_data(self, data: str) -> None:
        if self.in_update:
            self.last_update += " " + data
        if self.in_cell:
            self.cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_cell and tag in {"th", "td"}:
            value = " ".join("".join(self.cell_text).split())
            self.row.append(html.unescape(value))
            self.in_cell = False
        elif self.in_row and tag == "tr":
            if self.row:
                if not self.headers:
                    self.headers = self.row
                else:
                    self.tables[self.section]["rows"].append(
                        dict(zip(self.headers, self.row))
                    )
            self.in_row = False
        elif self.in_table and tag == "table":
            self.tables[self.section]["headers"] = self.headers
            self.tables[self.section]["last_update"] = " ".join(self.last_update.split())
            self.in_table = False
        if tag == "div":
            if self.section and self.div_depth == self.section_depth:
                self.section = None
                self.section_depth = None
                self.in_update = False
            self.div_depth -= 1


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_observed_at(value: str, fallback: datetime) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", value)
    if not match:
        return fallback.isoformat(timespec="minutes")
    return datetime.strptime(
        f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=CN_TZ).isoformat(timespec="minutes")


def select_row(rows: list[dict[str, str]], spec: dict[str, Any]) -> dict[str, str] | None:
    for row in rows:
        text = " | ".join(row.values())
        if spec.get("item") and spec["item"] == row.get("Item"):
            return row
        if spec.get("contains") and all(token.lower() in text.lower() for token in spec["contains"]):
            return row
    return None


def quote_from_row(
    spec: dict[str, Any],
    row: dict[str, str],
    last_update: str,
    source_url: str,
    collected_at: str,
) -> dict[str, Any]:
    average = (
        row.get("Session Average")
        or row.get("Average")
        or row.get("Avg.")
    )
    high = row.get("Session High") or row.get("Daily High") or row.get("High")
    low = row.get("Session Low") or row.get("Daily Low") or row.get("Low")
    change = (
        row.get("Session Change")
        or row.get("Average Change")
        or row.get("Change")
    )
    observed_at = parse_observed_at(last_update, datetime.now(CN_TZ))
    allowed_days = 100 if spec["quote_type"] == "合约" else 21
    age_days = max(
        0,
        (
            datetime.now(CN_TZ)
            - datetime.fromisoformat(observed_at)
        ).days,
    )
    return {
        "metric_id": spec["metric_id"],
        "product": spec["product"],
        "segment": spec["segment"],
        "item": spec.get("item") or " / ".join(spec["contains"]),
        "quote_type": spec["quote_type"],
        "price": parse_number(average),
        "high": parse_number(high),
        "low": parse_number(low),
        "change_pct": parse_number(change),
        "currency": "USD",
        "unit": "USD/官网报价单位",
        "observed_at": observed_at,
        "collected_at": collected_at,
        "source_name": "TrendForce公开价格页",
        "source_url": source_url,
        "source_tier": 2,
        "evidence_status": "public_snapshot",
        "freshness": {
            "status": "fresh" if age_days <= allowed_days else "stale",
            "age_days": age_days,
            "allowed_days": allowed_days,
        },
        "note": "仅保存公开页面当前快照；芯片、模组、晶圆与整盘报价单位不同，不做直接加总。",
    }


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def update_history(path: Path, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except json.JSONDecodeError:
                continue
    by_key = {
        (row.get("date"), row.get("metric_id")): row
        for row in rows
        if row.get("date") and row.get("metric_id")
    }
    for metric in metrics:
        if metric.get("price") is None:
            continue
        observed = metric.get("observed_at", "")[:10]
        by_key[(observed, metric["metric_id"])] = {
            "date": observed,
            "metric_id": metric["metric_id"],
            "product": metric["product"],
            "segment": metric["segment"],
            "quote_type": metric["quote_type"],
            "price": metric["price"],
            "change_pct": metric.get("change_pct"),
            "currency": metric["currency"],
            "unit": metric["unit"],
            "source_url": metric["source_url"],
        }
    rows = sorted(by_key.values(), key=lambda row: (row["date"], row["metric_id"]))[-4000:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    now = datetime.now(CN_TZ)
    collected_at = now.isoformat(timespec="seconds")
    pages: dict[str, dict[str, dict[str, Any]]] = {}
    errors: list[str] = []
    source_status = []

    for source_id, url in SOURCES.items():
        try:
            parser_obj = PricePageParser()
            parser_obj.feed(fetch_text(url))
            pages[source_id] = parser_obj.tables
            source_status.append({"source": source_id, "url": url, "status": "ok"})
        except Exception as exc:  # noqa: BLE001 - source failures must not stop stale fallback
            errors.append(f"{source_id}: {type(exc).__name__}: {exc}")
            source_status.append({"source": source_id, "url": url, "status": "error"})

    metrics = []
    for spec in METRIC_SPECS:
        section = pages.get(spec["source"], {}).get(spec["section"], {})
        row = select_row(section.get("rows", []), spec)
        if not row:
            errors.append(f"{spec['metric_id']}: public quote row not found")
            continue
        metrics.append(
            quote_from_row(
                spec,
                row,
                section.get("last_update", ""),
                SOURCES[spec["source"]],
                collected_at,
            )
        )

    latest_path = root / "data" / "storage_prices_latest.json"
    previous = load_json(latest_path, {})
    if not metrics and previous.get("metrics"):
        metrics = previous["metrics"]
        for row in metrics:
            row["evidence_status"] = "stale_fallback"
        errors.append("本轮公开价格抓取失败，保留最近成功快照。")

    history = update_history(root / "data" / "storage_price_history.jsonl", metrics)
    valid = [row for row in metrics if row.get("price") is not None]
    fresh = [row for row in valid if row.get("freshness", {}).get("status") == "fresh"]
    up = [row for row in fresh if (row.get("change_pct") or 0) > 0]
    down = [row for row in fresh if (row.get("change_pct") or 0) < 0]
    breadth = round(len(up) / len(fresh) * 100, 1) if fresh else None
    payload = {
        "meta": {
            "generated_at": collected_at,
            "scope": "TrendForce公开价格页当前快照",
            "license_boundary": "不抓取会员历史下载；历史仅由本项目每日公开快照自行积累。",
            "unit_policy": "不同报价类型不混加；单位显示为官网报价单位。",
        },
        "summary": {
            "metric_count": len(valid),
            "fresh_count": len(fresh),
            "up_count": len(up),
            "flat_count": len(fresh) - len(up) - len(down),
            "down_count": len(down),
            "up_breadth_pct": breadth,
            "history_points": len(history),
        },
        "metrics": metrics,
        "history": history,
        "sources": source_status,
        "quality": {
            "status": "ready" if metrics and not errors else "partial",
            "errors": errors,
        },
    }
    write_json(latest_path, payload)
    print(json.dumps(payload["summary"] | payload["quality"], ensure_ascii=False))
    return 0 if metrics else 1


if __name__ == "__main__":
    raise SystemExit(main())
