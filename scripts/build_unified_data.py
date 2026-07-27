#!/usr/bin/env python3
"""Build the public AI-compute and storage dashboard data model."""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
TITLE = "【创新构建总量八大指标+产业双维体系,AI牛市目前处于哪个阶段？】全球大模型和Token跟踪"
CONTACT = "请联系浙商何佳烨/孙一峰/赵乾凯/马莉"


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


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CN_TZ)
        return parsed.astimezone(CN_TZ)
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=CN_TZ)
        except ValueError:
            return None


def freshness(period: str | None, allowed_days: int, now: datetime) -> dict[str, Any]:
    observed = parse_date(period)
    if observed is None:
        return {"status": "unknown", "age_days": None, "allowed_days": allowed_days}
    age = max(0, (now - observed).days)
    return {
        "status": "fresh" if age <= allowed_days else "stale",
        "age_days": age,
        "allowed_days": allowed_days,
    }


def mean_score(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return round(statistics.fmean(values), 1) if values else None


def determine_stage(macro_score: float | None, industry_score: float | None) -> dict[str, Any]:
    if macro_score is None or industry_score is None:
        return {
            "stage_short": "证据不足",
            "stage_label": "保留最近研究基准，等待足够公开指标验证",
            "rationale": "总量或产业证据覆盖未达到最低门槛。",
        }
    if macro_score >= 65 and industry_score >= 55:
        return {
            "stage_short": "共振拥挤",
            "stage_label": "上游紧缺涨价与中下游商业闭环共振期",
            "rationale": "产业兑现仍强，但总量估值、杠杆与风险偏好已处较高分位。",
        }
    if macro_score >= 65 and industry_score < 55:
        return {
            "stage_short": "泡沫兑现风险",
            "stage_label": "估值与拥挤度领先产业兑现",
            "rationale": "总量风险偏高而产业验证不足，需要警惕估值消化。",
        }
    if macro_score < 40 and industry_score < 45:
        return {
            "stage_short": "起步验证",
            "stage_label": "产业需求与商业化仍处验证期",
            "rationale": "总量环境尚未拥挤，产业信号仍需更多订单与收入验证。",
        }
    if industry_score >= 45:
        return {
            "stage_short": "主升扩散",
            "stage_label": "产业景气向商业化和应用端扩散",
            "rationale": "产业证据改善且总量拥挤度尚未进入高风险区。",
        }
    return {
        "stage_short": "出清再平衡",
        "stage_label": "产业动能回落，进入出清与再平衡观察期",
        "rationale": "产业验证偏弱，总量环境也未形成新的上行动能。",
    }


def normalized_metric(
    metric_id: str,
    value: Any,
    unit: str,
    region: str,
    period: str,
    source_name: str,
    source_url: str,
    source_tier: int,
    evidence_status: str,
    collected_at: str,
    note: str = "",
    currency: str | None = None,
) -> dict[str, Any]:
    return {
        "metric_id": metric_id,
        "value": value,
        "unit": unit,
        "currency": currency,
        "region": region,
        "period": period,
        "source_name": source_name,
        "source_url": source_url,
        "source_tier": source_tier,
        "evidence_status": evidence_status,
        "collected_at": collected_at,
        "note": note,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    now = datetime.now(CN_TZ)
    generated_at = now.isoformat(timespec="seconds")

    ai = load_json(root / "data" / "dashboard_api.json", {})
    framework = load_json(root / "data" / "framework_metrics.json", {})
    storage_prices = load_json(root / "data" / "storage_prices_latest.json", {})
    storage = load_json(root / "存储日报" / "output" / "latest.json", {})
    if not storage:
        storage = {
            "meta": {
                "generated_at": None,
                "delivery": "no-current-storage-snapshot",
                "methodology": "存储采集未成功，页面保留正式价格信号和空状态。",
            },
            "quality": {
                "status": "partial",
                "selected_events": 0,
                "market_quotes": 0,
                "source_errors": 1,
                "errors": ["本轮未生成存储日报快照"],
            },
            "summary": {
                "product_counts": {},
                "layer_counts": {},
                "event_counts": {},
                "evidence_counts": {},
            },
            "events": [],
            "market": [],
        }

    fx = 7.2
    fx_note = ai.get("meta", {}).get("fx_note", "")
    for gpu in framework.get("gpu_rental", []):
        if gpu.get("usd_per_gpu_hour") is None and gpu.get("cny_per_gpu_hour") is not None:
            gpu["usd_per_gpu_hour"] = round(float(gpu["cny_per_gpu_hour"]) / fx, 4)
        if gpu.get("cny_per_gpu_hour") is None and gpu.get("usd_per_gpu_hour") is not None:
            gpu["cny_per_gpu_hour"] = round(float(gpu["usd_per_gpu_hour"]) * fx, 2)
        gpu["freshness"] = freshness(gpu.get("observed_at"), 45, now)

    macro = framework.get("macro_indicators", [])
    valid_macro = []
    for row in macro:
        row["freshness"] = freshness(
            row.get("period"), int(row.get("freshness_days", 60)), now
        )
        if row.get("risk_percentile") is not None and row["freshness"]["status"] != "unknown":
            valid_macro.append(row)

    industry = framework.get("industry_signals", [])
    valid_industry = [row for row in industry if row.get("score") is not None]
    macro_score = mean_score(valid_macro, "risk_percentile") if len(valid_macro) >= 6 else None
    industry_score = mean_score(valid_industry, "score") if len(valid_industry) >= 4 else None
    stage = determine_stage(macro_score, industry_score)
    stage.update({
        "macro_score": macro_score,
        "industry_score": industry_score,
        "macro_coverage": f"{len(valid_macro)}/8",
        "industry_coverage": f"{len(valid_industry)}/6",
        "minimum_coverage": {"macro": 6, "industry": 4},
        "thresholds": {
            "early": "总量<40 且产业<45",
            "expansion": "产业≥45 且总量<65",
            "resonance": "总量≥65 且产业≥55",
            "bubble_risk": "总量≥65 且产业<55",
        },
        "research_baseline": framework.get("research_baseline", {}),
    })

    storage_signals = framework.get("storage_price_signals", [])
    upward = sum(
        row.get("direction") in {"上行", "偏紧", "改善", "上修"}
        for row in storage_signals
    )
    price_summary = storage_prices.get("summary", {})
    price_breadth = price_summary.get("up_breadth_pct")
    direction_score = upward / max(len(storage_signals), 1) * 100
    cycle_score = (
        round(float(price_breadth) * 0.6 + direction_score * 0.4, 1)
        if price_breadth is not None
        else round(direction_score, 1)
    )
    storage_cycle = {
        "label": "上行偏紧" if cycle_score >= 60 else ("下行去库" if cycle_score < 35 else "分化观察"),
        "score": cycle_score,
        "signal_count": len(storage_signals),
        "price_metric_count": price_summary.get("metric_count", 0),
        "fresh_price_count": price_summary.get("fresh_count", 0),
        "price_breadth_pct": price_breadth,
        "event_count": len(storage.get("events", [])),
        "market_count": len(storage.get("market", [])),
        "method": "60%公开价格上涨广度 + 40%供需方向；陈旧报价不参与价格广度。",
    }

    normalized: list[dict[str, Any]] = []
    for row in macro:
        normalized.append(normalized_metric(
            row["metric_id"], row.get("value"), row.get("unit", ""),
            "全球", row.get("period", ""), row.get("source_name", ""),
            row.get("source_url", ""), int(row.get("source_tier", 3)),
            row.get("evidence_status", "unknown"), generated_at, row.get("note", ""),
        ))
    for row in framework.get("gpu_rental", []):
        normalized.append(normalized_metric(
            row["metric_id"], row.get("usd_per_gpu_hour"), "USD/GPU·小时",
            row.get("region", ""), row.get("observed_at", ""), row.get("provider", ""),
            row.get("source_url", ""), int(row.get("source_tier", 3)),
            row.get("evidence_status", "unknown"), generated_at, row.get("note", ""),
            "USD",
        ))
    for row in storage_signals:
        normalized.append(normalized_metric(
            row["metric_id"], row.get("change_range"), "方向/区间", "全球",
            row.get("period", ""), row.get("source_name", ""),
            row.get("source_url", ""), int(row.get("source_tier", 3)),
            row.get("evidence_status", "unknown"), generated_at, row.get("note", ""),
        ))
    for row in storage_prices.get("metrics", []):
        normalized.append(normalized_metric(
            row["metric_id"], row.get("price"), row.get("unit", "USD/官网报价单位"),
            "全球", row.get("observed_at", ""), row.get("source_name", ""),
            row.get("source_url", ""), int(row.get("source_tier", 2)),
            row.get("evidence_status", "public_snapshot"), generated_at, row.get("note", ""),
            row.get("currency", "USD"),
        ))

    ai_sources = ai.get("sources", [])
    ai_success = sum(row.get("status") == "ok" for row in ai_sources)
    storage_quality = storage.get("quality", {})
    health = {
        "status": "ok" if ai_success and storage_quality.get("status") != "blocked" else "partial",
        "generated_at": generated_at,
        "ai_sources_ok": ai_success,
        "ai_sources_total": len(ai_sources),
        "storage_status": storage_quality.get("status", "unknown"),
        "storage_source_errors": storage_quality.get("source_errors", 0),
        "storage_price_status": storage_prices.get("quality", {}).get("status", "unknown"),
        "storage_price_errors": len(storage_prices.get("quality", {}).get("errors", [])),
        "stale_macro_metrics": [
            row["metric_id"] for row in macro if row.get("freshness", {}).get("status") == "stale"
        ],
        "delivery": "github-pages-static-json",
    }

    payload = {
        "meta": {
            "title": TITLE,
            "contact": CONTACT,
            "generated_at": generated_at,
            "metrics_as_of": framework.get("as_of", ai.get("meta", {}).get("metrics_as_of")),
            "schedule": "每日 07:30（Asia/Shanghai）",
            "timezone": "Asia/Shanghai",
            "fx_note": fx_note or "跨币种展示按 1 USD = 7.2 CNY，仅用于横向比较。",
            "public_policy": "只发布可公开引用的数据、方向和有限快照；不上传Wind、付费TrendForce历史或内部底稿。",
        },
        "kpis": ai.get("kpis", {}),
        "pricing": ai.get("pricing", []),
        "business": ai.get("business", []),
        "tokens": ai.get("tokens", []),
        "sources": ai_sources,
        "signals": ai.get("signals", []),
        "history_series": ai.get("history_series", {}),
        "methodology": ai.get("methodology", {}),
        "overview": {
            "stage": stage,
            "macro_indicators": macro,
            "industry_signals": industry,
        },
        "ai_compute": {
            "pricing": ai.get("pricing", []),
            "business": ai.get("business", []),
            "tokens": ai.get("tokens", []),
            "csp_capex": framework.get("csp_capex", []),
            "industry_signals": industry,
            "history_series": ai.get("history_series", {}),
        },
        "gpu_rental": {
            "meta": {
                "unit": "单卡每小时",
                "fx": fx,
                "comparability": "按需、合约和竞价价格分层；不把整机价格当作单卡价格。",
            },
            "rows": framework.get("gpu_rental", []),
        },
        "storage": {
            "cycle": storage_cycle,
            "price_signals": storage_signals,
            "price_metrics": storage_prices.get("metrics", []),
            "price_history": storage_prices.get("history", []),
            "price_summary": price_summary,
            "price_quality": storage_prices.get("quality", {}),
            "price_meta": storage_prices.get("meta", {}),
            "daily": storage,
            "chain": [
                "终端需求", "bit需求", "库存", "有效供给", "价格",
                "收入/毛利", "Capex", "设备材料订单",
            ],
        },
        "market_cycle": stage,
        "news": {
            "storage": storage.get("events", []),
            "ai_review_queue": ai.get("signals", [])[:40],
        },
        "health": health,
        "normalized_metrics": normalized,
    }

    write_json(root / "data" / "dashboard_v2.json", payload)
    history_path = root / "data" / "unified_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_row = {
        "run_at": generated_at,
        "macro_score": macro_score,
        "industry_score": industry_score,
        "stage": stage["stage_short"],
        "gpu": [
            {
                "metric_id": row["metric_id"],
                "usd_per_gpu_hour": row.get("usd_per_gpu_hour"),
            }
            for row in framework.get("gpu_rental", [])
        ],
        "storage_cycle": storage_cycle["label"],
        "storage_price_breadth_pct": price_breadth,
        "storage_prices": [
            {
                "metric_id": row.get("metric_id"),
                "price": row.get("price"),
                "change_pct": row.get("change_pct"),
            }
            for row in storage_prices.get("metrics", [])
        ],
        "health": health["status"],
    }
    existing = []
    if history_path.exists():
        existing = [line for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    existing.append(json.dumps(history_row, ensure_ascii=False))
    history_path.write_text("\n".join(existing[-400:]) + "\n", encoding="utf-8")
    print(root / "data" / "dashboard_v2.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
