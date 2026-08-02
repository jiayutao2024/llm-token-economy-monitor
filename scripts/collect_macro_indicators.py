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
import html
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
UA = "Mozilla/5.0 (compatible; ZheshangPublicResearchMonitor/2.0)"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=10y&interval=1d&events=history"
CP_ZIP = "https://www.federalreserve.gov/releases/cp/data/FRB_CP_xml.zip"
H8_CSV = "https://www.federalreserve.gov/datadownload/Output.aspx?filetype=csv&from=&label=include&lastobs=&layout=seriescolumn&rel=H8&series=c8dfa96ef1d2db40ce57121ffdddf59d&to=&type=package"
BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0?startyear={start}&endyear={end}"
FINRA_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"


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


def kst_series(values: list[float]) -> list[float | None]:
    """Classic KST: ROC(10,15,20,30), smoothed 10/10/10/15, weights 1..4."""
    rocs: list[list[float | None]] = []
    for period in (10, 15, 20, 30):
        row: list[float | None] = []
        for index, value in enumerate(values):
            row.append(None if index < period or values[index - period] == 0 else 100 * (value / values[index - period] - 1))
        rocs.append(row)
    result: list[float | None] = []
    for index in range(len(values)):
        parts = [sma(rocs[i], index, length) for i, length in enumerate((10, 10, 10, 15))]
        result.append(None if any(v is None for v in parts) else sum((i + 1) * float(v) for i, v in enumerate(parts)))
    return result


def yahoo_series(ticker: str) -> tuple[list[str], list[float]]:
    data = json.loads(fetch(YAHOO.format(ticker=urllib.parse.quote(ticker))).decode("utf-8"))
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"].get("adjclose", result["indicators"]["quote"])[0]
    closes = quote.get("adjclose", quote.get("close"))
    pairs = [(datetime.fromtimestamp(ts, timezone.utc).date().isoformat(), float(v)) for ts, v in zip(timestamps, closes) if v is not None]
    return [x[0] for x in pairs], [x[1] for x in pairs]


def aligned_ratio(left: tuple[list[str], list[float]], right: tuple[list[str], list[float]]) -> tuple[list[str], list[float]]:
    rmap = dict(zip(*right))
    pairs = [(d, value / rmap[d]) for d, value in zip(*left) if d in rmap and rmap[d]]
    return [x[0] for x in pairs], [x[1] for x in pairs]


def market_metric(metric_id: str, name: str, family: str, ticker_a: str, ticker_b: str | None, unit: str, note: str) -> dict:
    dates, values = yahoo_series(ticker_a)
    if ticker_b:
        dates, values = aligned_ratio((dates, values), yahoo_series(ticker_b))
    dates, values = trailing(dates, values)
    return metric(metric_id, name, family, values[-1], unit, percentile_rank(values), dates[-1], "日频（交易日）",
                  "Yahoo Finance 公开行情", f"https://finance.yahoo.com/quote/{ticker_a}/", 2, "public_proxy", len(values), dates[0], note)


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
    loan = header.index("B1020NCBD")
    deposits = header.index("B1151NCBD")
    pairs = []
    for row in rows[6:]:
        try:
            pairs.append((row[0][:10], 100 * float(row[loan]) / float(row[deposits])))
        except (ValueError, IndexError, ZeroDivisionError):
            continue
    return trailing([x[0] for x in pairs], [x[1] for x in pairs])


def funding_metric() -> dict:
    cp_dates, cp_values = parse_cp()
    ratio_dates, ratio_values = parse_h8_ratio()
    kst = kst_series(ratio_values)
    valid = [(d, v) for d, v in zip(ratio_dates, kst) if v is not None]
    kst_dates, kst_values = [x[0] for x in valid], [float(x[1]) for x in valid]
    risk = (percentile_rank(cp_values) + percentile_rank(kst_values)) / 2
    period = max(cp_dates[-1], ratio_dates[-1])
    return metric("macro_funding_kst", "商业票据利率 + 贷存比 KST", "流动性", cp_values[-1], "% / KST", risk,
                  period, "周频", "美联储 CP 与 H.8", "https://www.federalreserve.gov/releases/cp/", 1, "official_calculated",
                  min(len(cp_values), len(kst_values)), max(cp_dates[0], kst_dates[0]),
                  "90天 AA 非金融商业票据利率与商业银行贷存比 KST 的风险分位均值；两项均为美联储公开代理口径。",
                  secondary_value=round(kst_values[-1], 4), secondary_period=kst_dates[-1], calculation="mean(percentile(CP90), percentile(KST(loan/deposit)))")


def cpi_metric(now: datetime) -> dict:
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
                pairs.append((f'{row["year"]}-{int(row["period"][1:]):02d}-01', float(row["value"])))
            except ValueError:
                continue
    pairs.sort()
    rocs = [(pairs[i][0], 100 * (pairs[i][1] / pairs[i - 18][1] - 1)) for i in range(18, len(pairs))]
    dates, values = [x[0] for x in rocs], [x[1] for x in rocs]
    dates, values = trailing(dates, values)
    return metric("macro_cpi_18roc", "CPI 18个月 ROC", "通胀", values[-1], "%", percentile_rank(values), dates[-1], "月频",
                  "美国劳工统计局 BLS", "https://www.bls.gov/cpi/data.htm", 1, "official_calculated", len(values), dates[0],
                  "CPI-U 经季调指数的18个月变化率，反映通胀趋势而非当月同比。", calculation="CPI(t)/CPI(t-18m)-1")


