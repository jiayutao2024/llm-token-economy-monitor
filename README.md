# AI 算力与存储产业双维跟踪

公开站点：

- Dashboard：<https://jiayutao2024.github.io/llm-token-economy-monitor/>
- 存储 Tab：<https://jiayutao2024.github.io/llm-token-economy-monitor/storage/>
- API 目录：<https://jiayutao2024.github.io/llm-token-economy-monitor/api/index.json>

页面不依赖 GPT、登录、VPN、CDN 或第三方前端脚本。GitHub Actions 在服务器端抓取公开来源，读者只访问本站生成的静态 HTML、CSS、JavaScript 和 JSON。

## Dashboard 结构

### AI 与算力

- 浙商研究框架中的总量八大指标；
- “总量拥挤度 × 产业验证度”二维阶段矩阵；
- 国内外大模型 API Token 价格；
- Token 使用量与 ARR/收入披露；
- GPU 单卡租赁价格；
- 北美 CSP Capex 与产业六项验证。

### 存储产业

- DRAM DDR5/DDR4、服务器 RDIMM、GDDR6、NAND MLC/TLC 与 SSD 公开价格、区间和涨跌幅；
- 价格上涨广度与存储周期分，公开快照从上线日起每日积累历史；
- HBM 在无稳定公开绝对报价时仅展示合约方向、晶圆投入与产业证据代理；
- 需求 → bit需求 → 库存 → 供给 → 价格 → 盈利 → Capex 产业链；
- 产品 × 产业环节事件热力图；
- 发布 → 送样 → 验证 → 合同 → 量产 → 收入证据漏斗；
- 国内外核心标的行情；
- 经过去重和零售噪声过滤的公开新闻发现队列。

## 刷新与历史

- 每日北京时间 07:30 自动更新；
- GitHub Actions cron：`30 23 * * *`；
- `data-history` 分支保存 AI、统一指标与存储公开价格历史；
- 单一来源失败时保留最近成功快照并在健康接口中标记。

本地更新：

```powershell
.\run_update.ps1
```

本地安装每日任务：

```powershell
.\install_schedule.ps1 -Time "07:30"
```

## 公共 API

- `/api/dashboard.json`：完整双 Tab 快照；
- `/api/overview.json`：阶段判断和总量八指标；
- `/api/ai-compute.json`：Token、ARR、价格和 Capex；
- `/api/gpu-rental.json`：GPU 租赁价格；
- `/api/storage.json`：存储周期、公开价格、行情和事件；
- `/api/storage-prices.json`：DRAM、NAND、GDDR、SSD 报价与自建历史；
- `/api/market-cycle.json`：阶段分数、阈值和覆盖率；
- `/api/news.json`：事件与待复核池；
- `/api/health.json`：来源、陈旧指标和部署状态；
- `/api/metrics.json`：统一字段的标准化指标；
- 原 `pricing.json`、`business.json`、`tokens.json`、`sources.json`、`signals.json`、`history.json` 保持兼容。

标准化指标字段：

```text
metric_id, value, unit, currency, region, period,
source_name, source_url, source_tier, evidence_status,
collected_at, note
```

## 数据边界

- T1：公司官网、IR、交易所、监管、政府和官方价格页；
- T2：权威媒体和可公开引用的行业研究；
- T3：RSS/聚合新闻，仅用于发现，不自动写入正式指标；
- ARR、年化收入和年度收入不合并；
- 公司、行业、训练和推理 Token 不跨口径求和；
- 按需、合约、竞价和整机 GPU 价格不混排；
- 不上传 Wind、付费 TrendForce 历史表、原始研究 PPT/Word 或内部底稿。
- TrendForce 只采集公开价格页的当前快照；历史由本站每日快照自行积累。

公开页面用于研究线索与证据管理，不构成投资建议。
