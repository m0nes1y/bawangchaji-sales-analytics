/**
 * 霸王茶姬销量数据分析 - ECharts 图表 + 交互筛选 + 分析结论
 */

const purple = "#7c3aed";
const orange = "#f97316";
const palette = ["#7c3aed", "#f97316", "#06b6d4", "#10b981", "#f43f5e", "#8b5cf6", "#eab308", "#ec4899", "#14b8a6", "#6366f1"];

const state = { city: "", category: "", product: "", month: "" };
const charts = {}; // id -> echarts 实例
let productMetric = "qty"; // 产品排名图指标：qty=销量, revenue=营收

function fmt(n) { return n == null ? "--" : Number(n).toLocaleString("zh-CN"); }

function getChart(id) {
    if (charts[id]) charts[id].dispose();
    const c = echarts.init(document.getElementById(id));
    charts[id] = c;
    return c;
}
window.addEventListener("resize", () => Object.values(charts).forEach(c => c.resize()));

async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
}

function qp() {
    const p = new URLSearchParams();
    if (state.city) p.set("city", state.city);
    if (state.category) p.set("category", state.category);
    if (state.product) p.set("product", state.product);
    if (state.month) p.set("month", state.month);
    const s = p.toString();
    return s ? "?" + s : "";
}

// ── KPI ────────────────────────────────────────
async function loadKPI() {
    const d = await getJSON("/api/summary" + qp());
    document.querySelector("#kpi-qty .kpi-value").textContent = fmt(d.total_qty);
    document.querySelector("#kpi-rev .kpi-value").textContent = fmt(d.total_revenue);
    document.querySelector("#kpi-records .kpi-value").textContent = fmt(d.total_records);
    document.querySelector("#kpi-member .kpi-value").textContent =
        d.avg_member != null ? d.avg_member.toFixed(1) + "%" : "--";
}

// ── 月度趋势 ──────────────────────────────────
async function loadMonthly() {
    const d = await getJSON("/api/monthly" + qp());
    const c = getChart("chart-monthly");
    c.setOption({
        tooltip: {
            trigger: "axis",
            formatter: p => {
                const row = d[p[0].dataIndex];
                let s = row.month + "<br/>";
                s += "销量: " + Number(row.qty).toLocaleString("zh-CN") + " 杯<br/>";
                s += "营收: ¥" + Number(row.revenue).toLocaleString("zh-CN") + "<br/>";
                if (row.mom != null) s += "环比: " + (row.mom > 0 ? "+" : "") + row.mom + "%";
                return s;
            }
        },
        legend: { data: ["销量(杯)", "营收(元)"] },
        xAxis: { type: "category", data: d.map(r => r.month), axisLabel: { rotate: 30 } },
        yAxis: [{ type: "value", name: "销量" }, { type: "value", name: "营收" }],
        series: [
            { name: "销量(杯)", type: "line", data: d.map(r => r.qty), smooth: true, itemStyle: { color: purple } },
            { name: "营收(元)", type: "bar", yAxisIndex: 1, data: d.map(r => r.revenue), itemStyle: { color: orange }, barWidth: 24 }
        ],
        grid: { left: 60, right: 60, bottom: 50, top: 50 }
    });
}

// ── 产品排名（销量/营收 切换）──────────────────
async function loadProduct() {
    const d = await getJSON("/api/product_rank" + qp());
    const c = getChart("chart-product");
    const isRev = productMetric === "revenue";
    const key = isRev ? "revenue" : "qty";
    const name = isRev ? "营收(元)" : "销量(杯)";
    const sorted = [...d].sort((a, b) => a[key] - b[key]); // 升序，横向条形从下往上排
    const rows = sorted.map(r => r.product);
    const vals = sorted.map(r => r[key]);
    c.setOption({
        tooltip: {
            trigger: "axis",
            axisPointer: { type: "shadow" },
            formatter: p => {
                const r = sorted[p[0].dataIndex];
                return `${r.product}<br/>销量: ${Number(r.qty).toLocaleString("zh-CN")} 杯<br/>营收: ¥${Number(r.revenue).toLocaleString("zh-CN")}`;
            }
        },
        xAxis: { type: "value", axisLabel: { formatter: isRev ? v => "¥" + (v / 10000).toFixed(0) + "万" : v => v.toLocaleString("zh-CN") } },
        yAxis: { type: "category", data: rows, axisLabel: { width: 90, overflow: "truncate" } },
        series: [{
            type: "bar", data: vals, itemStyle: { color: isRev ? orange : purple }, barWidth: 18,
            label: { show: true, position: "right", formatter: p => isRev ? "¥" + Number(p.value).toLocaleString("zh-CN") : Number(p.value).toLocaleString("zh-CN") }
        }],
        grid: { left: 110, right: 110, top: 10, bottom: 20 }
    });
}

