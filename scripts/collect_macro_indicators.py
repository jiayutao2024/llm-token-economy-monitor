#!/usr/bin/env python3
"""Refresh the eight public macro indicators used by the dashboard.

Only public endpoints are used.  Each observation keeps its actual source date;
the collection timestamp is deliberately separate so monthly data is never
presented as a same-day observation.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import bisect
import calendar
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
UA = "Mozilla/5.0 (compatible; ZheshangPublicResearchMonitor/2.0)"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=max&interval=1d&events=history"
CP_ZIP = "https://www.federalreserve.gov/releases/cp/data/FRB_CP_xml.zip"
H8_CSV = "https://www.federalreserve.gov/datadownload/Output.aspx?filetype=csv&from=&label=include&lastobs=&layout=seriescolumn&rel=H8&series=c8dfa96ef1d2db40ce57121ffdddf59d&to=&type=package"
BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0?startyear={start}&endyear={end}"
FINRA_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
SHILLER_PAGE = "https://shillerdata.com/"


def fetch(url: str, timeout: int = 45) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def percentile_rank(values: Iterable[float], latest: float | None = None) -> float:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        raise ValueError("no finite observations")
    target = clean[-1] if latest is None else float(latest)
    return round(100 * sum(v <= target for v in clean) / len(clean), 1)


def baseline_percentile(baseline: dict, metric_id: str, value: float) -> float:
    """Invert the workbook-derived quantile curve (PERCENTRANK.EXC proxy)."""
    series = baseline["series"][metric_id]
    knots = series["quantile_knots"]
    values = [float(row[1]) for row in knots]
    index = bisect.bisect_left(values, float(value))
    if index <= 0:
        raw = 0.0
    elif index >= len(knots):
        raw = 100.0
    else:
        p0, v0 = float(knots[index - 1][0]), values[index - 1]
        p1, v1 = float(knots[index][0]), values[index]
        raw = (p0 + p1) / 2 if v1 == v0 else p0 + (float(value) - v0) * (p1 - p0) / (v1 - v0)
    # PERCENTRANK.EXC maps the sample extrema to 1/(n+1) and n/(n+1),
    # rather than 0 and 100 as an inclusive percentile does.
    count = int(series["sample_count"])
    floor, ceiling = 100 / (count + 1), 100 * count / (count + 1)
    raw = max(floor, min(ceiling, raw))
    return round(raw, 1)


def trailing(dates: list[str], values: list[float], years: int = 10) -> tuple[list[str], list[float]]:
    latest = datetime.strptime(dates[-1][:10], "%Y-%m-%d")
    cutoff = latest - timedelta(days=365.25 * years)
    pairs = [(d, v) for d, v in zip(dates, values) if datetime.strptime(d[:10], "%Y-%m-%d") >= cutoff]
    return [x[0] for x in pairs], [x[1] for x in pairs]


def sma(values: list[float | None], end: int, length: int) -> float | None:
    if end + 1 < length:
        return None
    sample = values[end - length + 1:end + 1]
    if any(v is None for v in sample):
        return None
    return sum(float(v) for v in sample) / length


def weighted_roc_momentum(
    values: list[float], roc_periods: tuple[int, int, int, int],
    smooth_periods: tuple[int, int, int, int],
) -> list[float | None]:
    """Workbook KST-style weighted smoothed ROC (ROC values are decimals)."""
    rocs: list[list[float | None]] = []
    for period in roc_periods:
        row: list[float | None] = []
        for index, value in enumerate(values):
            row.append(None if index < period or values[index - period] == 0 else value / values[index - period] - 1)
        rocs.append(row)
    result: list[float | None] = []
    for index in range(len(values)):
        parts = [sma(rocs[i], index, length) for i, length in enumerate(smooth_periods)]
        result.append(None if any(v is None for v in parts) else sum((i + 1) * float(v) for i, v in enumerate(parts)))
    return result


def kst_series(values: list[float]) -> list[float | None]:
    """Compatibility helper for the classic KST used by older callers/tests."""
    return weighted_roc_momentum(values, (10, 15, 20, 30), (10, 10, 10, 15))


def yahoo_series(ticker: str) -> tuple[list[str], list[float]]:
    encoded = urllib.parse.quote(ticker)
    results = []
    for url in (YAHOO.format(ticker=encoded), YAHOO.format(ticker=encoded).replace("range=max", "range=1mo")):
        data = json.loads(fetch(url).decode("utf-8"))
        results.append(data["chart"]["result"][0])
    pairs_by_date: dict[str, float] = {}
    for result in results:
        timestamps = result.get("timestamp", [])
        # Wind sheets use unadjusted close, not adjusted close.
        closes = result["indicators"]["quote"][0]["close"]
        for ts, value in zip(timestamps, closes):
            if value is not None:
                pairs_by_date[datetime.fromtimestamp(ts, timezone.utc).date().isoformat()] = float(value)
    pairs = sorted(pairs_by_date.items())
    result = results[-1]
    meta = result.get("meta", {})
    if meta.get("regularMarketPrice") is not None and meta.get("regularMarketTime"):
        live = (datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc).date().isoformat(), float(meta["regularMarketPrice"]))
        pairs = [pair for pair in pairs if pair[0] != live[0]]
        pairs.append(live)
        pairs.sort()
    return [x[0] for x in pairs], [x[1] for x in pairs]


def aligned_ratio(left: tuple[list[str], list[float]], right: tuple[list[str], list[float]]) -> tuple[list[str], list[float]]:
    rmap = dict(zip(*right))
    pairs = [(d, value / rmap[d]) for d, value in zip(*left) if d in rmap and rmap[d]]
    return [x[0] for x in pairs], [x[1] for x in pairs]


def market_metric(baseline: dict, metric_id: str, name: str, family: str, ticker_a: str, ticker_b: str | None, unit: str, note: str) -> dict:
    dates, values = yahoo_series(ticker_a)
    if ticker_b:
        dates, values = aligned_ratio((dates, values), yahoo_series(ticker_b))
    base = baseline["series"][metric_id]
    return metric(metric_id, name, family, values[-1], unit, baseline_percentile(baseline, metric_id, values[-1]), dates[-1], "日频（交易日）",
                  "Yahoo Finance 公开行情（与 Wind 收盘价回测）", f"https://finance.yahoo.com/quote/{ticker_a}/", 2, "public_validated", base["sample_count"], dates[0], note,
                  baseline_as_of=base.get("latest_period"), percentile_method="Wind底稿历史CDF + PERCENTRANK.EXC插值")


def metric(metric_id: str, name: str, family: str, value: float, unit: str, risk: float,
           period: str, frequency: str, source_name: str, source_url: str, source_tier: int,
           evidence: str, sample_count: int, history_start: str, note: str, **extra) -> dict:
    row = {
        "metric_id": metric_id, "name": name, "family": family,
        "value": round(float(value), 4), "unit": unit,
        "percentile": round(float(risk), 1), "risk_percentile": round(float(risk), 1),
        "period": period, "data_frequency": frequency, "source_date": period,
        "source_name": source_name, "source_url": source_url, "source_tier": source_tier,
        "evidence_status": evidence, "freshness_days": {"日频（交易日）": 7, "周频": 14, "月频": 75}.get(frequency, 100),
        "sample_count": sample_count, "history_start": history_start, "note": note,
    }
    row.update(extra)
    return row


def parse_cp() -> tuple[list[str], list[float]]:
    archive = zipfile.ZipFile(io.BytesIO(fetch(CP_ZIP, 90)))
    xml_name = next(name for name in archive.namelist() if name.lower().endswith("cp_data.xml"))
    dates, values, target = [], [], False
    with archive.open(xml_name) as stream:
        for event, element in ET.iterparse(stream, events=("start", "end")):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "Series" and event == "start":
                target = element.attrib.get("SERIES_NAME") == "RIFSPPNAAD90_N.B"
            elif target and tag == "Obs" and event == "end":
                value = element.attrib.get("OBS_VALUE")
                if value not in (None, "ND"):
                    dates.append(element.attrib["TIME_PERIOD"][:10]); values.append(float(value))
                element.clear()
            elif tag == "Series" and event == "end":
                if target:
                    break
                element.clear()
    return trailing(dates, values)


def parse_h8_ratio() -> tuple[list[str], list[float]]:
    rows = list(csv.reader(io.StringIO(fetch(H8_CSV, 60).decode("utf-8-sig"))))
    header = rows[5]
    # Exact Wind workbook definitions: loans *to commercial banks* / deposits.
    loan = header.index("B1047NCBD")
    deposits = header.index("B1058NCBD")
    pairs = []
    for row in rows[6:]:
        try:
            pairs.append((row[0][:10], float(row[loan]) / float(row[deposits])))
        except (ValueError, IndexError, ZeroDivisionError):
            continue
    return trailing([x[0] for x in pairs], [x[1] for x in pairs])


def funding_metric(baseline: dict) -> dict:
    cp_dates, cp_values = parse_cp()
    ratio_dates, ratio_values = parse_h8_ratio()
    kst = weighted_roc_momentum(ratio_values, (10, 13, 15, 20), (10, 13, 15, 20))
    valid = [(d, v) for d, v in zip(ratio_dates, kst) if v is not None]
    kst_dates, kst_values = [x[0] for x in valid], [float(x[1]) for x in valid]
    cp_percentile = baseline_percentile(baseline, "macro_cp_rate", cp_values[-1])
    ldr_percentile = baseline_percentile(baseline, "macro_ldr_kst", kst_values[-1])
    risk = (cp_percentile + ldr_percentile) / 2
    period = max(cp_dates[-1], ratio_dates[-1])
    return metric("macro_funding_kst", "商业票据利率 + 贷存比 KST", "流动性", cp_values[-1], "% / KST", risk,
                  period, "周频", "美联储 CP 与 H.8", "https://www.federalreserve.gov/releases/cp/", 1, "official_calculated",
                  min(baseline["series"]["macro_cp_rate"]["sample_count"], baseline["series"]["macro_ldr_kst"]["sample_count"]), max(cp_dates[0], kst_dates[0]),
                  "与底稿同口径：90天AA非金融商业票据利率，以及对商业银行贷款/存款比的 KST；两项底稿分位取均值。",
                  secondary_value=round(kst_values[-1], 4), secondary_period=kst_dates[-1], cp_percentile=cp_percentile,
                  ldr_kst_percentile=ldr_percentile, calculation="mean(base_percentile(CP90), base_percentile(KST(LDR)))",
                  kst_parameters={"roc": [10, 13, 15, 20], "sma": [10, 13, 15, 20], "weights": [1, 2, 3, 4]},
                  baseline_as_of=max(baseline["series"]["macro_cp_rate"].get("latest_period", ""), baseline["series"]["macro_ldr_kst"].get("latest_period", "")),
                  percentile_method="Wind底稿历史CDF + PERCENTRANK.EXC插值")


def cpi_series(now: datetime) -> tuple[list[str], list[float]]:
    # The unregistered BLS API limits a request to ten years, so use two
    # contiguous public requests and merge them.
    raw = []
    for start, end in ((now.year - 19, now.year - 10), (now.year - 9, now.year)):
        data = json.loads(fetch(BLS_API.format(start=start, end=end), 45).decode("utf-8"))
        raw.extend(data["Results"]["series"][0]["data"])
    pairs = []
    for row in raw:
        if row["period"].startswith("M") and row["period"] != "M13":
            try:
                month = int(row["period"][1:])
                pairs.append((f'{row["year"]}-{month:02d}-{calendar.monthrange(int(row["year"]), month)[1]:02d}', float(row["value"])))
            except ValueError:
                continue
    pairs.sort()
    return [x[0] for x in pairs], [x[1] for x in pairs]


def cpi_metric(baseline: dict, now: datetime) -> dict:
    dates, levels = cpi_series(now)
    rocs = [(dates[i], levels[i] / levels[i - 18] - 1) for i in range(18, len(levels))]
    rdates, values = [x[0] for x in rocs], [x[1] for x in rocs]
    base = baseline["series"]["macro_cpi_18roc"]
    risk = baseline_percentile(baseline, "macro_cpi_18roc", values[-1])
    return metric("macro_cpi_18roc", "CPI 18个月 ROC", "通胀", 100 * values[-1], "%", risk, rdates[-1], "月频",
                  "美国劳工统计局 BLS", "https://www.bls.gov/cpi/data.htm", 1, "official_calculated", base["sample_count"], rdates[0],
                  "与底稿同口径：美国季调 CPI 的18个月变化率；先做 ROC18，再进入底稿历史分布计算分位。",
                  calculation="CPI(t)/CPI(t-18m)-1", processed_value=values[-1], baseline_as_of=base.get("latest_period"),
                  percentile_method="Wind底稿历史CDF + PERCENTRANK.EXC插值")


def multpl_series(slug: str) -> tuple[list[str], list[float]]:
    page = fetch(f"https://www.multpl.com/{slug}/table/by-month", 60).decode("utf-8", "ignore")
    matches = re.findall(r"<td[^>]*>([^<]+)</td>\s*<td[^>]*>(.*?)</td>", page, re.S | re.I)
    pairs = []
    for date_text, value_html in matches:
        try:
            import html as html_module
            date = datetime.strptime(html_module.unescape(date_text).strip(), "%b %d, %Y").date().isoformat()
            value = float(re.sub(r"[^0-9.\-]", "", html_module.unescape(re.sub(r"<[^>]+>", "", value_html))))
            pairs.append((date, value))
        except ValueError:
            continue
    pairs.sort()
    return [x[0] for x in pairs], [x[1] for x in pairs]


def shiller_ecy_series() -> tuple[list[str], list[float]]:
    import xlrd
    page = fetch(SHILLER_PAGE, 45).decode("utf-8", "ignore")
    links = re.findall(r'(?:https:)?//img1\.wsimg\.com/[^"\']+ie_data\.xls[^"\']*', page)
    if not links:
        raise ValueError("Shiller ie_data.xls link not found")
    url = links[0].replace("&amp;", "&")
    if url.startswith("//"):
        url = "https:" + url
    workbook = xlrd.open_workbook(file_contents=fetch(url, 90))
    sheet = workbook.sheet_by_name("Data")
    pairs = []
    for row in range(8, sheet.nrows):
        raw_date, raw_value = sheet.cell_value(row, 0), sheet.cell_value(row, 16)
        if not isinstance(raw_date, (int, float)) or not isinstance(raw_value, (int, float)):
            continue
        year = int(raw_date)
        month = max(1, min(12, int(round((float(raw_date) - year) * 100))))
        # Shiller publishes a month label and may include a preliminary current
        # month, so use the first day to avoid a future-dated observation.
        pairs.append((f"{year:04d}-{month:02d}-01", float(raw_value)))
    return [x[0] for x in pairs], [x[1] for x in pairs]


def valuation_metrics(baseline: dict) -> list[dict]:
    ddates, yields = multpl_series("s-p-500-dividend-yield")
    completed = [(date, value) for date, value in zip(ddates, yields) if int(date[8:10]) == calendar.monthrange(int(date[:4]), int(date[5:7]))[1]]
    ddates, yields = [x[0] for x in completed], [x[1] for x in completed]
    inverse_yields = [1 / value for value in yields]
    dividend_kst = weighted_roc_momentum(inverse_yields, (9, 12, 18, 24), (6, 6, 6, 9))
    dvalid = [(date, value) for date, value in zip(ddates, dividend_kst) if value is not None]
    dk_dates, dk_values = [x[0] for x in dvalid], [float(x[1]) for x in dvalid]
    dividend_base = baseline["series"]["macro_dividend_kst"]
    dividend_risk = baseline_percentile(baseline, "macro_dividend_kst", dk_values[-1])

    edates, ecy = shiller_ecy_series()
    ecy_base = baseline["series"]["macro_excess_cape_yield"]
    raw_ecy_percentile = baseline_percentile(baseline, "macro_excess_cape_yield", ecy[-1])
    return [
        metric("macro_dividend_yield", "股息率 KST", "估值", yields[-1], "%", dividend_risk, ddates[-1], "月频",
               "标准普尔股息率公开月表（Multpl 展示）", "https://www.multpl.com/s-p-500-dividend-yield/table/by-month", 2, "public_validated", dividend_base["sample_count"], dk_dates[0],
               "与底稿同口径：先取 1/股息率，再按指定参数计算 KST，最后进入底稿历史分布。",
               processed_value=dk_values[-1], calculation="KST(1/dividend yield)", kst_parameters={"roc": [9, 12, 18, 24], "sma": [6, 6, 6, 9], "weights": [1, 2, 3, 4]},
               baseline_as_of=dividend_base.get("latest_period"), percentile_method="Wind底稿历史CDF + PERCENTRANK.EXC插值"),
        metric("macro_excess_cape_yield", "超额 CAPE 收益率", "估值", 100 * ecy[-1], "%", 100 - raw_ecy_percentile, edates[-1], "月频",
               "Robert Shiller ie_data.xls", SHILLER_PAGE, 1, "official_snapshot", ecy_base["sample_count"], edates[0],
               "直接读取 ie_data.xls 的 Excess CAPE Yield；展示原始分位，同时阶段风险使用反向分位（ECY 越低风险越高）；Shiller 当月值可能含估算。",
               raw_percentile=raw_ecy_percentile, processed_value=ecy[-1], calculation="Shiller ie_data.xls: Excess CAPE Yield",
               baseline_as_of=ecy_base.get("latest_period"), percentile_method="Wind底稿历史CDF + PERCENTRANK.EXC插值")
    ]


def finra_series() -> tuple[list[str], list[float]]:
    archive = zipfile.ZipFile(io.BytesIO(fetch(FINRA_XLSX, 60)))
    root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    pairs = []
    for row in root.findall(".//x:sheetData/x:row", ns)[1:]:
        cells = row.findall("x:c", ns)
        if len(cells) < 2:
            continue
        text = cells[0].findtext("x:is/x:t", default="", namespaces=ns)
        value = cells[1].findtext("x:v", default="", namespaces=ns)
        if re.fullmatch(r"\d{4}-\d{2}", text or "") and value:
            year, month = map(int, text.split("-"))
            pairs.append((f"{text}-{calendar.monthrange(year, month)[1]:02d}", float(value)))
    pairs.sort()
    return [x[0] for x in pairs], [x[1] for x in pairs]


def finra_metric(baseline: dict) -> dict:
    dates, balances = finra_series()
    kst = weighted_roc_momentum(balances, (9, 12, 18, 24), (6, 6, 6, 9))
    valid = [(d, v) for d, v in zip(dates, kst) if v is not None]
    kdates, values = [x[0] for x in valid], [float(x[1]) for x in valid]
    base = baseline["series"]["macro_margin_kst"]
    risk = baseline_percentile(baseline, "macro_margin_kst", values[-1])
    return metric("macro_margin_kst", "保证金余额 KST", "杠杆资金", values[-1], "KST", risk, kdates[-1], "月频",
                  "FINRA 保证金统计", "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics", 1, "official_calculated",
                  base["sample_count"], kdates[0], "与底稿同口径：FINRA 客户证券保证金账户借方余额按指定参数计算 KST。",
                  underlying_value=round(balances[-1], 0), underlying_unit="USD million", calculation="KST(FINRA margin debit balance)",
                  kst_parameters={"roc": [9, 12, 18, 24], "sma": [6, 6, 6, 9], "weights": [1, 2, 3, 4]},
                  baseline_as_of=base.get("latest_period"), percentile_method="Wind底稿历史CDF + PERCENTRANK.EXC插值")


def collect(root: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(CN_TZ)
    baseline_path = root / "data" / "macro_baseline.json"
    if not baseline_path.exists():
        raise FileNotFoundError("data/macro_baseline.json is required")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    rows = [
        market_metric(baseline, "macro_spy_djp", "SPY / DJP", "跨资产", "SPY", "DJP", "倍", "与底稿 SPY.OF/DJP.OF 收盘价口径一致。"),
        market_metric(baseline, "macro_discretionary_staples", "可选消费 / 必需消费", "风险偏好", "^SP500-25", "^SP500-30", "倍", "与底稿 S5COND.SPI/S5CONS.SPI 的价格指数口径一致。"),
        market_metric(baseline, "macro_dxy", "美元流动性", "流动性", "DX-Y.NYB", None, "指数点", "美元指数收盘值，与底稿逐点回测。"),
        funding_metric(baseline), cpi_metric(baseline, now), *valuation_metrics(baseline), finra_metric(baseline),
    ]
    validation_path = root / "data" / "macro_source_validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.exists() else {"meta": {"status": "missing"}, "checks": []}
    checks = {row["metric_id"]: row for row in validation.get("checks", [])}
    validation_map = {
        "macro_dividend_yield": ["macro_dividend_kst"],
        "macro_funding_kst": ["macro_cp_rate", "macro_ldr_kst"],
    }
    for row in rows:
        ids = validation_map.get(row["metric_id"], [row["metric_id"]])
        matched = [checks.get(metric_id) for metric_id in ids]
        row["source_validation"] = {
            "status": "passed" if matched and all(item and item.get("status") == "passed" for item in matched) else "not_validated",
            "validated_at": validation.get("meta", {}).get("validated_at"),
            "checks": ids,
        }
    order = ["macro_spy_djp", "macro_discretionary_staples", "macro_dxy", "macro_funding_kst", "macro_cpi_18roc", "macro_dividend_yield", "macro_margin_kst", "macro_excess_cape_yield"]
    rows.sort(key=lambda row: order.index(row["metric_id"]))
    return {"meta": {"collected_at": now.isoformat(timespec="seconds"), "timezone": "Asia/Shanghai", "method": "public-source-refresh + Wind-baseline technical transforms", "indicator_count": len(rows), "baseline_generated_at": baseline["meta"].get("generated_at"), "baseline_workbook": baseline["meta"].get("workbook_name"), "source_validation_status": validation.get("meta", {}).get("status"), "source_validation_at": validation.get("meta", {}).get("validated_at")}, "metrics": rows, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    output = root / "data" / "macro_indicators_latest.json"
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"metrics": []}
    try:
        snapshot = collect(root)
    except Exception as exc:
        if not previous.get("metrics"):
            raise
        snapshot = previous
        snapshot["meta"]["last_attempt_at"] = datetime.now(CN_TZ).isoformat(timespec="seconds")
        snapshot["meta"]["status"] = "stale_fallback"
        snapshot["errors"] = [f"{type(exc).__name__}: {exc}"]
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    history = root / "data" / "macro_history.jsonl"
    existing = [line for line in history.read_text(encoding="utf-8").splitlines() if line.strip()] if history.exists() else []
    day = snapshot["meta"].get("collected_at", "")[:10]
    compact = {"date": day, "collected_at": snapshot["meta"].get("collected_at"), "metrics": [{"metric_id": r["metric_id"], "value": r["value"], "risk_percentile": r["risk_percentile"], "period": r["period"]} for r in snapshot["metrics"]]}
    existing = [line for line in existing if json.loads(line).get("date") != day]
    existing.append(json.dumps(compact, ensure_ascii=False))
    history.write_text("\n".join(existing[-800:]) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