def multpl_series(slug: str, percent: bool = False) -> tuple[list[str], list[float]]:
    page = fetch(f"https://www.multpl.com/{slug}/table/by-month", 60).decode("utf-8", "ignore")
    matches = re.findall(r"<td[^>]*>([^<]+)</td>\s*<td[^>]*>(.*?)</td>", page, re.S | re.I)
    pairs = []
    for date_text, value_html in matches:
        try:
            date = datetime.strptime(html.unescape(date_text).strip(), "%b %d, %Y").date().isoformat()
            value = float(re.sub(r"[^0-9.\-]", "", html.unescape(re.sub(r"<[^>]+>", "", value_html))))
            pairs.append((date, value))
        except ValueError:
            continue
    pairs.sort()
    return trailing([x[0] for x in pairs], [x[1] for x in pairs])


def treasury_real_yield(start_year: int, end_year: int) -> dict[str, tuple[str, float]]:
    monthly: dict[str, tuple[str, float]] = {}
    for year in range(start_year, end_year + 1):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_real_yield_curve&"
               f"field_tdr_date_value={year}&page&_format=csv")
        for row in csv.DictReader(io.StringIO(fetch(url, 45).decode("utf-8-sig"))):
            try:
                date = datetime.strptime(row["Date"], "%m/%d/%Y").date().isoformat()
                key = date[:7]
                if key not in monthly or date > monthly[key][0]:
                    monthly[key] = (date, float(row["10 YR"]))
            except (ValueError, KeyError):
                continue
    return monthly


def shiller_metrics() -> list[dict]:
    ddates, dvalues = multpl_series("s-p-500-dividend-yield", percent=True)
    cdates, capes = multpl_series("shiller-pe")
    real_yields = treasury_real_yield(int(cdates[0][:4]), int(cdates[-1][:4]))
    excess = [(d, 100 / cape - real_yields[d[:7]][1]) for d, cape in zip(cdates, capes) if cape and d[:7] in real_yields]
    edates, evalues = [x[0] for x in excess], [x[1] for x in excess]
    return [
        metric("macro_dividend_yield", "股息率", "估值", dvalues[-1], "%", 100 - percentile_rank(dvalues), ddates[-1], "月频",
               "Multpl 标普500股息率公开月表", "https://www.multpl.com/s-p-500-dividend-yield/table/by-month", 2, "public_snapshot", len(dvalues), ddates[0],
               "标普500股息率公开月度序列；低股息率对应高估值风险，风险分位反向计算。", calculation="100 - percentile(S&P 500 dividend yield)"),
        metric("macro_excess_cape_yield", "超额 CAPE 收益率", "估值", evalues[-1], "%", 100 - percentile_rank(evalues), edates[-1], "月频",
               "Multpl CAPE + 美国财政部实际收益率", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates", 2, "public_calculated", len(evalues), edates[0],
               "CAPE 收益率减美国财政部10年期实际收益率；CAPE月表为公开二级源，实际收益率为官方一级源。", calculation="100/CAPE - Treasury 10Y real yield")
    ]


def finra_metric() -> dict:
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
            pairs.append((text + "-01", float(value)))
    pairs.sort()
    dates, balances = trailing([x[0] for x in pairs], [x[1] for x in pairs])
    kst = kst_series(balances)
    valid = [(d, v) for d, v in zip(dates, kst) if v is not None]
    kdates, values = [x[0] for x in valid], [float(x[1]) for x in valid]
    return metric("macro_margin_kst", "保证金余额 KST", "杠杆资金", values[-1], "KST", percentile_rank(values), kdates[-1], "月频",
                  "FINRA 保证金统计", "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics", 1, "official_calculated",
                  len(values), kdates[0], "FINRA 客户证券保证金账户借方余额的经典 KST 动量；高分位代表杠杆资金拥挤。",
                  underlying_value=round(balances[-1], 0), underlying_unit="USD million", calculation="KST(FINRA margin debit balance)")


def collect(now: datetime | None = None) -> dict:
    now = now or datetime.now(CN_TZ)
    rows = [
        market_metric("macro_spy_djp", "SPY / DJP", "跨资产", "SPY", "DJP", "倍", "股票相对商品的公开 ETF 代理；高分位代表成长交易拥挤度提高。"),
        market_metric("macro_discretionary_staples", "可选消费 / 必需消费", "风险偏好", "XLY", "XLP", "倍", "XLY/XLP 代理风险消费相对防御消费。"),
        market_metric("macro_dxy", "美元流动性", "流动性", "DX-Y.NYB", None, "指数点", "美元指数公开行情代理；美元走强通常对应全球美元流动性收紧。"),
        funding_metric(), cpi_metric(now), *shiller_metrics(), finra_metric(),
    ]
    order = ["macro_spy_djp", "macro_discretionary_staples", "macro_dxy", "macro_funding_kst", "macro_cpi_18roc", "macro_dividend_yield", "macro_margin_kst", "macro_excess_cape_yield"]
    rows.sort(key=lambda row: order.index(row["metric_id"]))
    return {"meta": {"collected_at": now.isoformat(timespec="seconds"), "timezone": "Asia/Shanghai", "method": "public-source-refresh", "indicator_count": len(rows)}, "metrics": rows, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    output = root / "data" / "macro_indicators_latest.json"
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"metrics": []}
    try:
        snapshot = collect()
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