function initProductToggle() {
    const box = document.getElementById("product-metric-toggle");
    if (!box) return;
    box.addEventListener("click", e => {
        const btn = e.target.closest("button[data-metric]");
        if (!btn) return;
        productMetric = btn.dataset.metric;
        box.querySelectorAll("button").forEach(b => b.classList.toggle("active", b === btn));
        loadProduct();
    });
}

// ── 类别饼图 ──────────────────────────────────
async function loadCategory() {
    const d = await getJSON("/api/category_pie" + qp());
    const c = getChart("chart-category");
    c.setOption({
        tooltip: { trigger: "item", formatter: "{b}: {c}杯 ({d}%)" },
        series: [{
            type: "pie", radius: ["40%", "70%"], center: ["50%", "55%"],
            data: d.map((r, i) => ({ name: r.category, value: r.qty, itemStyle: { color: palette[i] } })),
            label: { formatter: "{b}\n{d}%" }
        }]
    });
}

// ── 城市排名 ──────────────────────────────────
async function loadCity() {
    const d = await getJSON("/api/city_rank" + qp());
    const c = getChart("chart-city");
    c.setOption({
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: d.map(r => r.city), axisLabel: { rotate: 35 } },
        yAxis: { type: "value", name: "营收(元)" },
        series: [{ type: "bar", data: d.map(r => r.revenue), itemStyle: { color: purple }, barWidth: 20, label: { show: true, position: "top", fontSize: 10 } }],
        grid: { left: 60, right: 20, bottom: 50, top: 30 }
    });
}

// ── 季节对比 ──────────────────────────────────
async function loadSeason() {
    const d = await getJSON("/api/season" + qp());
    const order = ["春季", "夏季", "秋季", "冬季"];
    d.sort((a, b) => order.indexOf(a.season) - order.indexOf(b.season));
    const c = getChart("chart-season");
    c.setOption({
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: d.map(r => r.season) },
        yAxis: { type: "value" },
        series: [{ type: "bar", data: d.map((r, i) => ({ value: r.qty, itemStyle: { color: palette[i] } })), barWidth: 40, label: { show: true, position: "top", formatter: "{c}杯" } }],
        grid: { left: 60, right: 20, bottom: 20, top: 30 }
    });
}

// ── 天气影响 ──────────────────────────────────
async function loadWeather() {
    const d = await getJSON("/api/weather" + qp());
    const c = getChart("chart-weather");
    c.setOption({
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: d.map(r => r.weather) },
        yAxis: { type: "value", name: "平均销量" },
        series: [{ type: "bar", data: d.map((r, i) => ({ value: Math.round(r.avg_qty), itemStyle: { color: palette[i % palette.length] } })), barWidth: 28, label: { show: true, position: "top" } }],
        grid: { left: 50, right: 20, bottom: 20, top: 30 }
    });
}

// ── 营销活动 ──────────────────────────────────
async function loadCampaign() {
    const d = await getJSON("/api/campaign" + qp());
    const c = getChart("chart-campaign");
    c.setOption({
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: d.map(r => r.campaign), axisLabel: { rotate: 35, fontSize: 11 } },
        yAxis: { type: "value", name: "平均销量" },
        series: [{ type: "bar", data: d.map((r, i) => ({ value: Math.round(r.avg_qty), itemStyle: { color: palette[i % palette.length] } })), barWidth: 24, label: { show: true, position: "top" } }],
        grid: { left: 50, right: 20, bottom: 70, top: 30 }
    });
}

