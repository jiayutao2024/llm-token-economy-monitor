#!/usr/bin/env python3
"""Storage-industry news monitor: collect, classify, map implications, and report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable


CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
LOGS = ROOT / "logs"
INBOX = ROOT / "inbox"
USER_AGENT = "Mozilla/5.0 StorageIntelligenceBot/1.0 (local research monitor)"
HARD_NOISE_TERMS = (
    "giveaway", "win a ", "deal will save", "drops to", "discount", "coupon",
    "best ssd", "review", "to watch this week", "etf stocks", "amazon deal",
    "ram deal", "perfect starting point", "dividend", "valuation", "relief rally",
    "stocks to watch", "what it means for", "build a new pc",
    "优惠", "促销", "赠品", "抽奖", "评测", "值得买", "概念股推荐", "etf",
    "股价一夜", "融资客", "名单", "估值可能", "股票反弹", "装机",
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dirs() -> None:
    for path in (DATA, OUTPUT, LOGS, INBOX):
        path.mkdir(parents=True, exist_ok=True)


def phrase_match(text: str, phrase: str) -> bool:
    p = phrase.lower().strip()
    if not p:
        return False
    if re.fullmatch(r"[a-z0-9.+-]+", p):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", text))
    return p in text


def matches(text: str, mapping: dict[str, list[str]]) -> list[str]:
    return [label for label, terms in mapping.items() if any(phrase_match(text, t) for t in terms)]


def parse_date(raw: str | None, now: datetime) -> datetime:
    if not raw:
        return now
    raw = raw.strip()
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(CN_TZ)
    except (TypeError, ValueError, OverflowError):
        pass
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(CN_TZ)
        except ValueError:
            continue
    return now


def text_of(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def child_by_local_name(node: ET.Element, names: Iterable[str]) -> ET.Element | None:
    wanted = set(names)
    for child in list(node):
        if child.tag.rsplit("}", 1)[-1] in wanted:
            return child
    return None


def parse_feed(payload: bytes, source: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    entries = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] in ("item", "entry")]
    results: list[dict[str, Any]] = []
    for entry in entries:
        title = text_of(child_by_local_name(entry, ("title",)))
        if not title:
            continue
        link_node = child_by_local_name(entry, ("link",))
        link = ""
        if link_node is not None:
            link = (link_node.attrib.get("href") or text_of(link_node)).strip()
        description = text_of(child_by_local_name(entry, ("description", "summary", "content")))
        date_raw = text_of(child_by_local_name(entry, ("pubDate", "published", "updated", "date")))
        publisher = text_of(child_by_local_name(entry, ("source", "author")))
        results.append({
            "title": title,
            "url": link,
            "summary_raw": html.unescape(re.sub(r"<[^>]+>", " ", description)),
            "published_at": parse_date(date_raw, now).isoformat(),
            "publisher": publisher or source["name"],
            "feed_id": source["id"],
            "feed_name": source["name"],
            "feed_tier": int(source.get("tier", 3)),
        })
    return results


def feed_url(source: dict[str, Any]) -> str:
    if source["type"] == "rss":
        return source["url"]
    if source["type"] == "google_news_query":
        lang = source.get("language", "en-US")
        region = source.get("region", "US")
        return "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": source["query"], "hl": lang, "gl": region, "ceid": f"{region}:{lang.split('-')[0]}"
        })
    if source["type"] == "bing_news_query":
        return "https://www.bing.com/news/search?" + urllib.parse.urlencode({
            "q": source["query"], "format": "rss", "setlang": source.get("language", "zh-CN")
        })
    raise ValueError(f"Unsupported feed type: {source['type']}")


def fetch(source: dict[str, Any], now: datetime, max_items: int) -> tuple[list[dict[str, Any]], str | None]:
    url = feed_url(source)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read(5_000_000)
        return parse_feed(payload, source, now)[:max_items], None
    except Exception as exc:  # feed failures must not stop the full run
        return [], f"{source['id']}: {type(exc).__name__}: {exc}"


def domain_tier(url: str, default: int, domain_tiers: dict[str, int]) -> int:
    domain = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    for known, tier in domain_tiers.items():
        if domain == known or domain.endswith("." + known):
            return int(tier)
    return default


def normalized_title(title: str) -> str:
    value = re.sub(r"\s+[-–—|]\s+[^-–—|]{2,60}$", "", title.lower())
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    return " ".join(value.split())


def entity_hits(text: str, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entities if any(phrase_match(text, alias) for alias in e["aliases"])]


def infer_region(entity_rows: list[dict[str, Any]], text: str) -> str:
    regions = {e["region"] for e in entity_rows}
    if len(regions) == 1:
        return next(iter(regions))
    if any(term in text for term in ("中国", "china", "a股", "上交所", "深交所")):
        return "China"
    return "Global" if len(regions) > 1 else "Unclear"


def evidence_stage(text: str, stages: dict[str, list[str]]) -> str:
    # Ordered so the strongest explicit evidence wins.
    hits = [stage for stage, terms in stages.items() if any(phrase_match(text, t) for t in terms)]
    return max(hits, key=lambda x: int(x.split("_", 1)[0])) if hits else "0_Unspecified"


def impact_directions(event_types: list[str]) -> list[str]:
    mapping = {
        "Order/Contract": ["需求可见度↑", "供应商份额/收入预期↑", "客户供货风险↓"],
        "Qualification/Sample": ["技术成功概率↑", "订单仍待确认"],
        "Mass Production/Shipment": ["有效供给↑", "收入兑现度↑", "潜在价格压力↑"],
        "Capacity/Capex": ["中期供给↑", "设备材料订单↑", "远期价格压力↑"],
        "Supply Cut/Disruption": ["有效供给↓", "价格预期↑", "下游成本↑"],
        "Price": ["原厂ASP/毛利预期↑", "下游成本压力↑"],
        "Inventory/Utilization": ["周期位置变化", "补库/去库信号"],
        "Earnings/Guidance": ["基本面预期重估", "量价利润验证"],
        "Technology/Roadmap": ["技术竞争力预期变化", "量产仍待验证"],
        "Policy/Trade": ["可获得性/国产替代变化", "供应链重构"],
        "M&A/IPO/Financing": ["资本与整合预期变化", "估值锚变化"],
        "Demand/Shipment": ["终端bit需求变化", "库存与价格预期变化"],
    }
    values: list[str] = []
    for event in event_types:
        for value in mapping.get(event, []):
            if value not in values:
                values.append(value)
    return values[:6]


def infer_exposure(product_hits: list[str], matched_entities: list[dict[str, Any]], all_entities: list[dict[str, Any]]) -> list[str]:
    names = [e["name"] for e in matched_entities]
    if names:
        return names[:8]
    product_roles = set(product_hits)
    for entity in all_entities:
        if product_roles.intersection(entity.get("roles", [])):
            names.append(entity["name"])
    return names[:8]


def analyze(item: dict[str, Any], ontology: dict[str, Any], entities: list[dict[str, Any]], domain_tiers: dict[str, int], now: datetime) -> dict[str, Any]:
    raw_text = f"{item['title']} {item.get('summary_raw', '')}".lower()
    excluded = (
        any(phrase_match(raw_text, term) for term in ontology.get("exclusion_terms", []))
        or any(term in raw_text for term in HARD_NOISE_TERMS)
        or ("simpson" in raw_text and "ssd" in raw_text)
    )
    product_hits = matches(raw_text, ontology["products"])
    layer_hits = matches(raw_text, ontology["chain_layers"])
    events = matches(raw_text, ontology["event_types"])
    found_entities = entity_hits(raw_text, entities)
    stage = evidence_stage(raw_text, ontology["evidence_stages"])
    tier = domain_tier(item.get("url", ""), item["feed_tier"], domain_tiers)
    published = datetime.fromisoformat(item["published_at"])
    age_hours = max(0.0, (now - published).total_seconds() / 3600)
    recency = max(0, 10 - int(age_hours / 12))
    score = min(100, len(product_hits) * 7 + len(events) * 5 + len(found_entities) * 3 + len(layer_hits) * 2 + {1: 10, 2: 7, 3: 3}.get(tier, 1) + recency)
    logic = ontology["logic_templates"].get(events[0], {}) if events else {}
    item.update({
        "id": hashlib.sha256(normalized_title(item["title"]).encode("utf-8")).hexdigest()[:20],
        "normalized_title": normalized_title(item["title"]),
        "products": product_hits,
        "chain_layers": layer_hits,
        "event_types": events,
        "evidence_stage": stage,
        "entities": [e["name"] for e in found_entities],
        "tickers": sorted({t for e in found_entities for t in e.get("tickers", [])}),
        "region": infer_region(found_entities, raw_text),
        "source_tier": tier,
        "relevance_score": score,
        "excluded_as_retail_noise": excluded,
        "impact_directions": impact_directions(events),
        "first_order_effect": logic.get("first_order_effect", "需先确认事件性质，再映射至需求、供给、价格、收入与资本开支。"),
        "second_order_effect": logic.get("second_order_effect", "关注产业链产能再配置、库存和价格的二阶反馈。"),
        "verification": logic.get("verification", "回到一手来源，确认数量、价格、时点、客户、产能与收入兑现。"),
        "exposed_entities": infer_exposure(product_hits, found_entities, entities),
        "collected_at": now.isoformat(),
    })
    return item


def is_retail_noise(item: dict[str, Any], ontology: dict[str, Any]) -> bool:
    raw_text = f"{item.get('title', '')} {item.get('title_zh', '')} {item.get('summary_raw', '')}".lower()
    return (
        any(phrase_match(raw_text, term) for term in ontology.get("exclusion_terms", []))
        or any(term in raw_text for term in HARD_NOISE_TERMS)
    )


def topic_tokens(title: str) -> set[str]:
    stop = {
        "the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with", "as", "at", "by",
        "new", "latest", "news", "report", "says", "said", "from", "is", "are", "will", "could",
        "公司", "宣布", "消息", "报道", "今日", "最新", "相关", "进行", "以及", "对于", "已经"
    }
    clean = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", normalized_title(title))
    return {token for token in clean.split() if len(token) > 1 and token not in stop}


def same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    title_ratio = SequenceMatcher(None, a["normalized_title"], b["normalized_title"]).ratio()
    if title_ratio >= 0.72:
        return True
    products_overlap = bool(set(a["products"]) & set(b["products"]))
    entities_overlap = bool(set(a["entities"]) & set(b["entities"]))
    events_overlap = bool(set(a["event_types"]) & set(b["event_types"]))
    ta, tb = topic_tokens(a["title"]), topic_tokens(b["title"])
    union = ta | tb
    jaccard = len(ta & tb) / len(union) if union else 0
    tech_a = set(re.findall(r"(?:hbm|gddr|lpddr|ddr|ufs)\s*\d+[a-z]*|\d+\s*layer", a["title"].lower()))
    tech_b = set(re.findall(r"(?:hbm|gddr|lpddr|ddr|ufs)\s*\d+[a-z]*|\d+\s*layer", b["title"].lower()))
    if products_overlap and entities_overlap and tech_a & tech_b:
        return True
    if products_overlap and events_overlap and jaccard >= 0.30:
        return True
    return products_overlap and entities_overlap and (events_overlap or jaccard >= 0.34) and jaccard >= 0.22


def cluster_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    ordered = sorted(items, key=lambda x: (x["relevance_score"], -x["source_tier"], x["published_at"]), reverse=True)
    for item in ordered:
        target = next((group for group in groups if same_event(item, group[0])), None)
        if target is None:
            groups.append([item])
        else:
            target.append(item)
    events: list[dict[str, Any]] = []
    for group in groups:
        representative = dict(sorted(group, key=lambda x: (x["relevance_score"], -x["source_tier"]), reverse=True)[0])
        representative["merged_count"] = len(group)
        representative["related_links"] = [
            {"title": x["title"], "url": x["url"], "publisher": x.get("publisher") or x["feed_name"]}
            for x in sorted(group, key=lambda x: (x["source_tier"], -x["relevance_score"]))[:5]
        ]
        representative["publishers"] = sorted({x.get("publisher") or x["feed_name"] for x in group})
        representative["relevance_score"] = min(100, representative["relevance_score"] + min(8, (len(group) - 1) * 2))
        events.append(representative)
    return sorted(events, key=lambda x: (x["relevance_score"], x["published_at"]), reverse=True)


def select_balanced_events(events: list[dict[str, Any]], target: int = 14, maximum: int = 15) -> list[dict[str, Any]]:
    target = min(maximum, max(10, target))
    domestic = [x for x in events if x["region"] == "China"]
    international = [x for x in events if x["region"] != "China"]
    chosen: list[dict[str, Any]] = []
    signature_counts: Counter[tuple[str, str]] = Counter()

    def try_add(item: dict[str, Any]) -> None:
        title_key = f"{item.get('title', '')} {item.get('title_zh', '')}".lower()
        if "cxmt" in title_key or "长鑫" in title_key:
            entity = "CXMT"
        else:
            entity = (item.get("entities") or ["行业"])[0]
        event = (item.get("event_types") or ["行业动态"])[0]
        signature = (entity, event)
        if signature_counts[signature] >= 2:
            return
        if item["id"] in {row["id"] for row in chosen}:
            return
        chosen.append(item)
        signature_counts[signature] += 1

    for item in domestic:
        if len([x for x in chosen if x["region"] == "China"]) >= 5:
            break
        try_add(item)
    for item in international:
        if len([x for x in chosen if x["region"] != "China"]) >= 7:
            break
        try_add(item)
    for item in events:
        if len(chosen) >= target:
            break
        try_add(item)
    return sorted(chosen[:maximum], key=lambda x: (x["region"] != "China", -x["relevance_score"]))


def contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def load_translation_cache() -> dict[str, str]:
    path = DATA / "translations.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_translation_cache(cache: dict[str, str]) -> None:
    (DATA / "translations.json").write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def polish_translation(value: str) -> str:
    glossary = {
        "国产模具": "中国制造颗粒",
        "中国制造的模具": "中国制造颗粒",
        "RAM 紧缩": "DRAM供应紧张",
        "Memory Edge": "内存优势",
        "高带宽存储器": "高带宽内存",
        "SK Hynix": "SK海力士",
    }
    for source_term, preferred_term in glossary.items():
        value = value.replace(source_term, preferred_term)
    return value


def translate_to_chinese(value: str, cache: dict[str, str]) -> str:
    value = " ".join((value or "").split()).strip()
    if not value or contains_chinese(value):
        return value
    key = hashlib.sha256(value.encode("utf-8")).hexdigest()
    if key in cache:
        return polish_translation(cache[key])
    query = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": value[:1200]})
    request = urllib.request.Request("https://translate.googleapis.com/translate_a/single?" + query, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.load(response)
        translated = "".join(part[0] for part in payload[0] if part and part[0]).strip()
        translated = polish_translation(translated)
        cache[key] = translated or value
    except Exception:
        cache[key] = value
    return cache[key]


def sentiment_of(item: dict[str, Any]) -> str:
    text = f"{item['title']} {item.get('summary_raw', '')}".lower()
    negative_terms = ("delay", "decline", "drop", "cut forecast", "loss", "shortage", "reject", "weak", "slump", "ban", "sanction", "延期", "下滑", "亏损", "拒绝", "疲软", "制裁", "停产")
    positive_terms = ("order", "agreement", "growth", "increase", "record", "ramp", "mass production", "qualified", "breakthrough", "investment", "funding", "订单", "增长", "创纪录", "量产", "验证通过", "突破", "投资", "增资")
    if "Supply Cut/Disruption" in item["event_types"] or ("Price" in item["event_types"] and not any(x in text for x in negative_terms)):
        return "分化"
    if any(x in text for x in negative_terms):
        return "负面"
    if any(x in text for x in positive_terms) or set(item["event_types"]) & {"Order/Contract", "Mass Production/Shipment", "Demand/Shipment", "Technology/Roadmap"}:
        return "积极"
    return "中性"


def chain_stage(item: dict[str, Any]) -> str:
    layers = set(item["chain_layers"])
    products = set(item["products"])
    roles = {
        "设备材料": {"Equipment", "Materials"},
        "原厂制造": {"Memory Design/Manufacturing"},
        "封装测试/主控": {"Packaging/Test"},
        "模组/渠道": {"Module/Channel"},
        "系统/终端需求": {"System/OEM", "End Demand"},
    }
    for label, candidates in roles.items():
        if layers & candidates:
            return label
    if products & {"HBM", "DRAM", "NAND", "NOR/EEPROM"}:
        return "原厂制造"
    if products & {"Controller"}:
        return "封装测试/主控"
    if products & {"SSD", "Managed Flash"}:
        return "模组/渠道"
    if products & {"HDD", "Tape/Archive", "Optical/MED"}:
        return "系统/终端需求"
    return "产业链综合"


def entity_name_zh(value: str) -> str:
    mapping = {
        "Samsung Electronics": "三星电子", "SK hynix": "SK海力士", "Micron": "美光科技",
        "Kioxia": "铠侠", "Sandisk": "闪迪", "YMTC": "长江存储", "CXMT": "长鑫存储",
        "Fujian Jinhua": "福建晋华", "NVIDIA": "英伟达", "GigaDevice": "兆易创新",
        "Montage Technology": "澜起科技", "Longsys": "江波龙", "BIWIN Storage": "佰维存储",
        "Dosilicon": "东芯股份", "Ingenic": "北京君正", "Deyiwei": "德明利",
        "Western Digital": "西部数据", "Seagate": "希捷科技", "Microsoft": "微软",
        "Amazon": "亚马逊", "Google": "谷歌", "Broadcom": "博通"
    }
    return mapping.get(value, value)


def event_name_zh(value: str) -> str:
    mapping = {
        "Order/Contract": "订单/供货协议", "Qualification/Sample": "送样/客户认证",
        "Mass Production/Shipment": "量产/出货", "Capacity/Capex": "扩产/资本开支",
        "Supply Cut/Disruption": "减产/供应扰动", "Price": "价格变化",
        "Inventory/Utilization": "库存/稼动率", "Earnings/Guidance": "业绩/指引",
        "Technology/Roadmap": "技术/路线图", "Policy/Trade": "政策/贸易限制",
        "M&A/IPO/Financing": "资本运作/并购IPO", "Demand/Shipment": "终端需求/出货"
    }
    return mapping.get(value, "行业动态")


def evidence_name_zh(value: str) -> str:
    mapping = {
        "0_Unspecified": "未明确", "1_Rumor": "传闻", "2_Announcement": "官方发布",
        "3_Sample": "送样", "4_Qualification": "客户验证", "5_Contract": "合同/订单",
        "6_MassProduction": "量产", "7_ShipmentRevenue": "出货/收入"
    }
    return mapping.get(value, value)


def enrich_for_report(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cache = load_translation_cache()
    for item in items:
        clean_title = re.sub(r"\s+[-–—|]\s+[^-–—|]{2,80}$", "", item["title"]).strip()
        item["title_zh"] = translate_to_chinese(clean_title, cache)
        raw_summary = html.unescape(item.get("summary_raw", "")).replace("\xa0", " ")
        raw_summary = re.sub(r"\s+", " ", raw_summary).strip()
        same_as_title = normalized_title(raw_summary) == normalized_title(item["title"]) or normalized_title(raw_summary).startswith(normalized_title(clean_title))
        if raw_summary and not same_as_title:
            translated_summary = translate_to_chinese(raw_summary[:700], cache)
            item["brief_zh"] = translated_summary[:220] + ("…" if len(translated_summary) > 220 else "")
        else:
            products = "、".join(item["products"][:3]) or "存储产品"
            event = "、".join(event_name_zh(x) for x in item["event_types"][:2]) or "行业动态"
            item["brief_zh"] = f"报道显示：{item['title_zh']}。事件涉及{products}，属于{event}，主要影响{chain_stage(item)}环节。"
        item["sentiment"] = sentiment_of(item)
        item["stage_zh"] = chain_stage(item)
        item["geo_tag"] = "国内" if item["region"] == "China" else "国外"
        item["exposed_entities_zh"] = [entity_name_zh(x) for x in item["exposed_entities"]]
        item["judgement"] = f"{item['first_order_effect']} {item['second_order_effect']} 关键验证：{item['verification']}"
    save_translation_cache(cache)
    return items


def load_history(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                records[row["id"]] = row
            except (json.JSONDecodeError, KeyError):
                continue
    return records


def save_history(path: Path, records: dict[str, dict[str, Any]]) -> None:
    rows = sorted(records.values(), key=lambda x: x.get("published_at", ""), reverse=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(path.parent), suffix=".tmp") as f:
        temp_name = f.name
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp_name, path)


def load_csv_if_present(filename: str) -> list[dict[str, str]]:
    path = INBOX / filename
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def fetch_market_quote(entry: dict[str, str], group: str) -> tuple[dict[str, Any] | None, str | None]:
    symbol = entry["symbol"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=10d&interval=1d"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
        valid = [(i, close) for i, close in enumerate(quote.get("close", [])) if close is not None]
        if len(valid) < 2:
            raise ValueError("fewer than two valid closing prices")
        current_i, current_close = valid[-1]
        _, previous_close = valid[-2]
        change_pct = (current_close / previous_close - 1) * 100 if previous_close else None
        traded_at = datetime.fromtimestamp(timestamps[current_i], timezone.utc).astimezone(CN_TZ).date().isoformat()
        return {
            "group": group,
            "symbol": symbol,
            "name": entry["name"],
            "role": entry["role"],
            "price": round(current_close, 3),
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "volume": (quote.get("volume") or [None])[current_i],
            "currency": meta.get("currency", ""),
            "trade_date": traded_at,
            "exchange": meta.get("exchangeName", ""),
            "source_url": f"https://finance.yahoo.com/quote/{urllib.parse.quote(symbol)}",
        }, None
    except Exception as exc:
        return None, f"market {symbol}: {type(exc).__name__}: {exc}"


def fetch_market_watchlist(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    quotes: list[dict[str, Any]] = []
    errors: list[str] = []
    for group_key, group_label in (("foreign", "国外"), ("domestic", "国内")):
        for entry in config.get(group_key, []):
            row, error = fetch_market_quote(entry, group_label)
            if row:
                quotes.append(row)
            if error:
                errors.append(error)
    return quotes, errors


def fmt_volume(value: Any) -> str:
    if value is None or value == "":
        return "—"
    number = float(value)
    if number >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.1f}万"
    return f"{number:,.0f}"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def pill_list(values: list[str], css_class: str = "pill") -> str:
    return "".join(f'<span class="{css_class}">{esc(v)}</span>' for v in values) or '<span class="muted">未识别</span>'


def source_label(item: dict[str, Any]) -> str:
    return f"T{item['source_tier']} · {item.get('publisher') or item['feed_name']}"


def build_html(items: list[dict[str, Any]], all_new_count: int, errors: list[str], now: datetime, market: list[dict[str, Any]], lookback_hours: int) -> str:
    product_counts = Counter(p for item in items for p in item["products"])
    event_counts = Counter(e for item in items for e in item["event_types"])
    sentiment_counts = Counter(item["sentiment"] for item in items)
    domestic = [x for x in items if x["geo_tag"] == "国内"]
    international = [x for x in items if x["geo_tag"] == "国外"]
    top_products = "、".join(k for k, _ in product_counts.most_common(3)) or "存储综合"
    top_events = "、".join(event_name_zh(k) for k, _ in event_counts.most_common(3)) or "行业动态"
    overall_tone = sentiment_counts.most_common(1)[0][0] if sentiment_counts else "中性"
    title = f"存储产业每日情报 · {now:%Y-%m-%d}"
    executive = f"过去{lookback_hours}小时共筛选并聚合为{len(items)}个重要事件，新闻重心集中在{top_products}，主要催化类型为{top_events}。整体信号偏{overall_tone}，但价格、扩产和供给扰动对上下游的影响可能相反，需结合订单、出货与库存继续验证。"

    chain_order = ["设备材料", "原厂制造", "封装测试/主控", "模组/渠道", "系统/终端需求"]
    chain_items: dict[str, list[dict[str, Any]]] = {key: [] for key in chain_order}
    for item in items:
        if item["stage_zh"] in chain_items:
            chain_items[item["stage_zh"]].append(item)
    chain_nodes = []
    for stage in chain_order:
        rows = chain_items[stage]
        tones = Counter(x["sentiment"] for x in rows)
        tone = tones.most_common(1)[0][0] if tones else "无新增"
        focus = "、".join(Counter(p for x in rows for p in x["products"]).most_common(2)[i][0] for i in range(min(2, len(Counter(p for x in rows for p in x["products"]).most_common(2))))) if rows else "—"
        chain_nodes.append(f'<div class="chain-node"><b>{esc(stage)}</b><strong>{len(rows)}条 · {esc(tone)}</strong><small>{esc(focus)}</small></div>')
    chain_html = '<span class="arrow">→</span>'.join(chain_nodes)

    def digest_html(group: list[dict[str, Any]]) -> str:
        return "".join(f'<li><a href="{esc(x["url"])}" target="_blank" rel="noreferrer">{esc(x["title_zh"])}</a><span>{esc(x["stage_zh"])} · {esc(x["sentiment"])}</span></li>' for x in group[:4]) or '<li class="muted">今日暂无高相关事件</li>'

    def news_html(item: dict[str, Any]) -> str:
        tone_class = {"积极": "positive", "负面": "negative", "分化": "mixed", "中性": "neutral"}.get(item["sentiment"], "neutral")
        related = ""
        if item.get("merged_count", 1) > 1:
            links = " · ".join(f'<a href="{esc(x["url"])}" target="_blank">{esc(x["publisher"])}</a>' for x in item["related_links"])
            related = f'<div class="related">合并 {item["merged_count"]} 篇重复/相关报道：{links}</div>'
        original = "" if normalized_title(item["title_zh"]) == normalized_title(item["title"]) else f'<div class="original">原题：{esc(item["title"])}</div>'
        return f"""
        <article class="news-card">
          <div class="news-title"><span class="rank">{item['relevance_score']}</span><div><a href="{esc(item['url'])}" target="_blank" rel="noreferrer">{esc(item['title_zh'])}</a>{original}<div class="meta">{esc(item['published_at'][:16].replace('T',' '))} · {esc(source_label(item))} · 证据：{esc(evidence_name_zh(item['evidence_stage']))}</div></div></div>
          <div class="tags"><span class="pill stage">{esc(item['stage_zh'])}</span><span class="pill {tone_class}">{esc(item['sentiment'])}</span><span class="pill geo">{esc(item['geo_tag'])}</span>{pill_list(item['products'], 'pill product')}</div>
          <div class="brief"><b>新闻概括：</b>{esc(item['brief_zh'])}</div>
          <div class="judgement"><b>判断与逻辑链：</b>{esc(item['judgement'])}</div>
          <div class="impact"><b>影响方向：</b>{esc(' / '.join(item['impact_directions']) or '待进一步判断')}　<b>相关标的：</b>{esc('、'.join(item['exposed_entities_zh']) or '待映射')}</div>
          {related}
        </article>"""

    def market_table(group: str) -> str:
        rows = [x for x in market if x["group"] == group]
        if not rows:
            return '<p class="muted">行情源暂未返回有效数据。</p>'
        body = ""
        for row in rows:
            change = row["change_pct"]
            cls = "up" if change is not None and change >= 0 else "down"
            sign = "+" if change is not None and change >= 0 else ""
            body += f'<tr><td><a href="{esc(row["source_url"])}" target="_blank">{esc(row["name"])}</a><small>{esc(row["symbol"])}</small></td><td>{esc(row["role"])}</td><td>{esc(row["price"])} {esc(row["currency"])}</td><td class="{cls}">{sign}{esc(change)}%</td><td>{esc(fmt_volume(row["volume"]))}</td><td>{esc(row["trade_date"])}</td></tr>'
        return f'<table><thead><tr><th>标的</th><th>产业环节</th><th>收盘价</th><th>日涨跌</th><th>成交量</th><th>交易日</th></tr></thead><tbody>{body}</tbody></table>'

    domestic_cards = "".join(news_html(x) for x in domestic)
    international_cards = "".join(news_html(x) for x in international)
    error_html = "" if not errors else '<details><summary>部分数据源异常（其余内容已正常生成）</summary><pre>' + esc("\n".join(errors)) + '</pre></details>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>
<style>
:root{{--bg:#f4f7fb;--ink:#14213d;--muted:#64748b;--blue:#165dff;--blue-soft:#eaf1ff;--orange:#b45309;--orange-soft:#fff3df;--line:#dce3ee;--card:#fff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}main{{max-width:1280px;margin:auto;padding:28px}}h1{{font-size:28px;margin:0}}h2{{font-size:20px;margin:30px 0 12px}}h3{{font-size:16px;margin:0 0 10px}}a{{color:inherit}}.sub,.muted,.meta,.original{{color:var(--muted)}}.sub{{margin:4px 0 18px}}.panel,.news-card,.digest{{background:var(--card);border:1px solid var(--line);border-radius:12px}}.panel{{padding:17px;margin:12px 0}}.summary{{font-size:16px;border-left:4px solid var(--blue)}}.summary-grid,.market-grid,.digest-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.mini-stat{{display:flex;gap:20px;margin-top:12px;color:var(--muted)}}.mini-stat b{{color:var(--ink);font-size:18px}}.chain{{display:flex;align-items:stretch;gap:7px;overflow-x:auto;padding:4px 0 8px}}.chain-node{{min-width:170px;flex:1;background:white;border:1px solid var(--line);border-top:4px solid var(--blue);border-radius:10px;padding:13px}}.chain-node b,.chain-node strong,.chain-node small{{display:block}}.chain-node strong{{margin:7px 0;font-size:15px}}.chain-node small{{color:var(--muted)}}.arrow{{align-self:center;color:#94a3b8;font-size:22px}}.digest{{padding:15px}}.digest ul{{margin:0;padding-left:20px}}.digest li{{margin:8px 0}}.digest li a{{font-weight:650;text-decoration:none}}.digest li span{{display:block;font-size:12px;color:var(--muted)}}.news-list{{display:grid;grid-template-columns:1fr;gap:12px}}.news-card{{padding:17px}}.news-title{{display:grid;grid-template-columns:42px 1fr;gap:11px}}.news-title a{{font-size:17px;font-weight:750;text-decoration:none}}.news-title a:hover{{color:var(--blue)}}.rank{{width:38px;height:38px;display:grid;place-items:center;background:var(--blue-soft);color:var(--blue);border-radius:9px;font-weight:800}}.original{{font-size:12px;margin-top:3px}}.meta{{font-size:12px;margin-top:4px}}.tags{{margin:10px 0}}.pill{{display:inline-block;margin:0 5px 5px 0;padding:2px 8px;border-radius:20px;background:#eef2f7;font-size:12px}}.stage,.geo,.product{{background:var(--blue-soft);color:#19499b}}.positive{{background:#dfeaff;color:#123f94}}.negative{{background:var(--orange-soft);color:#8a4300}}.mixed{{background:#f0e8ff;color:#5d2c91}}.neutral{{background:#eef2f7;color:#475569}}.brief{{margin:7px 0;color:#334155}}.judgement{{background:#f8fafc;border-left:3px solid var(--blue);padding:10px 12px}}.impact,.related{{margin-top:9px;padding-top:8px;border-top:1px dashed var(--line);font-size:13px}}.related{{color:var(--muted)}}.related a{{color:var(--blue)}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{background:#f8fafc}}td small{{display:block;color:var(--muted)}}.up{{color:#164da6;font-weight:700}}.down{{color:#a04b00;font-weight:700}}details{{margin-top:18px;color:var(--orange)}}pre{{white-space:pre-wrap}}footer{{margin:30px 0;color:var(--muted);font-size:12px}}@media(max-width:850px){{.summary-grid,.market-grid,.digest-grid{{grid-template-columns:1fr}}main{{padding:16px}}.chain{{display:grid;grid-template-columns:1fr 1fr}}.arrow{{display:none}}}}@media(max-width:560px){{.chain{{grid-template-columns:1fr}}.panel{{overflow-x:auto}}}}
</style></head><body><main>
<h1>{esc(title)}</h1><p class="sub">生成时间：{now:%Y-%m-%d %H:%M}（北京时间） · 新闻窗口：过去 {lookback_hours} 小时 · 事件级去重后最多 15 条</p>
<section class="panel summary"><b>今日摘要</b><p>{esc(executive)}</p><div class="mini-stat"><span><b>{len(items)}</b> 个重要事件</span><span><b>{len(domestic)}</b> 国内</span><span><b>{len(international)}</b> 国外</span><span><b>{all_new_count}</b> 本轮新发现</span></div></section>
<h2>今日新闻对存储产业链的影响</h2><p class="muted">产业链结构：设备材料 → 存储原厂 → 封装/主控 → 模组渠道 → 系统与终端需求。节点数字为今日入选事件数，方向为该节点新闻的主导标签。</p><section class="chain">{chain_html}</section>
<h2>国内 / 国外主要摘要</h2><section class="digest-grid"><div class="digest"><h3>国内重点</h3><ul>{digest_html(domestic)}</ul></div><div class="digest"><h3>国外重点</h3><ul>{digest_html(international)}</ul></div></section>
<h2>国内重要新闻</h2><section class="news-list">{domestic_cards or '<div class="panel muted">今日暂无入选的国内事件。</div>'}</section>
<h2>国外重要新闻</h2><section class="news-list">{international_cards or '<div class="panel muted">今日暂无入选的国外事件。</div>'}</section>
<h2>核心标的最近完整交易日表现</h2><p class="muted">公开行情源：Yahoo Finance；不同市场休市日和收盘时间不同，以每行交易日为准。涨跌幅由最近两个有效收盘价计算。</p><section class="market-grid"><div class="panel"><h3>国外核心标的</h3>{market_table('国外')}</div><div class="panel"><h3>国内核心标的</h3>{market_table('国内')}</div></section>
{error_html}<footer>本报告自动聚合公开信息并进行事件级去重、标题翻译和规则化逻辑映射。机器翻译及媒体报道可能存在误差，重大判断请回到原文和公司一手披露核验；不构成投资建议。</footer></main></body></html>"""


