#!/usr/bin/env python3
"""Build the GitHub Pages site and versioned public JSON endpoints."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def render_page(template: str, root_prefix: str, asset_prefix: str) -> str:
    return (
        template.replace("{{ROOT_PREFIX}}", root_prefix)
        .replace("{{ASSET_PREFIX}}", asset_prefix)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    output = root / "_site"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    payload = json.loads((root / "data" / "dashboard_v2.json").read_text(encoding="utf-8"))
    macro_validation_path = root / "data" / "macro_source_validation.json"
    macro_validation = json.loads(macro_validation_path.read_text(encoding="utf-8")) if macro_validation_path.exists() else {"meta": {"status": "missing"}, "checks": []}
    macro_history_path = root / "data" / "macro_public_history.jsonl"
    macro_history = [json.loads(line) for line in macro_history_path.read_text(encoding="utf-8").splitlines() if line.strip()] if macro_history_path.exists() else []
    web = root / "web"
    template = (web / "index.html").read_text(encoding="utf-8")
    (output / "index.html").write_text(render_page(template, "./", ""), encoding="utf-8")
    storage_root = output / "storage"
    storage_root.mkdir()
    (storage_root / "index.html").write_text(
        render_page(template, "../", "../"), encoding="utf-8"
    )
    shutil.copy2(web / "styles.css", output / "styles.css")
    shutil.copy2(web / "app.js", output / "app.js")
    (output / ".nojekyll").write_text("", encoding="utf-8")

    meta = payload["meta"]
    endpoints = {
        "dashboard.json": payload,
        "overview.json": {
            "meta": meta,
            "overview": payload["overview"],
            "health": payload["health"],
        },
        "macro-indicators.json": {
            "meta": meta,
            "rows": payload["overview"]["macro_indicators"],
            "health": {
                "status": payload["health"].get("macro_source_status"),
                "last_collected_at": payload["health"].get("macro_last_collected_at"),
                "errors": payload["health"].get("macro_source_errors", []),
            },
        },
        "macro-source-validation.json": macro_validation,
        "macro-history.json": {
            "meta": {
                "generated_at": meta["generated_at"],
                "grain": "one row per metric_id and source observation period",
                "rows": len(macro_history),
                "baseline": "Wind workbook CDF through each metric's baseline_as_of; this endpoint contains public-source extensions only",
            },
            "rows": macro_history,
        },
        "ai-compute.json": {
            "meta": meta,
            **payload["ai_compute"],
        },
        "gpu-rental.json": {
            "meta": meta,
            **payload["gpu_rental"],
        },
        "storage.json": {
            "meta": meta,
            **payload["storage"],
        },
        "storage-prices.json": {
            "meta": meta,
            "cycle": payload["storage"]["cycle"],
            "summary": payload["storage"]["price_summary"],
            "rows": payload["storage"]["price_metrics"],
            "history": payload["storage"]["price_history"],
            "quality": payload["storage"]["price_quality"],
            "methodology": payload["storage"]["price_meta"],
        },
        "market-cycle.json": {
            "meta": meta,
            **payload["market_cycle"],
        },
        "news.json": {
            "meta": meta,
            **payload["news"],
        },
        "health.json": payload["health"],
        "metrics.json": {
            "meta": meta,
            "rows": payload["normalized_metrics"],
        },
        # Legacy endpoints retained for existing users.
        "pricing.json": {"meta": meta, "rows": payload["pricing"]},
        "business.json": {"meta": meta, "rows": payload["business"]},
        "tokens.json": {"meta": meta, "rows": payload["tokens"]},
        "sources.json": {"meta": meta, "rows": payload["sources"]},
        "signals.json": {"meta": meta, "rows": payload["signals"]},
        "history.json": {"meta": meta, "series": payload["history_series"]},
    }
    api_root = output / "api"
    for filename, value in endpoints.items():
        write_json(api_root / filename, value)

    endpoint_docs = [
        {"path": f"./{name}", "description": description}
        for name, description in {
            "dashboard.json": "完整双Tab兼容快照",
            "overview.json": "阶段判断、八大总量指标与产业验证",
            "ai-compute.json": "Token、API价格、ARR和CSP Capex",
            "gpu-rental.json": "标准化单卡GPU租赁价格",
            "storage.json": "存储周期、公开价格指标、事件与行情",
            "storage-prices.json": "DRAM、NAND、GDDR与SSD公开报价及自建历史",
            "market-cycle.json": "阶段得分、阈值和证据覆盖",
            "news.json": "经过去重和噪声过滤的发现队列",
            "health.json": "来源成功、陈旧数据和部署状态",
            "metrics.json": "统一字段的标准化指标明细",
            "pricing.json": "兼容：API Token价格",
            "business.json": "兼容：ARR和收入披露",
            "tokens.json": "兼容：Token披露",
            "sources.json": "兼容：来源状态",
            "signals.json": "兼容：AI新闻待复核池",
            "history.json": "兼容：价格历史",
        }.items()
    ]
    endpoint_docs.append({
        "path": "./macro-indicators.json",
        "description": "总量八大指标最新观测、真实数据日期、频率与公开来源",
    })
    endpoint_docs.append({
        "path": "./macro-history.json",
        "description": "Wind 基准日之后逐指标、逐观测期去重的公开增量历史",
    })
    endpoint_docs.append({
        "path": "./macro-source-validation.json",
        "description": "公开源与 Wind 底稿对应期逐点回测结果（不包含 Wind 历史原始序列）",
    })
    write_json(
        api_root / "index.json",
        {
            "name": "Zheshang AI Compute & Storage Monitor API",
            "version": 2.1,
            "snapshot_at": meta["generated_at"],
            "refresh_schedule": "Every 6 hours / 01:30, 07:30, 13:30, 19:30 Asia/Shanghai",
            "schema": {
                "normalized_metric": [
                    "metric_id", "value", "unit", "currency", "region", "period",
                    "source_name", "source_url", "source_tier",
                    "evidence_status", "collected_at", "note",
                ]
            },
            "endpoints": endpoint_docs,
            "note": "GitHub Pages静态JSON代表最近一次成功Actions生成的公开快照。",
        },
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