// ── 热力图 ────────────────────────────────────
async function loadHeatmap() {
    const d = await getJSON("/api/city_product" + qp());
    const c = getChart("chart-heatmap");
    const max = Math.max(...d.data.map(v => v[2]));
    c.setOption({
        tooltip: { formatter: p => `${d.cities[p.data[1]]} - ${d.products[p.data[0]]}<br/>销量: ${p.data[2]}杯` },
        xAxis: { type: "category", data: d.products, axisLabel: { rotate: 30, fontSize: 11 }, splitArea: { show: true } },
        yAxis: { type: "category", data: d.cities, splitArea: { show: true } },
        visualMap: { min: 0, max, calculable: true, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#f3e8ff", purple, "#3b0764"] } },
        series: [{ type: "heatmap", data: d.data, label: { show: true, fontSize: 10 }, emphasis: { itemStyle: { shadowBlur: 10, shadowColor: "rgba(0,0,0,.5)" } } }],
        grid: { left: 80, right: 20, bottom: 60, top: 10 }
    });
}

// ── 节假日对比 ────────────────────────────────
async function loadHoliday() {
    const d = await getJSON("/api/holiday" + qp());
    const c = getChart("chart-holiday");
    c.setOption({
        tooltip: {
            trigger: "axis",
            formatter: p => {
                let s = p[0].axisValue + "<br/>";
                p.forEach(it => {
                    const v = it.seriesName === "平均营收"
                        ? "¥" + Number(it.value).toLocaleString("zh-CN")
                        : Number(it.value).toLocaleString("zh-CN") + " 杯";
                    s += it.marker + it.seriesName + "：" + v + "<br/>";
                });
                return s;
            }
        },
        legend: { bottom: 0, data: ["平均销量", "平均营收"] },
        xAxis: { type: "category", data: d.map(r => r.is_holiday === "是" ? "节假日" : "工作日") },
        yAxis: [
            { type: "value", name: "平均销量(杯)", position: "left" },
            { type: "value", name: "平均营收(元)", position: "right", axisLabel: { formatter: v => "¥" + v } }
        ],
        series: [
            {
                name: "平均销量", type: "bar", yAxisIndex: 0,
                data: d.map(r => Math.round(r.avg_qty)),
                itemStyle: { color: purple }, barWidth: 44,
                label: { show: true, position: "top", formatter: "{c}" }
            },
            {
                name: "平均营收", type: "line", yAxisIndex: 1,
                data: d.map(r => Math.round(r.avg_revenue)),
                itemStyle: { color: orange }, symbolSize: 10, lineStyle: { width: 3 },
                label: { show: true, position: "top", formatter: p => "¥" + p.value }
            }
        ],
        grid: { left: 65, right: 75, bottom: 40, top: 30 }
    });
}

// ── 折扣分析 ──────────────────────────────────
async function loadDiscount() {
    const d = await getJSON("/api/discount" + qp());
    const c = getChart("chart-discount");
    c.setOption({
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: d.map(r => r.discount === 0 ? "原价" : (Math.round((1 - r.discount) * 100) / 10) + "折"), axisLabel: { rotate: 20 } },
        yAxis: { type: "value", name: "平均销量" },
        series: [{ type: "bar", data: d.map((r, i) => ({ value: Math.round(r.avg_qty), itemStyle: { color: palette[i] } })), barWidth: 36, label: { show: true, position: "top" } }],
        grid: { left: 50, right: 20, bottom: 40, top: 20 }
    });
}

// ── 单品经营明细表 ────────────────────────────
async function loadProductDetail() {
    const d = await getJSON("/api/product_detail" + qp());
    const box = document.getElementById("product-detail-table");
    if (!d.length) { box.innerHTML = '<p class="muted">当前筛选条件下无数据</p>'; return; }
    const head = `<tr><th>产品</th><th>品类</th><th>销量(杯)</th><th>营收(元)</th><th>客单价(元)</th><th>平均会员%</th><th>平均折扣</th><th>覆盖城市</th></tr>`;
    const rows = d.map(r => `<tr data-p="${r.product}" class="${state.product === r.product ? "sel-row" : ""}">
        <td>${r.product}</td><td>${r.category}</td>
        <td>${fmt(r.qty)}</td><td>¥${fmt(r.revenue)}</td>
        <td>${r.unit_price}</td><td>${r.avg_member}%</td>
        <td>${r.avg_discount ? Math.round(r.avg_discount * 100) + "%" : "--"}</td>
        <td>${r.city_cnt}</td></tr>`).join("");
    box.innerHTML = `<table>${head}${rows}</table>`;
    box.querySelectorAll("tr[data-p]").forEach(tr => {
        tr.style.cursor = "pointer";
        tr.addEventListener("click", () => selectProduct(tr.getAttribute("data-p")));
    });
}

// ── 产品月度趋势（多线图）─────────────────────
async function loadProductMonthly() {
    const d = await getJSON("/api/product_monthly" + qp());
    const c = getChart("chart-product-monthly");
    c.setOption({
        tooltip: { trigger: "axis" },
        legend: { type: "scroll", bottom: 0 },
        xAxis: { type: "category", data: d.months, axisLabel: { rotate: 30 } },
        yAxis: { type: "value", name: "销量(杯)" },
        series: d.series.map((s, i) => ({
            name: s.name, type: "line", smooth: true,
            data: s.data, symbolSize: 5, lineStyle: { width: 2 },
            itemStyle: { color: palette[i % palette.length] }
        })),
        grid: { left: 60, right: 30, bottom: 50, top: 30 }
    });
}

