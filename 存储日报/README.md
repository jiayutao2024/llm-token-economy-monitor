# 存储行业每日情报自动化

本地系统每天北京时间 07:30 运行一次，在国内开盘前汇总上一交易日 A 股、隔夜海外市场和过去 30 小时国内外存储新闻。

## 日报结构

1. 日期、生成时间和今日摘要。
2. 存储产业链结构及当天新闻对各环节的影响。
3. 国内/国外主要摘要。
4. 国内重要新闻、国外重要新闻。
5. 每个事件的中文标题、原文链接、产业环节/方向/地域标签、中文概括和逻辑链判断。
6. 国内外核心标的最近完整交易日收盘价、日涨跌幅和成交量。

新闻先按事件聚类，合并重复转载；默认入选 14 个事件，正常控制在 10–15 个，程序硬上限为 20 个。

## 立即运行

```powershell
py -3 .\scripts\run_storage_intel.py
```

常用参数：

```powershell
py -3 .\scripts\run_storage_intel.py --hours 30 --target-news 14 --max-news 15
```

输出文件：

- `output/latest.html`：最新可视化日报。
- `output/latest.csv`：入选事件结构化明细。
- `data/news.jsonl`：历史新闻库。
- `data/translations.json`：标题与摘要翻译缓存。
- `logs/runs.jsonl`：每次运行状态、数据源异常和入选数量。

## 每日自动调度

注册默认 07:30 任务，同时移除旧的早晚两次任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Register-StorageIntelTasks.ps1
```

自定义时间：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Register-StorageIntelTasks.ps1 -DailyTime '07:45'
```

移除任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Unregister-StorageIntelTasks.ps1
```

任务允许电池供电、错过时点后补跑，运行上限 20 分钟。当前使用交互式 Windows 登录账户。

## 配置

- `config/ontology.json`：产品、产业环节、事件、证据等级、逻辑模板和噪声词。
- `config/entities.json`：公司别名、产业角色和证券代码。
- `config/sources.json`：公开新闻源和来源分层。
- `config/market_watchlist.json`：国内外核心行情标的。

行情暂不使用 Wind 或 AlphaEngine，由 Yahoo Finance 公开 chart 接口获取最近两个有效日收盘价和成交量。不同市场交易日可能不同，日报逐行显示数据日期。

## 覆盖范围

- 产品：HBM、DRAM、NAND、SSD、eMMC/UFS、NOR/EEPROM、主控、CXL/SCM、HDD、磁带/归档、光/磁电存储。
- 产业链：设备材料、原厂制造、封装测试、主控、模组渠道、系统/OEM、终端需求。
- 事件：订单/长协、送样/认证、量产/出货、扩产/Capex、减产/事故、价格、库存/稼动率、业绩/指引、技术路线、政策、并购/IPO、终端出货。
- 证据链：传闻 → 官方发布 → 送样 → 验证 → 合同 → 量产 → 出货/收入。

## 数据边界

- 聚合 RSS 用于发现，重大结论仍需回到公司、监管或交易所一手页面。
- 英文标题和摘要通过公开翻译服务转为中文，并做存储术语校正；机器翻译仍可能存在误差。
- 事件聚类会合并标题相似、实体/产品/技术代际相同的转载，但极少数跨语言报道仍可能需要人工确认。
- Yahoo Finance 属于公开第三方行情，可能延迟或短暂不可用；日报会保留交易日期并报告缺失。
- 本系统用于研究线索、证据分级和逻辑映射，不构成投资建议。
