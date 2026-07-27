(() => {
  "use strict";
  const root = window.DASHBOARD_ROOT || "./";
  const view = /\/storage\/?$/.test(location.pathname) ? "storage" : "ai";
  const app = document.querySelector("#app");
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
  const n = (value, digits = 1) => Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: digits, maximumFractionDigits: digits
  });
  const compact = value => new Intl.NumberFormat("zh-CN", {
    notation: "compact", maximumFractionDigits: 1
  }).format(Number(value));
  const dateText = value => {
    if (!value) return "未生成";
    const d = new Date(value);
    return Number.isNaN(d.valueOf()) ? value : d.toLocaleString("zh-CN", {hour12:false});
  };
  const sourceLink = (url, label = "来源") => url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)} ↗</a>`
    : `<span class="muted">来源待补</span>`;
  const barList = (rows, value, label, formatter, tone = () => "") => {
    const values = rows.map(value).filter(Number.isFinite);
    const max = Math.max(...values, 1);
    return `<div class="bar-list">${rows.map(row => {
      const v = value(row);
      const width = Number.isFinite(v) ? Math.max(1, v / max * 100) : 0;
      return `<div class="bar-row">
        <div class="bar-label" title="${esc(label(row))}">${esc(label(row))}</div>
        <div class="bar-track"><div class="bar-fill ${esc(tone(row))}" style="width:${width}%"></div></div>
        <div class="bar-value">${esc(formatter(v, row))}</div>
      </div>`;
    }).join("")}</div>`;
  };
  const sectionHead = (title, subtitle, note = "") => `
    <div class="section-head"><div><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div>
    ${note ? `<div class="source-note">${note}</div>` : ""}</div>`;
  const getParam = name => new URL(location.href).searchParams.get(name) || "";
  const setParam = (name, value) => {
    const url = new URL(location.href);
    value ? url.searchParams.set(name, value) : url.searchParams.delete(name);
    history.replaceState({}, "", url);
  };

  function renderHeader(D) {
    document.title = D.meta.title;
    document.querySelector("#page-title").textContent = D.meta.title;
    document.querySelector("#contact-line").textContent = D.meta.contact;
    document.querySelector("#public-policy").textContent = D.meta.public_policy;
    document.querySelector("#header-freshness").innerHTML =
      `<b>最新快照 ${esc(dateText(D.meta.generated_at))}</b>${esc(D.meta.schedule)} · ${esc(D.health.status)}`;
    document.querySelectorAll(".tab").forEach(tab => {
      tab.classList.toggle("active", tab.dataset.view === view);
    });
  }

  function stageHero(D) {
    const s = D.overview.stage;
    const x = Math.max(0, Math.min(100, Number(s.macro_score || 0)));
    const y = Math.max(0, Math.min(100, Number(s.industry_score || 0)));
    return `<section class="section hero-grid">
      <article class="card stage-card">
        <span class="stage-label">当前阶段 · ${esc(s.stage_short)}</span>
        <h2>${esc(s.stage_label)}</h2>
        <p class="lead">${esc(s.rationale)}</p>
        <p class="muted">研究基准：${esc(s.research_baseline?.stage_label || "—")}（${esc(s.research_baseline?.as_of || "—")}）</p>
        <div class="coverage">
          <span><b>${esc(s.macro_score ?? "—")}</b>总量拥挤度 / 100</span>
          <span><b>${esc(s.industry_score ?? "—")}</b>产业验证度 / 100</span>
          <span><b>${esc(s.macro_coverage)}</b>总量覆盖</span>
          <span><b>${esc(s.industry_coverage)}</b>产业覆盖</span>
        </div>
      </article>
      <article class="card matrix-card">
        <h3>阶段二维矩阵</h3><p class="subtitle">横轴：流动性/估值/拥挤度；纵轴：商业化与盈利兑现</p>
        <div class="matrix">
          <span class="matrix-label top">产业验证强</span><span class="matrix-label bottom">产业验证弱</span>
          <span class="matrix-label left">总量风险低</span><span class="matrix-label right">总量风险高</span>
          <span class="matrix-point" data-label="${esc(s.stage_short)}" style="left:${x}%;bottom:${y}%"></span>
        </div>
      </article>
    </section>`;
  }

  function macroSection(D) {
    const rows = D.overview.macro_indicators || [];
    return `<section class="section">
      ${sectionHead("总量八大指标", "风险分位越高，代表流动性、估值或交易拥挤度越值得警惕。", "研究基准与公开代理口径")}
      <div class="grid-2">
        <article class="card pad">
          ${rows.map(row => `<div class="risk-row">
            <div class="risk-name"><b>${esc(row.name)}</b><small>${esc(row.family)} · ${esc(row.period)}</small></div>
            <div class="track"><div class="fill ${Number(row.risk_percentile) < 45 ? "open" : ""}" style="width:${Number(row.risk_percentile) || 0}%"></div></div>
            <div class="value">${esc(row.risk_percentile)}%</div>
          </div>`).join("")}
        </article>
        <article class="card pad">
          <h3>口径与可用性</h3>
          <div class="table-wrap"><table><thead><tr><th>指标</th><th>当前值</th><th>证据</th><th>来源</th></tr></thead><tbody>
          ${rows.map(row => `<tr><td>${esc(row.name)}</td><td class="num">${esc(row.value)} ${esc(row.unit)}</td>
          <td><span class="tag ${row.source_tier === 1 ? "red" : ""}">T${esc(row.source_tier)} · ${esc(row.evidence_status)}</span></td>
          <td>${sourceLink(row.source_url, row.source_name)}</td></tr>`).join("")}
          </tbody></table></div>
        </article>
      </div>
    </section>`;
  }

  function industrySignals(D) {
    const rows = D.overview.industry_signals || [];
    return `<section class="section">
      ${sectionHead("产业六项验证", "验证上游算力紧缺是否与中下游Token消费、商业化和盈利形成闭环。")}
      <div class="signal-grid">${rows.map(row => `<article class="card signal-card">
        <b>${esc(row.name)}</b><strong>${esc(row.score)}</strong>
        <span class="tag red">${esc(row.direction)}</span><small>${esc(row.period)} · T${esc(row.source_tier)}</small>
        <p class="source-note">${esc(row.note)}</p>
      </article>`).join("")}</div>
    </section>`;
  }

  function aiKpis(D) {
    const pricing = D.ai_compute.pricing || [];
    const gpu = D.gpu_rental.rows || [];
    const tokens = D.ai_compute.tokens || [];
    const arr = (D.ai_compute.business || []).filter(row => ["arr","annualized_revenue"].includes(row.metric));
    const cheapest = pricing.length ? Math.min(...pricing.map(x => Number(x.blended_cost_usd))) : null;
    const gpuMedian = gpu.length ? [...gpu.map(x => Number(x.usd_per_gpu_hour)).filter(Number.isFinite)].sort((a,b)=>a-b)[Math.floor(gpu.length/2)] : null;
    const tokenMax = tokens.length ? Math.max(...tokens.map(x => Number(x.value_t) || 0)) : null;
    return `<section class="section">
      <div class="kpi-grid">
        <article class="card kpi"><div class="kpi-label">API混合成本下沿</div><div class="kpi-value">${cheapest == null ? "—" : "$"+n(cheapest,3)}</div><div class="kpi-meta">USD / 百万总Tokens</div></article>
        <article class="card kpi"><div class="kpi-label">GPU价格样本中位数</div><div class="kpi-value">${gpuMedian == null ? "—" : "$"+n(gpuMedian,2)}</div><div class="kpi-meta">USD / 单卡·小时</div></article>
        <article class="card kpi"><div class="kpi-label">最大公开Token披露</div><div class="kpi-value">${tokenMax == null ? "—" : compact(tokenMax)+"T"}</div><div class="kpi-meta">保留原始口径，不跨公司求和</div></article>
        <article class="card kpi"><div class="kpi-label">ARR/年化收入披露</div><div class="kpi-value">${arr.length}</div><div class="kpi-meta">年度收入另行标注</div></article>
      </div>
    </section>`;
  }

  function pricingGpu(D) {
    const pricing = [...(D.ai_compute.pricing || [])].sort((a,b)=>a.blended_cost_usd-b.blended_cost_usd);
    const gpu = [...(D.gpu_rental.rows || [])].sort((a,b)=>a.usd_per_gpu_hour-b.usd_per_gpu_hour);
    return `<section class="section">
      ${sectionHead("Token价格与GPU租金", "统一展示单位，但不把不同性能、计费类型和服务等级视为同质商品。", esc(D.gpu_rental.meta.comparability))}
      <div class="grid-2">
        <article class="card pad"><h3>API混合成本</h3><p class="subtitle">75%输入 + 25%输出，USD/百万总Tokens</p>
          ${barList(pricing, x=>Number(x.blended_cost_usd), x=>`${x.company} · ${x.model}`, v=>"$"+n(v,2), x=>x.region==="国内"?"grey":"")}
        </article>
        <article class="card pad"><h3>GPU公开按需/资源价格</h3><p class="subtitle">USD/单卡·小时；阿里云样本不含CPU、内存与网络</p>
          ${barList(gpu, x=>Number(x.usd_per_gpu_hour), x=>`${x.provider} · ${x.gpu}`, v=>"$"+n(v,2), x=>x.region.includes("中国")?"grey":"")}
        </article>
      </div>
      <article class="card pad" style="margin-top:14px">
        <div class="controls"><select id="price-region"><option value="">全部地区</option><option value="国内">国内</option><option value="海外">海外</option></select>
        <input id="price-search" type="search" placeholder="搜索公司或模型"></div>
        <div class="table-wrap"><table id="pricing-table"><thead><tr><th data-key="region">地区</th><th data-key="company">公司/模型</th><th>档位</th><th data-key="input_per_m">输入</th><th data-key="output_per_m">输出</th><th data-key="blended_cost_usd">混合成本</th><th>证据/来源</th></tr></thead><tbody></tbody></table></div>
      </article>
    </section>`;
  }

  function businessTokenCapex(D) {
    const business = D.ai_compute.business || [];
    const tokens = D.ai_compute.tokens || [];
    const capex = (D.ai_compute.csp_capex || []).filter(x => x.year === "2026E");
    return `<section class="section">
      ${sectionHead("商业化、Token用量与Capex", "ARR、年化收入、年度收入和不同Token口径分开呈现。")}
      <div class="grid-3">
        <article class="card pad"><h3>ARR/收入公开值</h3><p class="subtitle">十亿美元，标签保留原始口径</p>
          ${barList([...business].sort((a,b)=>b.value_usd_b-a.value_usd_b), x=>Number(x.value_usd_b), x=>x.company, v=>"$"+n(v,2)+"B")}
        </article>
        <article class="card pad"><h3>Token披露</h3><p class="subtitle">数值按原披露单位；不做跨口径合计</p>
          ${tokens.map(x=>`<p><b>${esc(x.company)}</b><br><span class="kpi-value">${esc(x.value_t)}T</span> <span class="muted">${esc(x.unit)}</span><br><span class="source-note">${esc(x.period)} · ${sourceLink(x.source_url,x.source_name)}</span></p>`).join("")}
        </article>
        <article class="card pad"><h3>北美CSP 2026E Capex</h3><p class="subtitle">十亿美元；研究框架中的公开指引代理</p>
          ${barList(capex, x=>Number(x.value_usd_b), x=>x.company, v=>"$"+n(v,0)+"B", ()=>"grey")}
        </article>
      </div>
    </section>`;
  }

  function methodology(D) {
    return `<section class="section">
      ${sectionHead("来源、方法与健康度", "每个正式指标保留来源等级；新闻只进入待复核池。")}
      <div class="grid-2">
        <article class="card pad"><h3>运行健康</h3>
          <p><b>${esc(D.health.status)}</b> · AI来源 ${esc(D.health.ai_sources_ok)}/${esc(D.health.ai_sources_total)} · 存储 ${esc(D.health.storage_status)}</p>
          <p class="source-note">陈旧总量指标：${esc((D.health.stale_macro_metrics || []).join("、") || "无")}</p>
          <p>${esc(D.meta.fx_note)}</p>
        </article>
        <article class="card pad"><h3>阶段阈值</h3>
          ${Object.entries(D.market_cycle.thresholds || {}).map(([k,v])=>`<p><span class="tag red">${esc(k)}</span> ${esc(v)}</p>`).join("")}
        </article>
      </div>
    </section>`;
  }

  function aiView(D) {
    app.innerHTML = stageHero(D) + aiKpis(D) + macroSection(D) + industrySignals(D) +
      pricingGpu(D) + businessTokenCapex(D) + methodology(D);
    wirePricing(D);
    makeSortable();
  }

  function storageKpis(D) {
    const S = D.storage;
    const Q = S.daily.quality || {};
    return `<section class="section">
      <div class="kpi-grid">
        <article class="card kpi"><div class="kpi-label">存储周期状态</div><div class="kpi-value">${esc(S.cycle.label)}</div><div class="kpi-meta">公开量价与事件综合</div></article>
        <article class="card kpi"><div class="kpi-label">价格/供需信号</div><div class="kpi-value">${esc(S.cycle.signal_count)}</div><div class="kpi-meta">DRAM/NAND/HBM/eSSD</div></article>
        <article class="card kpi"><div class="kpi-label">高质量事件</div><div class="kpi-value">${esc(S.cycle.event_count)}</div><div class="kpi-meta">过去 ${esc(S.daily.meta?.lookback_hours || "—")} 小时</div></article>
        <article class="card kpi"><div class="kpi-label">行情覆盖</div><div class="kpi-value">${esc(S.cycle.market_count)}</div><div class="kpi-meta">源错误 ${esc(Q.source_errors || 0)} 个</div></article>
      </div>
    </section>`;
  }

  function storagePricesChain(D) {
    const S = D.storage;
    return `<section class="section">
      ${sectionHead("存储量价与产业链", "价格方向来自可公开引用的新闻稿；不转载付费历史表。")}
      <div class="grid-2">
        <article class="card pad"><h3>价格与供需方向</h3>
          <div class="grid-2">${S.price_signals.map(x=>`<div>
            <span class="tag red">${esc(x.product)}</span><h3>${esc(x.direction)}</h3>
            <p>${esc(x.change_range)}</p><p class="source-note">${esc(x.period)} · ${sourceLink(x.source_url,x.source_name)}</p>
          </div>`).join("")}</div>
        </article>
        <article class="card pad"><h3>研究主链</h3><p class="subtitle">从需求到设备材料订单的二阶映射</p>
          <div class="chain">${S.chain.map((x,i)=>`${i?'<span class="chain-arrow">→</span>':''}<div class="chain-node">${esc(x)}</div>`).join("")}</div>
        </article>
      </div>
    </section>`;
  }

  function heatmapAndFunnel(D) {
    const daily = D.storage.daily || {};
    const productCounts = daily.summary?.product_counts || {};
    const layerCounts = daily.summary?.layer_counts || {};
    const products = ["HBM","DRAM","NAND","SSD"];
    const layers = ["设备材料","原厂制造","封装测试/主控","模组/渠道","系统/终端需求"];
    const events = daily.events || [];
    const matrix = {};
    events.forEach(e => (e.products || []).forEach(p => {
      matrix[p] ||= {};
      matrix[p][e.stage_zh] = (matrix[p][e.stage_zh] || 0) + 1;
    }));
    const evidenceOrder = [
      ["1_Rumor","传闻"],["2_Announcement","官方发布"],["3_Sample","送样"],
      ["4_Qualification","验证"],["5_Contract","合同"],["6_MassProduction","量产"],["7_ShipmentRevenue","出货/收入"]
    ];
    const evidence = daily.summary?.evidence_counts || {};
    const max = Math.max(...evidenceOrder.map(x=>Number(evidence[x[0]])||0),1);
    return `<section class="section">
      ${sectionHead("事件结构与证据兑现", "产品×产业环节用于定位影响面；证据漏斗防止把发布、送样直接等同收入。")}
      <div class="grid-2">
        <article class="card pad"><h3>产品 × 产业环节热力图</h3><p class="subtitle">格内为本轮入选事件数</p>
          <div class="heatmap"><div class="heat-cell head">产品</div>${layers.map(x=>`<div class="heat-cell head">${esc(x)}</div>`).join("")}
          ${products.map(p=>`<div class="heat-cell head">${esc(p)} (${esc(productCounts[p]||0)})</div>${layers.map(l=>{
            const v=matrix[p]?.[l]||0; return `<div class="heat-cell ${v>=3?"hot":v?"warm":"zero"}">${v||"—"}</div>`;
          }).join("")}`).join("")}</div>
          <p class="source-note">产业环节总计：${esc(Object.entries(layerCounts).map(([k,v])=>`${k}${v}`).join(" · ") || "暂无")}</p>
        </article>
        <article class="card pad"><h3>HBM/存储证据漏斗</h3><p class="subtitle">越接近出货收入，基本面兑现度越高</p>
          <div class="funnel">${evidenceOrder.map(([key,label])=>{
            const v=Number(evidence[key])||0; return `<div class="funnel-row"><span>${esc(label)}</span><div class="funnel-bar" style="width:${Math.max(v/max*100,v?5:0)}%"></div><b>${v}</b></div>`;
          }).join("")}</div>
        </article>
      </div>
    </section>`;
  }

  function marketAndNews(D) {
    const market = D.storage.daily.market || [];
    const events = D.storage.daily.events || [];
    return `<section class="section">
      ${sectionHead("核心标的与事件", "行情使用最近两个有效收盘价；新闻经过去重和零售噪声过滤。")}
      <div class="grid-2">
        <article class="card pad"><h3>核心标的最近完整交易日</h3>
          <div class="table-wrap"><table><thead><tr><th data-key="group">地区</th><th data-key="name">标的</th><th>产业定位</th><th data-key="price">收盘</th><th data-key="change_pct">日涨跌</th><th data-key="trade_date">交易日</th></tr></thead><tbody>
          ${market.map(x=>`<tr><td><span class="tag">${esc(x.group)}</span></td><td>${sourceLink(x.source_url,x.name)}<br><small>${esc(x.symbol)}</small></td><td>${esc(x.role)}</td><td class="num">${esc(x.price)} ${esc(x.currency)}</td><td class="num">${Number(x.change_pct)>=0?"+":""}${esc(x.change_pct)}%</td><td>${esc(x.trade_date)}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">行情源暂未返回有效数据</td></tr>`}
          </tbody></table></div>
        </article>
        <article class="card pad"><h3>国内/国外重点事件</h3>
          <div class="controls"><input id="news-search" type="search" placeholder="搜索产品、公司或事件"><select id="news-region"><option value="">全部地区</option><option value="国内">国内</option><option value="国外">国外</option></select></div>
          <div class="news-list" id="storage-news"></div>
        </article>
      </div>
    </section>`;
  }

  function storageQuality(D) {
    const Q = D.storage.daily.quality || {};
    return `<section class="section">
      ${sectionHead("来源与数据质量", "RSS只负责发现，正式量价和收入必须回到一手来源。")}
      <article class="card pad">
        <p><span class="tag red">${esc(Q.status)}</span> 入选事件 ${esc(Q.selected_events || 0)} · 行情 ${esc(Q.market_quotes || 0)} · 来源错误 ${esc(Q.source_errors || 0)}</p>
        <p>${esc(Q.noise_policy || "")}</p>
        ${(Q.errors || []).length ? `<details><summary>查看来源异常</summary><ul>${Q.errors.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></details>` : `<p class="source-note">本轮未记录来源异常。</p>`}
      </article>
    </section>`;
  }

  function storageView(D) {
    app.innerHTML = storageKpis(D) + storagePricesChain(D) + heatmapAndFunnel(D) +
      marketAndNews(D) + storageQuality(D);
    wireNews(D);
    makeSortable();
  }

  function wirePricing(D) {
    const region = document.querySelector("#price-region");
    const search = document.querySelector("#price-search");
    const tbody = document.querySelector("#pricing-table tbody");
    region.value = getParam("region");
    search.value = getParam("q");
    const render = () => {
      const q = search.value.trim().toLowerCase();
      const rows = (D.ai_compute.pricing || []).filter(x =>
        (!region.value || x.region === region.value) &&
        (!q || `${x.company} ${x.model}`.toLowerCase().includes(q))
      );
      tbody.innerHTML = rows.map(x=>`<tr><td><span class="tag">${esc(x.region)}</span></td><td><b>${esc(x.company)}</b><br>${esc(x.model)}</td><td>${esc(x.tier)}</td>
      <td class="num">${esc(x.currency)} ${n(x.input_per_m,3)}</td><td class="num">${esc(x.currency)} ${n(x.output_per_m,3)}</td><td class="num">$${n(x.blended_cost_usd,3)}</td>
      <td><span class="tag ${x.evidence==="官方"?"red":""}">${esc(x.evidence)}</span><br><small>${esc(x.source_check)}</small></td></tr>`).join("") ||
      `<tr><td colspan="7" class="empty">没有匹配数据</td></tr>`;
      setParam("region", region.value); setParam("q", search.value.trim());
    };
    region.addEventListener("change", render); search.addEventListener("input", render); render();
  }

  function wireNews(D) {
    const search = document.querySelector("#news-search");
    const region = document.querySelector("#news-region");
    const target = document.querySelector("#storage-news");
    search.value = getParam("q"); region.value = getParam("region");
    const render = () => {
      const q = search.value.trim().toLowerCase();
      const rows = (D.storage.daily.events || []).filter(x =>
        (!region.value || x.geo_tag === region.value) &&
        (!q || `${x.title_zh} ${(x.products||[]).join(" ")} ${(x.entities||[]).join(" ")}`.toLowerCase().includes(q))
      );
      target.innerHTML = rows.slice(0,15).map(x=>`<article class="card news-item">
        <a class="news-title" href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title_zh)}</a>
        <div class="news-meta">${esc(x.published_at?.slice(0,16).replace("T"," "))} · T${esc(x.source_tier)} · ${esc(x.publisher)} · ${esc(x.stage_zh)} · 证据 ${esc(x.evidence_stage)}</div>
        <div>${(x.products||[]).map(p=>`<span class="tag red">${esc(p)}</span>`).join(" ")} <span class="tag">${esc(x.sentiment)}</span></div>
        <p class="news-brief">${esc(x.brief_zh)}</p>
      </article>`).join("") || `<div class="empty">没有匹配的高质量事件</div>`;
      setParam("region", region.value); setParam("q", search.value.trim());
    };
    search.addEventListener("input",render);region.addEventListener("change",render);render();
  }

  function makeSortable() {
    document.querySelectorAll("th[data-key]").forEach(th => th.addEventListener("click", () => {
      const table = th.closest("table"), body = table.tBodies[0];
      const index = [...th.parentNode.children].indexOf(th);
      const asc = th.dataset.direction !== "asc";
      th.dataset.direction = asc ? "asc" : "desc";
      [...body.rows].sort((a,b)=>{
        const av=a.cells[index]?.textContent.trim()||"", bv=b.cells[index]?.textContent.trim()||"";
        const an=parseFloat(av.replace(/[^\d.-]/g,"")), bn=parseFloat(bv.replace(/[^\d.-]/g,""));
        const cmp=Number.isFinite(an)&&Number.isFinite(bn)?an-bn:av.localeCompare(bv,"zh-CN");
        return asc?cmp:-cmp;
      }).forEach(row=>body.appendChild(row));
    }));
  }

  fetch(`${root}api/dashboard.json`, {cache:"no-store"})
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(D => {
      renderHeader(D);
      view === "storage" ? storageView(D) : aiView(D);
    })
    .catch(error => {
      app.innerHTML = `<section class="card error"><h2>快照暂时不可用</h2><p>${esc(error.message)}</p><p><a href="${esc(root)}api/health.json">查看健康接口</a></p></section>`;
    });
})();