// ── 产品关联分析（Apriori 结果表）──────────────
function _relOf(lift) {
    if (lift > 1.0) return { t: "互补", cls: "rel-comp" };
    if (lift < 1.0) return { t: "替代", cls: "rel-sub" };
    return { t: "中性", cls: "rel-neu" };
}
async function loadProductAssoc() {
    const d = await getJSON("/api/product_assoc" + qp());
    const box = document.getElementById("product-assoc-table");
    if (!d.length) {
        let reason;
        if (state.product) {
            reason = "已筛选「单品」，购物篮退化为单一产品，无法形成关联对。请取消单品筛选查看全量。";
        } else if (state.category) {
            reason = `已筛选「品类=${state.category}」，该品类在当前数据下仅含 1 款产品，无法形成关联对。请取消品类筛选。`;
        } else {
            reason = "当前筛选条件下每个购物篮仅含 1 个单品，无法生成关联规则。请取消筛选查看全量数据。";
        }
        box.innerHTML = `<p class="muted">${reason}</p>`;
        return;
    }
    const head = `<tr><th>产品A</th><th>产品B</th><th>共现次数</th><th>支持度</th><th>置信度</th><th>提升度</th><th>关系</th></tr>`;
    const rows = d.map(r => {
        const rel = _relOf(r.lift);
        return `<tr>
        <td>${r.a}</td><td>${r.b}</td>
        <td>${r.cooc}</td><td>${r.support}</td><td>${r.confidence}</td>
        <td>${r.lift}</td>
        <td class="${rel.cls}">${rel.t}</td></tr>`;
    }).join("");
    box.innerHTML = `<table>${head}${rows}</table>`;
}

// ── 会员分层柱状图（按会员占比四分位）────────────
async function loadMemberTier() {
    const d = await getJSON("/api/member_tier" + qp());
    const c = getChart("chart-member-tier");
    if (!d.length) { c.clear(); return; }
    c.setOption({
        tooltip: {
            trigger: "axis",
            formatter: p => {
                const it = d[p[0].dataIndex];
                return `${it.tier}<br/>平均销量：${fmt(it.avg_qty)} 杯<br/>样本门店数：${it.count}`;
            }
        },
        xAxis: { type: "category", data: d.map(r => r.tier), axisLabel: { fontSize: 11 } },
        yAxis: { type: "value", name: "平均销量(杯)" },
        series: [{
            type: "bar", data: d.map(r => r.avg_qty), itemStyle: { color: purple },
            barWidth: 44, label: { show: true, position: "top", formatter: p => fmt(p.value) }
        }],
        grid: { left: 60, right: 20, bottom: 40, top: 30 }
    });
}

// ── 中国销量分布地图（geo + 城市气泡 + 联动）────
let _chinaRegistered = false;
async function ensureChinaMap() {
    if (_chinaRegistered) return;
    const geo = await fetch("/geo/china.json").then(r => r.json());
    echarts.registerMap("china", geo);
    _chinaRegistered = true;
}

function selectCity(name) {
    const sel = document.getElementById("filter-city");
    if (state.city === name) { state.city = ""; sel.value = ""; }
    else { state.city = name; sel.value = name; }
    loadAll();
}

function selectProduct(name) {
    const sel = document.getElementById("filter-product");
    if (state.product === name) { state.product = ""; sel.value = ""; }
    else { state.product = name; sel.value = name; }
    loadAll();
}

