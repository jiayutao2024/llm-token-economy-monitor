# 全球大模型商业化与 Token 经济跟踪

这是一套不需要 API Key 的轻量自动化：

- 每周一、周五抓取官方定价页和行业数据页；
- 对来源页做内容指纹，识别“网页发生变化”；
- 保存每次运行的价格快照，形成价格变化历史；
- 抓取 Google News RSS，生成 ARR、token 用量、价格调整的待复核信号；
- 输出一个自包含的 `dashboard.html`，无需本地服务器即可打开。
- 同步生成可部署的在线 Worker，提供 Dashboard 与 JSON API。

## 运行

在 PowerShell 中：

```powershell
.\run_update.ps1
```

输出：

- `dashboard.html`：最终看板；
- `data/history.jsonl`：每次运行的快照；
- `data/source_state.json`：来源抓取状态与网页指纹；
- `data/news_signals.json`：自动发现的新闻线索；
- `logs/update.log`：运行日志。
- `data/dashboard_api.json`：标准化 API 快照；
- `worker/index.js`：在线 Dashboard 与 API 的部署入口。

## 在线 API

部署后可用：

- `/api/dashboard`：完整快照；
- `/api/pricing`：API token 价格；
- `/api/business`：ARR、年化收入和年度收入；
- `/api/tokens`：token 用量和训练量披露；
- `/api/sources`：来源与抓取状态；
- `/api/signals`：新闻待复核池；
- `/api/history`：价格历史；
- `/api/live/sources`：在线实时检查来源连通性；
- `/health`：服务及快照健康状态。

正式指标是版本化快照；`/api/live/sources` 是实时请求，不会用抓取失败覆盖最近成功快照。

## 定时任务

默认安装为每周一、周五 08:30 运行：

```powershell
.\install_schedule.ps1
```

修改时间：

```powershell
.\install_schedule.ps1 -Time "09:00"
```

计划任务名称：`LLM-Market-Monitor-Mon-Fri`。

## 数据口径

- 价格默认是每百万 tokens 的公开标价；跨币种比较使用配置文件中的固定汇率。
- “混合成本”假设每 100 万总 tokens 中输入占 75%、输出占 25%，仅用于统一比较。
- ARR、annualized revenue、年度收入不是同一口径，看板会分开展示。
- 公司未披露的数据保持“未披露”，不填 0。
- 自动化只把新闻放进“待复核信号”，不会直接把媒体标题中的数字写进正式指标。

## 更新公司或人工复核值

- 公司、来源、运行时间：`config/monitor.json`
- 已复核价格、ARR、token 披露：`data/reported_metrics.json`

修改后重新运行 `run_update.ps1` 即可。