def write_csv(path: Path, items: list[dict[str, Any]]) -> None:
    fields = ["published_at", "title_zh", "title", "url", "publisher", "source_tier", "relevance_score", "geo_tag", "stage_zh", "sentiment", "merged_count", "products", "event_types", "evidence_stage", "entities", "tickers", "brief_zh", "impact_directions", "judgement", "verification"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for item in items:
            row = {k: item.get(k, "") for k in fields}
            for key in ("products", "event_types", "entities", "tickers", "impact_directions"):
                row[key] = " | ".join(row[key])
            writer.writerow(row)


def build_snapshot(
    items: list[dict[str, Any]],
    market: list[dict[str, Any]],
    errors: list[str],
    now: datetime,
    lookback_hours: int,
    run_stats: dict[str, Any],
) -> dict[str, Any]:
    """Return the bounded, public dashboard snapshot.

    Aggregator text is kept as a discovery signal. It never becomes a formal
    price, order, shipment, or earnings metric without a reviewed source.
    """
    product_counts = Counter(p for item in items for p in item.get("products", []))
    layer_counts = Counter(item.get("stage_zh", "未识别") for item in items)
    event_counts = Counter(e for item in items for e in item.get("event_types", []))
    evidence_counts = Counter(item.get("evidence_stage", "0_Unspecified") for item in items)
    safe_items = []
    for item in items:
        safe_items.append({
            key: item.get(key)
            for key in (
                "id", "published_at", "title_zh", "title", "url", "publisher",
                "source_tier", "relevance_score", "geo_tag", "stage_zh",
                "sentiment", "merged_count", "products", "event_types",
                "evidence_stage", "entities", "tickers", "brief_zh",
                "impact_directions", "judgement", "verification",
            )
        })
    return {
        "meta": {
            "generated_at": now.isoformat(timespec="seconds"),
            "lookback_hours": lookback_hours,
            "delivery": "public-discovery-snapshot",
            "methodology": "公开RSS用于发现；正式量价、订单和业绩结论必须回到一手来源。",
        },
        "quality": {
            "status": "partial" if errors else "ready",
            "selected_events": len(items),
            "market_quotes": len(market),
            "source_errors": len(errors),
            "errors": errors[:20],
            "noise_policy": "排除促销、赠品、评测、ETF荐股和零售优惠；来源等级优先。",
        },
        "summary": {
            "product_counts": dict(product_counts),
            "layer_counts": dict(layer_counts),
            "event_counts": dict(event_counts),
            "evidence_counts": dict(evidence_counts),
        },
        "events": safe_items,
        "market": market,
        "run_stats": run_stats,
    }


def validate_config(sources: dict[str, Any], ontology: dict[str, Any], entities: dict[str, Any]) -> None:
    assert sources.get("feeds"), "sources.json has no feeds"
    for key in ("products", "chain_layers", "event_types", "evidence_stages", "logic_templates", "exclusion_terms"):
        assert ontology.get(key), f"ontology.json missing {key}"
    assert entities.get("entities"), "entities.json has no entities"
    known_events = set(ontology["event_types"])
    missing_templates = known_events - set(ontology["logic_templates"])
    assert not missing_templates, f"Missing logic templates: {sorted(missing_templates)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the storage-industry intelligence monitor")
    parser.add_argument("--hours", type=int, default=30, help="report lookback window")
    parser.add_argument("--max-per-feed", type=int, default=40)
    parser.add_argument("--min-score", type=int, default=12)
    parser.add_argument("--target-news", type=int, default=14)
    parser.add_argument("--max-news", type=int, default=15)
    parser.add_argument("--validate-config", action="store_true")
    args = parser.parse_args()
    ensure_dirs()
    sources_cfg = load_json(CONFIG / "sources.json")
    ontology = load_json(CONFIG / "ontology.json")
    entities_cfg = load_json(CONFIG / "entities.json")
    market_cfg = load_json(CONFIG / "market_watchlist.json")
    validate_config(sources_cfg, ontology, entities_cfg)
    if args.validate_config:
        print("Configuration validation passed.")
        return 0
    now = datetime.now(CN_TZ)
    collected: list[dict[str, Any]] = []
    errors: list[str] = []
    enabled_feeds = [source for source in sources_cfg["feeds"] if source.get("enabled", True)]
    for source in enabled_feeds:
        items, error = fetch(source, now, args.max_per_feed)
        collected.extend(items)
        if error:
            errors.append(error)
    analyzed = [analyze(x, ontology, entities_cfg["entities"], sources_cfg.get("source_domain_tiers", {}), now) for x in collected]
    analyzed = [x for x in analyzed if x["relevance_score"] >= args.min_score and x["products"] and not x["excluded_as_retail_noise"]]
    history_path = DATA / "news.jsonl"
    history = load_history(history_path)
    new_count = 0
    for item in analyzed:
        if item["id"] not in history:
            item["first_seen_at"] = now.isoformat()
            new_count += 1
        else:
            item["first_seen_at"] = history[item["id"]].get("first_seen_at", now.isoformat())
        history[item["id"]] = item
    save_history(history_path, history)
    cutoff = now - timedelta(hours=args.hours)
    window = [x for x in history.values() if datetime.fromisoformat(x["published_at"]) >= cutoff and x["relevance_score"] >= args.min_score and not is_retail_noise(x, ontology)]
    window.sort(key=lambda x: (x["relevance_score"], x["published_at"]), reverse=True)
    clustered = cluster_events(window)
    selected = select_balanced_events(clustered, args.target_news, min(20, args.max_news))
    selected = enrich_for_report(selected)
    selected = [item for item in selected if not is_retail_noise(item, ontology)]
    market, market_errors = fetch_market_watchlist(market_cfg)
    errors.extend(market_errors)
    stem = f"storage_intel_{now:%Y%m%d}"
    html_path = OUTPUT / f"{stem}.html"
    csv_path = OUTPUT / f"{stem}.csv"
    json_path = OUTPUT / f"{stem}.json"
    html_path.write_text(build_html(selected, new_count, errors, now, market, args.hours), encoding="utf-8")
    write_csv(csv_path, selected)
    shutil.copyfile(html_path, OUTPUT / "latest.html")
    shutil.copyfile(csv_path, OUTPUT / "latest.csv")
    log_record = {"run_at": now.isoformat(), "feeds": len(enabled_feeds), "fetched": len(collected), "relevant": len(analyzed), "new": new_count, "window": len(window), "clusters": len(clustered), "selected": len(selected), "market_quotes": len(market), "errors": errors, "html": str(html_path), "csv": str(csv_path), "json": str(json_path)}
    snapshot = build_snapshot(selected, market, errors, now, args.hours, log_record)
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(json_path, OUTPUT / "latest.json")
    with (LOGS / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_record, ensure_ascii=False) + "\n")
    print(json.dumps(log_record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