async function loadChinaMap() {
    await ensureChinaMap();
    // 地图始终展示全部城市，仅受品类/月份筛选（不被城市筛选收掉）
    const p = new URLSearchParams();
    if (state.category) p.set("category", state.category);
    if (state.month) p.set("month", state.month);
    const qs = p.toString() ? "?" + p.toString() : "";
    const d = await getJSON("/api/city_geo" + qs);
    const c = getChart("chart-china-map");
    const valid = d.filter(r => r.coord && r.coord[0] != null);
    const maxQ = Math.max(...valid.map(r => r.qty), 1);
    const selected = state.city;
    const data = valid.map(r => {
        const isSel = r.city === selected;
        return {
            name: r.city,
            value: [r.coord[0], r.coord[1], r.qty, r.revenue, isSel ? 1 : 0],
            itemStyle: isSel
                ? { color: "#ef4444", borderColor: "#fff", borderWidth: 3, shadowBlur: 12, shadowColor: "rgba(239,68,68,.6)" }
                : undefined,
            label: isSel
                ? { show: true, formatter: p => p.name, position: "right", fontSize: 12, fontWeight: "bold", color: "#ef4444" }
                : undefined
        };
    });
    c.setOption({
        tooltip: {
            trigger: "item",
            formatter: p => {
                const [, , q, rev] = p.value;
                return `${p.name}<br/>销量：${fmt(q)} 杯<br/>营收：¥${fmt(rev)}`;
            }
        },
        visualMap: {
            min: 0, max: maxQ, dimension: 2, left: "left", bottom: 24,
            text: ["高销量", "低销量"], calculable: true,
            inRange: { color: ["#f3e8ff", purple, "#3b0764"] }
        },
        geo: {
            map: "china", roam: true, zoom: 1.2,
            itemStyle: { areaColor: "#f0f0f5", borderColor: "#cbd5e1" },
            emphasis: { itemStyle: { areaColor: "#e9d5ff" }, label: { show: false } }
        },
        series: [{
            name: "城市销量", type: "effectScatter", coordinateSystem: "geo",
            data,
            symbolSize: v => (v[4] ? 40 : 10 + (v[2] / maxQ) * 26),
            label: { show: true, formatter: p => p.name, position: "right", fontSize: 11, color: "#333" },
            emphasis: { scale: true }
        }]
    }, { notMerge: true });
    c.off("click");
    c.on("click", params => {
        if (params.componentType === "series" && params.name) selectCity(params.name);
    });
}

// ── 分析结论 ──────────────────────────────────
async function loadInsights() {
    const box = document.getElementById("insights-list");
    try {
        const list = await getJSON("/api/insights" + qp());
        if (!list.length) { box.innerHTML = '<p class="muted">当前筛选条件下无数据</p>'; return; }
        box.innerHTML = list.map(it => `
            <div class="insight-card">
                <span class="insight-tag">${it.tag}</span>
                <h4>${it.title}</h4>
                <p>${it.text}</p>
            </div>`).join("");
    } catch (e) {
        box.innerHTML = '<p class="muted">分析结论加载失败</p>';
    }
}

// ── 总加载 ────────────────────────────────────
async function loadAll() {
    const tasks = [loadKPI, loadMonthly, loadProduct, loadCategory, loadCity,
        loadSeason, loadWeather, loadCampaign, loadHeatmap, loadHoliday, loadDiscount,
        loadProductDetail, loadProductMonthly, loadProductAssoc, loadMemberTier, loadChinaMap, loadInsights];
    await Promise.allSettled(tasks.map(t => t().catch(err => console.error(t.name, err))));
    const hint = document.getElementById("filter-hint");
    const parts = [];
    if (state.city) parts.push("城市：" + state.city);
    if (state.category) parts.push("品类：" + state.category);
    hint.textContent = parts.length ? "已筛选 · " + parts.join(" / ") : "展示全部数据";
}

// ── 筛选器初始化 ──────────────────────────────
async function initFilters() {
    try {
        const meta = await getJSON("/api/meta");
        const citySel = document.getElementById("filter-city");
        const catSel = document.getElementById("filter-category");
        const prodSel = document.getElementById("filter-product");
        const monthSel = document.getElementById("filter-month");
        meta.cities.forEach(c => citySel.add(new Option(c, c)));
        meta.categories.forEach(c => catSel.add(new Option(c, c)));
        meta.products.forEach(p => prodSel.add(new Option(p, p)));
        meta.months.forEach(m => monthSel.add(new Option(m, m)));
        citySel.addEventListener("change", e => { state.city = e.target.value; loadAll(); });
        catSel.addEventListener("change", e => { state.category = e.target.value; loadAll(); });
        prodSel.addEventListener("change", e => { state.product = e.target.value; loadAll(); });
        monthSel.addEventListener("change", e => { state.month = e.target.value; loadAll(); });
        document.getElementById("filter-reset").addEventListener("click", () => {
            state.city = ""; state.category = ""; state.product = ""; state.month = "";
            citySel.value = ""; catSel.value = ""; prodSel.value = ""; monthSel.value = "";
            loadAll();
        });
    } catch (e) {
        console.error("筛选器初始化失败", e);
    }
}

initFilters();
initProductToggle();
loadAll();
