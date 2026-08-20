#!/usr/bin/env python3
"""抓取公开来源、保存历史快照并生成自包含 HTML 看板。仅使用 Python 标准库。"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import re
import ssl
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 "
    "LLM-Market-Monitor/1.0"
)
TIMEOUT_SECONDS = 25


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data.strip():
            self.parts.append(data.strip())


def now_shanghai() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp"
    ) as handle:
        handle.write(content)
        temp_name = handle.name
    Path(temp_name).replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def log(log_path: Path, message: str) -> None:
    stamp = now_shanghai().strftime("%Y-%m-%d %H:%M:%S%z")
    line = f"[{stamp}] {message}"
    print(line)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def decode_body(raw: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([\w-]+)", content_type or "", flags=re.I)
    candidates = [charset_match.group(1)] if charset_match else []
    candidates += ["utf-8", "gb18030"]
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_url(url: str) -> tuple[str, int, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=context) as response:
        raw = response.read(5_000_000)
        return decode_body(raw, response.headers.get("Content-Type", "")), response.status, response.geturl()


def visible_text(document: str) -> str:
    parser = VisibleTextParser()
    parser.feed(document)
    text = " ".join(parser.parts)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def compact_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def numeric_mentions(text: str, value: float) -> bool:
    variants = {
        f"{value:g}",
        f"{value:.1f}",
        f"{value:.2f}",
        f"{value:.3f}",
        f"{value:.6f}".rstrip("0").rstrip("."),
    }
    return any(re.search(rf"(?<!\d){re.escape(v)}(?!\d)", text) for v in variants)


def verify_price_record(record: dict[str, Any], source_text: str) -> str:
    if not source_text:
        return "unavailable"
    anchor = str(record["model"]).casefold()
    folded = source_text.casefold()
    position = folded.find(anchor)
    if position < 0:
        anchor = anchor.replace("-", " ")
        position = folded.find(anchor)
    if position < 0:
        return "model_not_found"
    window = source_text[max(0, position - 1200) : position + 3500]
    values = [record.get("input_per_m"), record.get("output_per_m")]
    if record.get("cached_input_per_m") is not None:
        values.append(record["cached_input_per_m"])
    checks = [numeric_mentions(window, float(value)) for value in values if value is not None]
    return "values_seen_near_model" if checks and all(checks) else "model_seen_values_unparsed"


def scrape_source(source: dict[str, Any], previous: dict[str, Any]) -> tuple[dict[str, Any], str]:
    checked_at = now_shanghai().isoformat(timespec="seconds")
    result = {
        "source_id": source["id"],
        "company_id": source.get("company_id"),
        "name": source["name"],
        "url": source["url"],
        "kind": source["kind"],
        "checked_at": checked_at,
        "status": "error",
        "http_status": None,
        "final_url": source["url"],
        "content_hash": None,
        "changed": None,
        "error": None,
    }
    try:
        document, status, final_url = fetch_url(source["url"])
        text = visible_text(document)
        digest = compact_hash(text)
        old_hash = (previous or {}).get("content_hash")
        source_status = "ok" if len(text) >= 100 else "partial_dynamic"
        result.update(
            {
                "status": source_status,
                "http_status": status,
                "final_url": final_url,
                "content_hash": digest,
                "changed": bool(old_hash and old_hash != digest) if source_status == "ok" else None,
                "text_chars": len(text),
                "error": None if source_status == "ok" else "页面主要内容由前端动态加载，已抓到页面但无法稳定解析正文",
            }
        )
        return result, text
    except Exception as exc:  # 网络源的失败不应中止整个看板
        result["error"] = f"{type(exc).__name__}: {exc}"[:400]
        return result, ""


def parse_rss_date(value: str) -> str:
    try:
        parsed = dt.datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %Z")
        return parsed.replace(tzinfo=dt.timezone.utc).isoformat()
    except ValueError:
        return value


def fetch_news_signal(query: str) -> list[dict[str, Any]]:
    rss_url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode(
            {
                "q": query,
                "hl": "zh-CN",
                "gl": "CN",
                "ceid": "CN:zh-Hans",
            }
        )
    )
    document, _, _ = fetch_url(rss_url)
    root = ET.fromstring(document)
    output: list[dict[str, Any]] = []
    for item in root.findall("./channel/item")[:15]:
        source_node = item.find("source")
        output.append(
            {
                "query": query,
                "title": (item.findtext("title") or "").strip(),
                "url": (item.findtext("link") or "").strip(),
                "published_at": parse_rss_date((item.findtext("pubDate") or "").strip()),
                "publisher": (source_node.text or "").strip() if source_node is not None else "",
                "status": "待人工复核",
            }
        )
    return output


def collect_news(queries: list[str], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url = {item.get("url"): item for item in existing if item.get("url")}
    for query in queries:
        try:
            for item in fetch_news_signal(query):
                by_url[item["url"]] = item
        except Exception:
            continue
    values = list(by_url.values())
    values.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return values[:80]


def read_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-60:]


def blended_cost_usd(record: dict[str, Any], cny_per_usd: float) -> float:
    value = float(record["input_per_m"]) * 0.75 + float(record["output_per_m"]) * 0.25
    return value / cny_per_usd if record["currency"] == "CNY" else value


def prepare_payload(
    config: dict[str, Any],
    metrics: dict[str, Any],
    source_states: list[dict[str, Any]],
    source_texts: dict[str, str],
    signals: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    companies = {item["id"]: item for item in config["companies"]}
    cny_per_usd = float(config["fx"]["CNY_per_USD"])
    states = {item["source_id"]: item for item in source_states}
    pricing: list[dict[str, Any]] = []

    for item in metrics["pricing"]:
        record = dict(item)
        company = companies[record["company_id"]]
        record["company"] = company["name"]
        record["region"] = company["region"]
        record["blended_cost_usd"] = round(blended_cost_usd(record, cny_per_usd), 6)
        record["source_check"] = verify_price_record(
            record, source_texts.get(record["source_id"], "")
        )
        source_state = states.get(record["source_id"], {})
        record["source_status"] = source_state.get("status", "missing")
        record["source_name"] = source_state.get("name", record["source_id"])
        record["source_url"] = source_state.get("final_url") or source_state.get("url")
        pricing.append(record)

    pricing.sort(key=lambda row: row["blended_cost_usd"])
    previous_prices: dict[str, dict[str, Any]] = {}
    if history:
        for row in history[-1].get("pricing", []):
            previous_prices[f"{row['company_id']}::{row['model']}"] = row
    for record in pricing:
        prior = previous_prices.get(f"{record['company_id']}::{record['model']}")
        record["price_delta_pct"] = None
        if prior and prior.get("blended_cost_usd"):
            record["price_delta_pct"] = round(
                (record["blended_cost_usd"] / prior["blended_cost_usd"] - 1) * 100, 2
            )

    business = []
    for item in metrics["business_metrics"]:
        record = dict(item)
        record["company"] = companies[record["company_id"]]["name"]
        record["region"] = companies[record["company_id"]]["region"]
        business.append(record)
    business.sort(key=lambda row: row["value_usd_b"], reverse=True)

    platform_metrics = [dict(item) for item in metrics.get("platform_metrics", [])]
    platform_metrics.sort(key=lambda row: row.get("value_usd_b", 0), reverse=True)

    tokens = []
    for item in metrics["token_disclosures"]:
        record = dict(item)
        record["company"] = (
            companies[record["company_id"]]["name"] if record.get("company_id") else "中国全行业"
        )
        tokens.append(record)

    successful = sum(1 for item in source_states if item["status"] == "ok")
    changed = sum(1 for item in source_states if item.get("changed"))
    verified = sum(1 for item in pricing if item["source_check"] == "values_seen_near_model")
    disclosed_arr = sum(1 for item in business if item["metric"] in {"arr", "annualized_revenue"})

    token_daily = []
    for item in tokens:
        if item["metric"] == "inference_tokens_per_day":
            token_daily.append(float(item["value_t"]))
        elif item["metric"] == "api_tokens_per_minute":
            token_daily.append(float(item["value_t"]) * 1440)

    latest_history = history[-12:]
    history_series: dict[str, list[dict[str, Any]]] = {}
    for snapshot in latest_history:
        for item in snapshot.get("pricing", []):
            key = f"{item['company_id']}::{item['model']}"
            history_series.setdefault(key, []).append(
                {"run_at": snapshot["run_at"], "value": item["blended_cost_usd"]}
            )

    return {
        "meta": {
            "title": config["dashboard_title"],
            "generated_at": now_shanghai().isoformat(timespec="seconds"),
            "metrics_as_of": metrics["as_of"],
            "schedule": "每日 07:30（Asia/Shanghai）",
            "fx_note": f"跨币种比较按 1 USD = {cny_per_usd:g} CNY；{config['fx']['note']}",
        },
        "kpis": {
            "companies": len(companies),
            "pricing_models": len(pricing),
            "source_success": f"{successful}/{len(source_states)}",
            "source_changed": changed,
            "arr_disclosures": disclosed_arr,
            "price_verified": f"{verified}/{len(pricing)}",
            "largest_daily_token_t": max(token_daily) if token_daily else None,
        },
        "pricing": pricing,
        "business": business,
        "platform_metrics": platform_metrics,
        "tokens": tokens,
        "sources": source_states,
        "signals": signals[:40],
        "history_series": history_series,
        "methodology": {
            "blended_cost": "每 100 万总 tokens 假设输入 75%、输出 25%；不含缓存、Batch、长上下文、工具调用和企业折扣。",
            "arr": "ARR、annualized revenue、收入运行率和年度收入分别保留原始标签，不强行合并。",
            "platform": "云收入与AI/芯片业务运行率用于验证下游需求兑现，不等同于基础模型收入。",
            "missing": "未披露不等于 0。科技集团通常不单独披露基础模型 ARR。",
            "automation": "官方页自动抓取、指纹和近邻数字复核；新闻仅进入待复核池，不自动写入正式指标。",
        },
    }


def json_for_script(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def render_html(payload: dict[str, Any]) -> str:
    data_json = json_for_script(payload)
    generated = html.escape(payload["meta"]["generated_at"])
    title = html.escape(payload["meta"]["title"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>{title}</title>
<style>
:root{{--ink:#17212b;--muted:#65717d;--line:#dfe5ea;--paper:#f4f6f8;--card:#fff;--blue:#175cd3;--gold:#b7791f;--orange:#c2410c;--olive:#64752d;--pink:#a83268;--good:#176b49;--warn:#9a6700;--bad:#b42318}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth;overflow-x:hidden}}body{{margin:0;overflow-x:hidden;background:var(--paper);color:var(--ink);font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.55}}
a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}.wrap{{max-width:1320px;margin:auto;padding:28px 24px 64px}}
.hero{{background:linear-gradient(135deg,#102a43,#153e75);color:#fff;border-radius:20px;padding:34px 36px;box-shadow:0 18px 45px #102a4326}}
.eyebrow{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.72}}h1{{font-size:clamp(28px,4vw,46px);line-height:1.15;margin:10px 0 12px;overflow-wrap:anywhere}}.hero p{{max-width:920px;margin:0;color:#d9e8f7}}
.meta{{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}}.pill{{border:1px solid #ffffff45;border-radius:999px;padding:6px 11px;font-size:12px;max-width:100%}}
.nav{{position:sticky;top:0;z-index:5;margin:18px 0;background:#f4f6f8ee;backdrop-filter:blur(9px);padding:8px 0;display:flex;gap:8px;overflow:auto}}
.nav a{{white-space:nowrap;color:var(--ink);background:#fff;border:1px solid var(--line);border-radius:999px;padding:8px 13px;font-size:13px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:18px 0 30px}}.kpi,.card{{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:0 5px 18px #17212b0a}}
.kpi{{padding:18px}}.kpi b{{font:700 27px/1.15 ui-monospace,SFMono-Regular,Consolas,monospace;display:block}}.kpi span{{font-size:12px;color:var(--muted)}}
section,.hero,.card,.kpi{{min-width:0}}section{{margin:34px 0}}h2{{font-size:24px;margin:0 0 6px}}.sub{{color:var(--muted);margin:0 0 16px;font-size:14px}}
.grid2{{display:grid;grid-template-columns:1.25fr .75fr;gap:16px}}.card{{padding:20px;overflow:hidden}}.card h3{{font-size:16px;margin:0 0 14px}}
.bars{{display:grid;gap:12px}}.barrow{{display:grid;grid-template-columns:minmax(150px,1fr) 3fr 72px;gap:12px;align-items:center;font-size:13px}}.track{{height:16px;background:#eef2f5;border-radius:4px;overflow:hidden}}.fill{{height:100%;background:var(--blue);border-radius:4px}}.barrow.domestic .fill{{background:var(--gold)}}.value{{text-align:right;font-family:ui-monospace,Consolas,monospace}}
.tablewrap{{overflow:auto;border:1px solid var(--line);border-radius:12px}}table{{border-collapse:collapse;width:100%;min-width:880px;background:#fff}}th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;font-size:12.5px}}th{{position:sticky;top:0;background:#f7f9fb;color:#495563;font-size:11px;letter-spacing:.04em;text-transform:uppercase}}tr:last-child td{{border-bottom:0}}.num{{font-family:ui-monospace,Consolas,monospace;text-align:right}}.tag{{display:inline-block;border-radius:999px;background:#eef3f8;padding:3px 8px;font-size:11px;color:#435160}}.official{{background:#e7f6ef;color:var(--good)}}.low{{background:#fff2e8;color:var(--orange)}}
.status{{width:8px;height:8px;display:inline-block;border-radius:50%;margin-right:6px;background:var(--bad)}}.status.ok{{background:var(--good)}}.status.changed{{background:var(--warn)}}
.insights{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}}.insight{{border-left:4px solid var(--blue);background:#fff;padding:16px 18px;border-radius:0 12px 12px 0;border-top:1px solid var(--line);border-right:1px solid var(--line);border-bottom:1px solid var(--line)}}.insight:nth-child(2){{border-left-color:var(--gold)}}.insight:nth-child(3){{border-left-color:var(--pink)}}.insight b{{display:block;margin-bottom:4px}}
.signal-list{{display:grid;gap:9px}}.signal{{padding:12px 0;border-bottom:1px solid var(--line)}}.signal:last-child{{border:0}}.signal small{{color:var(--muted)}}.controls{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}}input,select{{background:#fff;border:1px solid #cbd4dc;border-radius:9px;padding:9px 11px;color:var(--ink)}}
.source-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}}.source{{border:1px solid var(--line);border-radius:11px;padding:13px;background:#fff}}.source small{{display:block;color:var(--muted);margin-top:5px;word-break:break-all}}.note{{font-size:12px;color:var(--muted)}}.empty{{padding:24px;color:var(--muted);text-align:center}}
footer{{margin-top:40px;border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:12px}}
@media(max-width:800px){{.wrap{{padding:16px 12px 40px}}.hero{{padding:26px 22px;border-radius:15px}}.grid2{{grid-template-columns:1fr}}.barrow{{grid-template-columns:120px 1fr 62px}}}}
@media print{{body{{background:#fff}}.nav,.controls{{display:none}}.wrap{{max-width:none;padding:0}}.hero,.card,.kpi{{box-shadow:none}}a{{color:inherit}}}}
</style>
</head>
<body>
<main class="wrap">
  <header class="hero">
    <div class="eyebrow">LLM commercialization monitor · snapshot first</div>
    <h1>{title}</h1>
    <p>追踪核心国内外大模型公司的 API token 价格、商业化收入运行率、公开 token 用量与来源变化。所有非同口径数据均保留原标签与可信度。</p>
    <div class="meta"><span class="pill" id="generated"></span><span class="pill" id="schedule"></span><span class="pill">自包含 HTML · 可离线分享</span></div>
  </header>
  <nav class="nav"><a href="#overview">总览</a><a href="#pricing">Token 价格</a><a href="#business">ARR / 收入</a><a href="#usage">Token 用量</a><a href="#signals">变化信号</a><a href="#sources">来源与方法</a></nav>
  <section id="overview">
    <div class="kpis" id="kpis"></div>
    <div class="insights" id="insights"></div>
  </section>
  <section id="pricing">
    <h2>Token 价格与成本结构</h2>
    <p class="sub">横向图采用统一“75% 输入 + 25% 输出”的每百万总 tokens 混合成本；原始标价仍在明细表中。</p>
    <div class="grid2">
      <div class="card"><h3>标准化混合成本（USD / 百万总 tokens）</h3><div class="bars" id="priceBars"></div></div>
      <div class="card"><h3>价格观察</h3><div id="priceNotes"></div></div>
    </div>
    <div class="card" style="margin-top:16px">
      <div class="controls"><select id="regionFilter"><option value="">全部地区</option><option>国内</option><option>海外</option></select><input id="priceSearch" placeholder="搜索公司或模型"></div>
      <div class="tablewrap"><table><thead><tr><th>地区</th><th>公司 / 模型</th><th>计价档位</th><th class="num">输入</th><th class="num">缓存输入</th><th class="num">输出</th><th class="num">上下文</th><th class="num">混合成本 USD</th><th>证据</th></tr></thead><tbody id="priceTable"></tbody></table></div>
    </div>
  </section>
  <section id="business">
    <h2>ARR 与商业化</h2>
    <p class="sub">只展示公开披露或媒体报道值；“年度收入（非 ARR）”不会与 ARR 偷换口径。</p>
    <div class="grid2"><div class="card"><h3>公开值（十亿美元）</h3><div class="bars" id="arrBars"></div></div><div class="card"><h3>口径提示</h3><p>独立模型公司更常披露年化收入；Google、阿里、字节等集团通常不拆分基础模型 ARR。空白代表未披露，不代表业务为零。</p><p class="note" id="arrCoverage"></p></div></div>
    <div class="card" style="margin-top:16px"><div class="tablewrap"><table><thead><tr><th>公司</th><th>指标</th><th>期间</th><th class="num">十亿美元</th><th>可信度</th><th>备注 / 来源</th></tr></thead><tbody id="businessTable"></tbody></table></div></div>
  </section>
  <section id="usage">
    <h2>Token 用量与披露</h2>
    <p class="sub">线上推理 tokens、训练 tokens、行业总量是不同测量对象，分行展示，不做简单求和。</p>
    <div class="card"><div class="tablewrap"><table><thead><tr><th>对象</th><th>指标</th><th>期间</th><th class="num">数值</th><th>可信度</th><th>口径 / 来源</th></tr></thead><tbody id="tokenTable"></tbody></table></div></div>
  </section>
  <section id="signals">
    <h2>变化信号与复核队列</h2>
    <p class="sub">官方页内容变化会标黄；新闻 RSS 只作为线索，必须人工复核后才进入正式指标。</p>
    <div class="grid2"><div class="card"><h3>官方来源页状态</h3><div id="changedSources"></div></div><div class="card"><h3>最新待复核新闻</h3><div class="signal-list" id="signalList"></div></div></div>
  </section>
  <section id="sources">
    <h2>来源、质量与方法</h2>
    <p class="sub" id="fxNote"></p>
    <div class="source-grid" id="sourceGrid"></div>
    <div class="card" style="margin-top:16px"><h3>方法说明</h3><ul id="methodology"></ul></div>
  </section>
  <footer>生成时间：{generated}。这是公开信息研究看板，不构成财务、投资或采购建议。正式引用前请打开原始来源复核。</footer>
</main>
<noscript><div style="padding:24px;font-family:sans-serif">此看板需要浏览器启用 JavaScript。数据仍保存在同目录的 JSON 与历史文件中。</div></noscript>
<script>
const D={data_json};
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
const money=(v,d=2)=>Number(v).toLocaleString("zh-CN",{{minimumFractionDigits:d,maximumFractionDigits:d}});
const fmtDate=s=>{{try{{return new Date(s).toLocaleString("zh-CN",{{hour12:false}})}}catch{{return s}}}};
$("#generated").textContent="更新："+fmtDate(D.meta.generated_at);
$("#schedule").textContent=D.meta.schedule;
$("#fxNote").textContent=D.meta.fx_note;

const kpis=[
  ["覆盖公司",D.kpis.companies],["模型价格",D.kpis.pricing_models],["来源抓取",D.kpis.source_success],
  ["网页变化",D.kpis.source_changed],["ARR 类披露",D.kpis.arr_disclosures],["官网近邻复核",D.kpis.price_verified]
];
$("#kpis").innerHTML=kpis.map(x=>`<div class="kpi"><b>${{esc(x[1])}}</b><span>${{esc(x[0])}}</span></div>`).join("");

const cheapest=D.pricing[0], overseas=D.pricing.filter(x=>x.region==="海外"), domestic=D.pricing.filter(x=>x.region==="国内");
const med=a=>{{const x=[...a].sort((p,q)=>p-q);return x.length?x[Math.floor(x.length/2)]:null}};
const om=med(overseas.map(x=>x.blended_cost_usd)), dm=med(domestic.map(x=>x.blended_cost_usd));
$("#insights").innerHTML=[
  `<div class="insight"><b>公开标价的成本下沿</b>${{esc(cheapest.company)}} ${{esc(cheapest.model)}} 的标准化混合成本约 <strong>$${{money(cheapest.blended_cost_usd,3)}}</strong>/百万总 tokens。</div>`,
  `<div class="insight"><b>国内外价格带</b>当前样本中位数：国内 $${{money(dm,2)}}，海外 $${{money(om,2)}}。这是标价比较，不代表同能力或同质量。</div>`,
  `<div class="insight"><b>商业化信息不对称</b>${{D.kpis.arr_disclosures}} 家披露 ARR/年化收入类指标；集团型厂商与未融资公司仍存在显著空白。</div>`
].join("");

function renderBars(target,rows,valueField,labelFn,formatter){{
  const max=Math.max(...rows.map(x=>Number(x[valueField])||0),1);
  $(target).innerHTML=rows.map(x=>`<div class="barrow ${{x.region==="国内"?"domestic":""}}"><div>${{esc(labelFn(x))}}</div><div class="track"><div class="fill" style="width:${{Math.max(1,(Number(x[valueField])/max)*100)}}%"></div></div><div class="value">${{esc(formatter(x[valueField]))}}</div></div>`).join("");
}}
renderBars("#priceBars",D.pricing,"blended_cost_usd",x=>x.model,v=>"$"+money(v,2));
$("#priceNotes").innerHTML=`<p><span class="tag">比较口径</span> ${{esc(D.methodology.blended_cost)}}</p><p><span class="tag">价格变化</span> 历史从首次自动运行开始积累；当前没有变化时显示持平。</p><p class="note">缓存、Batch、长上下文、优先队列和工具费可能显著改变真实账单。</p>`;

function renderPriceTable(){{
  const region=$("#regionFilter").value, q=$("#priceSearch").value.trim().toLowerCase();
  const rows=D.pricing.filter(x=>(!region||x.region===region)&&(!q||(x.company+" "+x.model).toLowerCase().includes(q)));
  $("#priceTable").innerHTML=rows.map(x=>`<tr><td><span class="tag">${{esc(x.region)}}</span></td><td><strong>${{esc(x.company)}}</strong><br>${{esc(x.model)}}</td><td>${{esc(x.tier)}}</td><td class="num">${{esc(x.currency)}} ${{money(x.input_per_m,3)}}</td><td class="num">${{x.cached_input_per_m==null?"—":esc(x.currency)+" "+money(x.cached_input_per_m,3)}}</td><td class="num">${{esc(x.currency)}} ${{money(x.output_per_m,3)}}</td><td class="num">${{money(x.context_k,0)}}K</td><td class="num">$${{money(x.blended_cost_usd,3)}}</td><td><span class="tag ${{x.evidence==="官方"?"official":"low"}}">${{esc(x.evidence)}}</span><br><small>${{esc(x.source_check)}}</small></td></tr>`).join("")||`<tr><td colspan="9" class="empty">没有匹配数据</td></tr>`;
}}
$("#regionFilter").addEventListener("change",renderPriceTable);$("#priceSearch").addEventListener("input",renderPriceTable);renderPriceTable();

renderBars("#arrBars",D.business,"value_usd_b",x=>x.company,v=>"$"+money(v,2)+"B");
$("#arrCoverage").textContent=`正式样本 ${{D.business.length}} 条，其中 ARR/年化收入 ${{D.kpis.arr_disclosures}} 条。`;
$("#businessTable").innerHTML=D.business.map(x=>`<tr><td><strong>${{esc(x.company)}}</strong><br><span class="tag">${{esc(x.region)}}</span></td><td>${{esc(x.label)}}</td><td>${{esc(x.period)}}</td><td class="num">$${{money(x.value_usd_b,3)}}B</td><td>${{esc(x.confidence)}}</td><td>${{esc(x.note)}}<br><a href="${{esc(x.source_url)}}" target="_blank" rel="noopener">${{esc(x.source_name)}} ↗</a></td></tr>`).join("");

const metricNames={{inference_tokens_per_day:"推理 tokens/日",api_tokens_per_minute:"API tokens/分钟",china_inference_tokens_per_day:"中国推理 tokens/日",training_tokens:"训练 tokens"}};
$("#tokenTable").innerHTML=D.tokens.map(x=>`<tr><td><strong>${{esc(x.company)}}</strong></td><td>${{esc(metricNames[x.metric]||x.metric)}}</td><td>${{esc(x.period)}}</td><td class="num">${{money(x.value_t,x.value_t<0.1?3:1)}} ${{esc(x.unit)}}</td><td>${{esc(x.confidence)}}</td><td>${{esc(x.note)}}<br><a href="${{esc(x.source_url)}}" target="_blank" rel="noopener">${{esc(x.source_name)}} ↗</a></td></tr>`).join("");

const changed=D.sources.filter(x=>x.changed), failed=D.sources.filter(x=>x.status!=="ok");
$("#changedSources").innerHTML=(changed.length?changed.map(x=>`<div class="signal"><span class="status changed"></span><strong>${{esc(x.name)}}</strong><br><small>页面内容指纹变化 · ${{fmtDate(x.checked_at)}}</small></div>`).join(""):`<p><span class="status ok"></span>本次未发现已成功抓取来源的内容指纹变化。</p>`)+(failed.length?`<p class="note">${{failed.length}} 个来源抓取失败，已保留上次正式指标，不会写成 0。</p>`:"");
$("#signalList").innerHTML=(D.signals.slice(0,12).map(x=>`<div class="signal"><a href="${{esc(x.url)}}" target="_blank" rel="noopener">${{esc(x.title)}}</a><small>${{esc(x.publisher)}} · ${{fmtDate(x.published_at)}} · 待复核</small></div>`).join("")||`<p class="empty">暂无新闻线索</p>`);

$("#sourceGrid").innerHTML=D.sources.map(x=>`<div class="source"><div><span class="status ${{x.status==="ok"?(x.changed?"changed":"ok"):""}}"></span><strong>${{esc(x.name)}}</strong></div><small>${{esc(x.status==="ok"?"抓取成功":"抓取失败："+(x.error||"unknown"))}}</small><small>${{fmtDate(x.checked_at)}} · ${{x.text_chars||0}} chars</small><a href="${{esc(x.url)}}" target="_blank" rel="noopener">打开原始来源 ↗</a></div>`).join("");
$("#methodology").innerHTML=Object.values(D.methodology).map(x=>`<li>${{esc(x)}}</li>`).join("");
</script>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    config_path = root / "config" / "monitor.json"
    metrics_path = root / "data" / "reported_metrics.json"
    state_path = root / "data" / "source_state.json"
    history_path = root / "data" / "history.jsonl"
    signals_path = root / "data" / "news_signals.json"
    api_path = root / "data" / "dashboard_api.json"
    output_path = root / "dashboard.html"
    log_path = root / "logs" / "update.log"

    config = load_json(config_path)
    metrics = load_json(metrics_path)
    if not config or not metrics:
        raise RuntimeError("缺少 config/monitor.json 或 data/reported_metrics.json")

    log(log_path, f"开始更新；项目目录={root}")
    previous_state_list = load_json(state_path, [])
    previous_states = {item["source_id"]: item for item in previous_state_list}
    source_states: list[dict[str, Any]] = []
    source_texts: dict[str, str] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {
            executor.submit(scrape_source, source, previous_states.get(source["id"], {})): source
            for source in config["sources"]
        }
        for future in concurrent.futures.as_completed(future_map):
            source = future_map[future]
            result, text = future.result()
            source_states.append(result)
            source_texts[source["id"]] = text
            log(log_path, f"来源 {source['id']}: {result['status']}")

    order = {source["id"]: index for index, source in enumerate(config["sources"])}
    source_states.sort(key=lambda row: order.get(row["source_id"], 999))
    atomic_write(state_path, json.dumps(source_states, ensure_ascii=False, indent=2))

    existing_signals = load_json(signals_path, [])
    signals = collect_news(config.get("news_queries", []), existing_signals)
    atomic_write(signals_path, json.dumps(signals, ensure_ascii=False, indent=2))

    history = read_history(history_path)
    payload = prepare_payload(
        config, metrics, source_states, source_texts, signals, history
    )
    snapshot = {
        "run_at": payload["meta"]["generated_at"],
        "pricing": [
            {
                "company_id": item["company_id"],
                "model": item["model"],
                "input_per_m": item["input_per_m"],
                "output_per_m": item["output_per_m"],
                "currency": item["currency"],
                "blended_cost_usd": item["blended_cost_usd"],
            }
            for item in payload["pricing"]
        ],
        "source_success": payload["kpis"]["source_success"],
        "source_changed": payload["kpis"]["source_changed"],
    }
    append_jsonl(history_path, snapshot)
    atomic_write(api_path, json.dumps(payload, ensure_ascii=False, indent=2))
    atomic_write(output_path, render_html(payload))
    log(log_path, f"完成；HTML={output_path}；新闻信号={len(signals)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
