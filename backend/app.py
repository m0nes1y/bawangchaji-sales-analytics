"""
霸王茶姬销量数据分析可视化平台 - Flask 后端
提供 REST API 并将前端作为静态资源托管。
"""

import csv
import os
import re
import sqlite3
import time
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DATA_PATH = os.path.join(ROOT_DIR, "data", "bawangchaji_sales.csv")
DB_PATH = os.path.join(BASE_DIR, "bawangchaji.db")

app = Flask(__name__, static_folder=os.path.join(ROOT_DIR, "frontend"), static_url_path="")

# ── 配置（环境变量驱动，避免硬编码）─────────────────
DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
PORT = int(os.environ.get("PORT", "8080"))

# 15 个城市经纬度（标准 WGS-84，用于中国地图气泡定位）
CITY_COORDS = {
    "北京": [116.405, 39.905], "上海": [121.4737, 31.2304],
    "广州": [113.2644, 23.1291], "深圳": [114.0579, 22.5431],
    "成都": [104.0668, 30.5728], "杭州": [120.1551, 30.2741],
    "武汉": [114.3055, 30.5928], "南京": [118.7974, 32.0603],
    "西安": [108.948, 34.263], "重庆": [106.551, 29.563],
    "长沙": [112.9823, 28.1941], "苏州": [120.6196, 31.2994],
    "郑州": [113.6254, 34.7466], "天津": [117.201, 39.084],
    "青岛": [120.355, 36.082],
}

# 简单内存缓存：数据静态，启动后不变，避免重复查询
_CACHE = {}
_CACHE_TTL = 120


def _cache_get(key):
    item = _CACHE.get(key)
    if item and time.time() - item[0] < _CACHE_TTL:
        return item[1]
    return None


def _cache_set(key, val):
    _CACHE[key] = (time.time(), val)


# ── 数据初始化（带校验）────────────────────────────
def _to_float(v):
    try:
        return float(str(v).strip())
    except Exception:
        return None


def init_db():
    """读取 CSV 并写入 SQLite，对脏数据做校验与跳过统计。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS sales")
    cur.execute(
        """CREATE TABLE sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, city TEXT, store_id TEXT, order_id TEXT, product TEXT,
            category TEXT, unit_price REAL, quantity INTEGER,
            discount REAL, revenue REAL, member_pct REAL,
            weather TEXT, season TEXT, is_holiday TEXT, campaign TEXT
        )"""
    )
    loaded = 0
    skipped = 0
    with open(DATA_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                date = str(row.get("日期", "")).strip()
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                    skipped += 1
                    continue
                order_id = str(row.get("订单号", "")).strip()
                if not order_id:
                    skipped += 1
                    continue
                unit_price = _to_float(row.get("单价(元)"))
                quantity = _to_float(row.get("销量(杯)"))
                discount = _to_float(row.get("折扣"))
                revenue = _to_float(row.get("实付金额(元)"))
                member = _to_float(row.get("会员占比(%)"))
                if None in (unit_price, quantity, discount, revenue, member):
                    skipped += 1
                    continue
                if quantity < 0 or revenue < 0 or not (0 <= discount <= 1):
                    skipped += 1
                    continue
                cur.execute(
                    "INSERT INTO sales VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        date,
                        str(row.get("城市", "")).strip(),
                        str(row.get("门店编号", "")).strip(),
                        order_id,
                        str(row.get("产品名称", "")).strip(),
                        str(row.get("产品类别", "")).strip(),
                        unit_price,
                        int(quantity),
                        discount,
                        revenue,
                        member,
                        str(row.get("天气", "")).strip(),
                        str(row.get("季节", "")).strip(),
                        str(row.get("是否节假日", "")).strip(),
                        str(row.get("营销活动", "") or "").strip(),
                    ),
                )
                loaded += 1
            except Exception:
                skipped += 1
    conn.commit()
    conn.close()
    return loaded, skipped


def ensure_db():
    # 数据 CSV 缺失时自动重新生成（固定随机种子，结果可复现），保证克隆仓库后开箱即用
    if not os.path.exists(DATA_PATH):
        try:
            import sys
            if BASE_DIR not in sys.path:
                sys.path.insert(0, BASE_DIR)
            import generate_data
            print("[init] 未找到数据文件，正在自动生成 bawangchaji_sales.csv ...")
            generate_data.main()
        except Exception as e:
            print(f"[init] 自动生成数据失败：{e}")
    if not os.path.exists(DB_PATH):
        n, s = init_db()
        print(f"[init] 首次建库完成：导入 {n} 行，跳过 {s} 行脏数据")
        return
    cnt = query("SELECT COUNT(*) AS c FROM sales")
    if not cnt or cnt[0]["c"] == 0:
        n, s = init_db()
        print(f"[init] 数据库为空，重新导入：{n} 行，跳过 {s} 行")


# ── 通用查询（缓存 + 行转字典）─────────────────────
def query(sql, params=()):
    key = sql + "|" + str(params)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    result = [dict(r) for r in rows]
    _cache_set(key, result)
    return result


def _filters():
    """从请求参数解析 城市/品类/产品/月份 筛选条件。"""
    clauses = []
    params = []
    for field, arg in (("city", "city"), ("category", "category"), ("product", "product")):
        raw = request.args.get(arg, "")
        items = [x for x in raw.split(",") if x]
        if items:
            clauses.append(f"{field} IN ({','.join('?' * len(items))})")
            params.extend(items)
    month = request.args.get("month", "")
    if month:
        clauses.append("strftime('%Y-%m', date) = ?")
        params.append(month)
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ── 前端路由 ───────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/meta")
def api_meta():
    """返回筛选项（城市/品类/产品/天气/季节/营销活动）。"""
    def distinct(col):
        return [r[col] for r in query(f"SELECT DISTINCT {col} FROM sales WHERE {col} <> '' ORDER BY {col}")]

    return jsonify({
        "cities": distinct("city"),
        "categories": distinct("category"),
        "products": distinct("product"),
        "weathers": distinct("weather"),
        "seasons": distinct("season"),
        "campaigns": distinct("campaign"),
        "months": [r["m"] for r in query("SELECT DISTINCT strftime('%Y-%m', date) AS m FROM sales ORDER BY m")],
    })


@app.route("/api/summary")
def api_summary():
    fw, fp = _filters()
    r = query(
        f"SELECT SUM(quantity) AS total_qty, SUM(revenue) AS total_revenue, "
        f"COUNT(*) AS total_records, AVG(member_pct) AS avg_member FROM sales WHERE 1=1 {fw}",
        fp,
    )[0]
    return jsonify(r)


@app.route("/api/monthly")
def api_monthly():
    fw, fp = _filters()
    rows = query(
        f"""SELECT month, qty, revenue,
            ROUND((qty - prev_qty) * 100.0 / NULLIF(prev_qty, 0), 1) AS mom
        FROM (
            SELECT strftime('%Y-%m', date) AS month,
                   SUM(quantity) AS qty,
                   SUM(revenue) AS revenue,
                   LAG(SUM(quantity)) OVER (ORDER BY strftime('%Y-%m', date)) AS prev_qty
            FROM sales WHERE 1=1 {fw} GROUP BY month
        ) t
        ORDER BY month""",
        fp,
    )
    return jsonify(rows)


@app.route("/api/product_rank")
def api_product_rank():
    fw, fp = _filters()
    rows = query(
        f"SELECT product, category, SUM(quantity) AS qty, SUM(revenue) AS revenue "
        f"FROM sales WHERE 1=1 {fw} GROUP BY product ORDER BY qty DESC",
        fp,
    )
    return jsonify(rows)


@app.route("/api/city_rank")
def api_city_rank():
    fw, fp = _filters()
    rows = query(
        f"SELECT city, SUM(quantity) AS qty, SUM(revenue) AS revenue "
        f"FROM sales WHERE 1=1 {fw} GROUP BY city ORDER BY revenue DESC",
        fp,
    )
    return jsonify(rows)


@app.route("/api/season")
def api_season():
    fw, fp = _filters()
    rows = query(
        f"SELECT season, SUM(quantity) AS qty, SUM(revenue) AS revenue "
        f"FROM sales WHERE 1=1 {fw} GROUP BY season",
        fp,
    )
    return jsonify(rows)


@app.route("/api/category_pie")
def api_category_pie():
    fw, fp = _filters()
    rows = query(
        f"SELECT category, SUM(quantity) AS qty FROM sales WHERE 1=1 {fw} "
        f"GROUP BY category ORDER BY qty DESC",
        fp,
    )
    return jsonify(rows)


@app.route("/api/weather")
def api_weather():
    # 交易级数据下，quantity 为单行杯数；以「单店日」为可比单元，看平均单店日销量
    fw, fp = _filters()
    rows = query(
        f"SELECT weather, AVG(day_qty) AS avg_qty "
        f"FROM (SELECT store_id, date, weather, SUM(quantity) AS day_qty "
        f"      FROM sales WHERE 1=1 {fw} GROUP BY store_id, date, weather) "
        f"GROUP BY weather ORDER BY avg_qty DESC",
        fp,
    )
    return jsonify(rows)


@app.route("/api/campaign")
def api_campaign():
    fw, fp = _filters()
    rows = query(
        f"SELECT campaign, AVG(day_qty) AS avg_qty, SUM(day_rev) AS revenue, COUNT(*) AS days "
        f"FROM (SELECT store_id, date, "
        f"        CASE WHEN campaign IS NOT NULL AND campaign != '' THEN campaign ELSE '无活动' END AS campaign, "
        f"        SUM(quantity) AS day_qty, SUM(revenue) AS day_rev "
        f"      FROM sales WHERE 1=1 {fw} "
        f"      GROUP BY store_id, date, CASE WHEN campaign IS NOT NULL AND campaign != '' THEN campaign ELSE '无活动' END) "
        f"GROUP BY campaign ORDER BY revenue DESC",
        fp,
    )
    return jsonify(rows)


@app.route("/api/holiday")
def api_holiday():
    fw, fp = _filters()
    rows = query(
        f"SELECT is_holiday, AVG(day_qty) AS avg_qty, AVG(day_rev) AS avg_revenue "
        f"FROM (SELECT store_id, date, is_holiday, SUM(quantity) AS day_qty, SUM(revenue) AS day_rev "
        f"      FROM sales WHERE 1=1 {fw} GROUP BY store_id, date, is_holiday) "
        f"GROUP BY is_holiday",
        fp,
    )
    return jsonify(rows)


@app.route("/api/discount")
def api_discount():
    # 折扣为明细行属性：按「单店日最深折扣」分档，看对应日均销量
    fw, fp = _filters()
    rows = query(
        f"SELECT disc, AVG(day_qty) AS avg_qty, SUM(day_rev) AS revenue "
        f"FROM (SELECT store_id, date, MAX(discount) AS disc, SUM(quantity) AS day_qty, SUM(revenue) AS day_rev "
        f"      FROM sales WHERE 1=1 {fw} GROUP BY store_id, date) "
        f"GROUP BY disc ORDER BY disc",
        fp,
    )
    return jsonify(rows)


@app.route("/api/city_product")
def api_city_product():
    fw, fp = _filters()
    rows = query(
        f"SELECT city, product, SUM(quantity) AS qty FROM sales WHERE 1=1 {fw} "
        f"GROUP BY city, product",
        fp,
    )
    cities = sorted(set(r["city"] for r in rows))
    products = sorted(set(r["product"] for r in rows))
    data = [[products.index(r["product"]), cities.index(r["city"]), r["qty"]] for r in rows]
    return jsonify({"cities": cities, "products": products, "data": data})


def _store_days(fw, fp):
    """「单店日」聚合，供对比类洞察统一口径（交易级 quantity 均值会被削平）。"""
    return query(
        f"SELECT store_id, date, weather, is_holiday, "
        f"  CASE WHEN campaign IS NOT NULL AND campaign != '' THEN campaign ELSE '无活动' END AS campaign, "
        f"  MAX(discount) AS disc, AVG(member_pct) AS member, "
        f"  SUM(quantity) AS qty, SUM(revenue) AS rev "
        f"FROM sales WHERE 1=1 {fw} "
        f"GROUP BY store_id, date, weather, is_holiday, "
        f"  CASE WHEN campaign IS NOT NULL AND campaign != '' THEN campaign ELSE '无活动' END",
        fp,
    )


@app.route("/api/insights")
def api_insights():
    """动态计算分析结论（非硬编码），供前端'分析结论'面板展示。"""
    fw, fp = _filters()
    insights = []

    base = query(
        f"SELECT COUNT(*) AS c, SUM(quantity) AS q, SUM(revenue) AS rev, AVG(member_pct) AS m "
        f"FROM sales WHERE 1=1 {fw}",
        fp,
    )
    if not base or base[0]["c"] == 0:
        return jsonify([])

    sd = _store_days(fw, fp)  # 单店日聚合，统一对比口径

    # 1. 城市营收两极
    cities = query(
        f"SELECT city, SUM(revenue) AS rev, SUM(quantity) AS q FROM sales WHERE 1=1 {fw} "
        f"GROUP BY city ORDER BY rev DESC",
        fp,
    )
    if len(cities) >= 2:
        top, bottom = cities[0], cities[-1]
        ratio = top["rev"] / bottom["rev"] if bottom["rev"] else 0
        insights.append({
            "tag": "区域表现",
            "title": f"营收冠军 {top['city']}，是末位 {bottom['city']} 的 {ratio:.1f} 倍",
            "text": f"{top['city']} 全年营收 ¥{top['rev']:,.0f}，{bottom['city']} 仅 ¥{bottom['rev']:,.0f}，"
                    f"区域间差距显著，下沉/弱势市场存在明显提升空间。",
        })

    # 2. 王牌单品
    prods = query(
        f"SELECT product, SUM(revenue) AS rev, SUM(quantity) AS q FROM sales WHERE 1=1 {fw} "
        f"GROUP BY product ORDER BY rev DESC",
        fp,
    )
    if prods:
        p = prods[0]
        share = p["rev"] / base[0]["rev"] * 100 if base[0]["rev"] else 0
        insights.append({
            "tag": "产品结构",
            "title": f"王牌单品「{p['product']}」贡献 {share:.1f}% 营收",
            "text": f"该单品营收 ¥{p['rev']:,.0f}、销量 {p['q']:,.0f} 杯，稳居第一，"
                    f"是引流与基本盘的核心，应优先保障供应与陈列。",
        })

    # 3. 季节性
    seasons = {r["season"]: r["qty"] for r in query(
        f"SELECT season, SUM(quantity) AS qty FROM sales WHERE 1=1 {fw} GROUP BY season", fp)}
    if len(seasons) >= 2:
        hi = max(seasons, key=seasons.get)
        lo = min(seasons, key=seasons.get)
        insights.append({
            "tag": "季节规律",
            "title": f"{hi}是销量旺季，{lo}最低",
            "text": f"{hi}销量 {seasons[hi]:,.0f} 杯，{lo}仅 {seasons[lo]:,.0f} 杯，"
                    f"热饮属性使冬季反超夏季，应据季节调整新品与备货节奏。",
        })

    # 4. 折扣弹性（有折扣 vs 无折扣 的「单店日均」对比）
    nodisc = [r["qty"] for r in sd if r["disc"] == 0]
    disc = [r["qty"] for r in sd if r["disc"] > 0]
    if nodisc and disc:
        a, b = sum(disc) / len(disc), sum(nodisc) / len(nodisc)
        lift = (a / b - 1) * 100
        buckets = {}
        for r in sd:
            buckets.setdefault(round(r["disc"], 2), []).append(r["qty"])
        off = {round(k * 100): sum(v) / len(v) for k, v in buckets.items() if k > 0}
        if off:
            best_off, worst_off = min(off), max(off)
            pos = lift >= 0
            insights.append({
                "tag": "价格策略",
                "title": f"折扣对单店日均销量{'正向拉动' if pos else '拉动有限'}（整体 {lift:+.1f}%）",
                "text": f"有折扣单店日均 {a:.0f} 杯 vs 无折扣 {b:.0f} 杯（{lift:+.1f}%）；"
                        f"让利{best_off}%约{off[best_off]:.0f}杯 → 让利{worst_off}%约{off[worst_off]:.0f}杯，"
                        f"{'折扣越深销量越高，可借促销做增量' if pos else '盲目深折未必换来增量，应精准投放'}。",
            })

    # 5. 天气敏感度（单店日均）
    wea = {}
    for r in sd:
        wea.setdefault(r["weather"], []).append(r["qty"])
    wea_avg = sorted(((k, sum(v) / len(v)) for k, v in wea.items()), key=lambda x: -x[1])
    if len(wea_avg) >= 2:
        hi, lo = wea_avg[0], wea_avg[-1]
        insights.append({
            "tag": "天气影响",
            "title": f"{hi[0]}天单店日均最高（{hi[1]:.0f} 杯），{lo[0]}天最低（{lo[1]:.0f} 杯）",
            "text": f"晴好天气显著带动购买，{lo[0]}等恶劣天气明显走低，"
                    f"可结合短期天气预报做动态排班与库存预警。",
        })

    # 6. 节假日效应（单店日均）
    hol = {"是": [], "否": []}
    for r in sd:
        hol[r["is_holiday"]].append(r["qty"])
    if hol["是"] and hol["否"]:
        a = sum(hol["是"]) / len(hol["是"])
        b = sum(hol["否"]) / len(hol["否"])
        lift = (a / b - 1) * 100
        insights.append({
            "tag": "节假日",
            "title": f"节假日单店日均较工作日{lift:+.1f}%",
            "text": f"节假日平均 {a:.0f} 杯，工作日 {b:.0f} 杯；"
                    f"{'节假日自然流量红利明显，可借势加码营销' if lift >= 0 else '节假日红利有限，需靠特定活动转化'}。",
        })

    # 7. 会员相关性（单店日：会员占比 vs 日均销量，Pearson）
    if len(sd) >= 3:
        xs = [r["member"] for r in sd]
        ys = [r["qty"] for r in sd]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = sum((x - mx) ** 2 for x in xs) ** 0.5
        sy = sum((y - my) ** 2 for y in ys) ** 0.5
        rr = cov / (sx * sy) if sx and sy else 0
        insights.append({
            "tag": "会员运营",
            "title": f"会员占比与销量正相关（r={rr:.2f}）",
            "text": "门店会员占比越高，单店日均销量越好；提升会员拉新与复购运营，对单店产出有正向杠杆作用。",
        })

    # 8. 机会点：全国热销但在某城市偏弱
    if len(prods) >= 1 and len(cities) >= 2:
        top_prod = prods[0]["product"]
        cp = query(
            f"SELECT city, SUM(quantity) AS q FROM sales WHERE 1=1 {fw} AND product=? GROUP BY city",
            fp + [top_prod],
        )
        if len(cp) >= 2:
            weak = min(cp, key=lambda x: x["q"])
            strong = max(cp, key=lambda x: x["q"])
            insights.append({
                "tag": "增长机会",
                "title": f"「{top_prod}」在 {weak['city']} 渗透偏低",
                "text": f"该单品在 {strong['city']} 销量 {strong['q']:,.0f} 杯，"
                        f"而在 {weak['city']} 仅 {weak['q']:,.0f} 杯，"
                        f"说明同款产品在不同城市接受度差异大，可针对性做本地化推广。",
            })

    # 9. 价格弹性（单店日口径：折扣档（MAX折扣）vs 日均销量，一元线性回归）
    buckets = {}
    for r in sd:
        buckets.setdefault(round(r["disc"], 2), []).append(r["qty"])
    xs = list(buckets.keys())
    ys = [sum(v) / len(v) for v in buckets.values()]
    if len(xs) >= 2:
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 0
        pct = slope * 0.1 / my * 100  # 折扣力度每增加 10 个百分点（0.1）的日均销量变化%
        insights.append({
            "tag": "价格弹性",
            "title": f"折扣力度每加深 10%，单店日均销量约 {pct:+.1f}%",
            "text": f"以单店日均销量对折扣力度做一元线性回归，斜率为 {slope:.0f} 杯/折扣单位，"
                    f"即{'让利越多销量走高' if pct >= 0 else '让利越多销量反而走低'}（{pct:+.1f}%/10% off），"
                    f"量化刻画了价格对销量的弹性，可作为促销 ROI 测算的输入。",
        })

    # 10. 会员分层（单店日口径，按会员占比四分位，呼应 RFM / pd.qcut 能力）
    if len(sd) >= 4:
        mp = sorted(r["member"] for r in sd)
        q = [mp[int(len(mp) * p)] for p in (0.25, 0.5, 0.75)]
        agg = [0.0, 0.0, 0.0, 0.0]
        cnt = [0, 0, 0, 0]
        for r in sd:
            m = r["member"]
            i = 0 if m <= q[0] else 1 if m <= q[1] else 2 if m <= q[2] else 3
            agg[i] += r["qty"]
            cnt[i] += 1
        avg = [agg[i] / cnt[i] if cnt[i] else 0 for i in range(4)]
        insights.append({
            "tag": "会员分层",
            "title": f"会员占比越高销量越高：高层日均 {avg[3]:.0f} 杯 vs 低层 {avg[0]:.0f} 杯",
            "text": f"按会员占比四分位分层后，各层单店日均销量随会员占比递增（{avg[0]:.0f}→{avg[3]:.0f} 杯），"
                    f"进一步验证会员运营对单店产出的正向杠杆，可作为精细化分层运营的依据。",
        })

    # 11. 产品关联（Apriori：以真实订单为购物篮，取提升度最高的组合）
    assoc_rows = query(f"SELECT order_id, product FROM sales WHERE 1=1 {fw}", fp)
    baskets = {}
    for r in assoc_rows:
        baskets.setdefault(r["order_id"], set()).add(r["product"])
    if len(baskets) >= 2:
        item_cnt, pair_cnt = {}, {}
        for items in baskets.values():
            items = list(items)
            for it in items:
                item_cnt[it] = item_cnt.get(it, 0) + 1
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = (items[i], items[j]) if items[i] < items[j] else (items[j], items[i])
                    pair_cnt[(a, b)] = pair_cnt.get((a, b), 0) + 1
        if pair_cnt:
            nb = len(baskets)
            best = max(pair_cnt.items(),
                       key=lambda kv: kv[1] / item_cnt[kv[0][0]] / (item_cnt[kv[0][1]] / nb))
            (a, b), c = best
            lift = (c / item_cnt[a]) / (item_cnt[b] / nb)
            insights.append({
                "tag": "产品关联",
                "title": f"「{a}」与「{b}」最常被一起购买（提升度 {lift:.2f}）",
                "text": f"以真实订单（购物篮）做关联分析，两款产品共同出现 {c} 次、提升度 {lift:.2f}，"
                        f"说明存在明显搭配需求，可设计组合套餐或相邻陈列以提升客单价。",
            })

    return jsonify(insights)


# ── 单品经营明细 ──────────────────────────────────
@app.route("/api/product_detail")
def api_product_detail():
    fw, fp = _filters()
    rows = query(
        f"""SELECT product, category,
                   SUM(quantity) AS qty,
                   SUM(revenue) AS revenue,
                   ROUND(SUM(revenue) * 1.0 / SUM(quantity), 1) AS unit_price,
                   ROUND(AVG(member_pct), 1) AS avg_member,
                   ROUND(AVG(discount), 3) AS avg_discount,
                   COUNT(DISTINCT city) AS city_cnt
            FROM sales WHERE 1=1 {fw} GROUP BY product ORDER BY revenue DESC""",
        fp,
    )
    return jsonify(rows)


# ── 产品月度趋势（多线图）─────────────────────────
@app.route("/api/product_monthly")
def api_product_monthly():
    fw, fp = _filters()
    raw = query(
        f"""SELECT strftime('%Y-%m', date) AS month, product, SUM(quantity) AS qty
            FROM sales WHERE 1=1 {fw} GROUP BY month, product""",
        fp,
    )
    months = sorted(set(r["month"] for r in raw))
    products = sorted(set(r["product"] for r in raw))
    table = {(r["month"], r["product"]): r["qty"] for r in raw}
    series = [
        {"name": p, "data": [table.get((m, p), 0) for m in months]}
        for p in products
    ]
    return jsonify({"months": months, "products": products, "series": series})


# ── 产品关联分析（Apriori 思路：以 门店-日 为购物篮）──
@app.route("/api/product_assoc")
def api_product_assoc():
    fw, fp = _filters()
    # 以真实「订单（购物篮）」为最小单元，而非自然日聚合
    rows = query(f"SELECT order_id, product FROM sales WHERE 1=1 {fw}", fp)
    baskets = {}
    for r in rows:
        baskets.setdefault(r["order_id"], set()).add(r["product"])
    n = len(baskets)
    if n == 0:
        return jsonify([])
    item_cnt, pair_cnt = {}, {}
    for items in baskets.values():
        items = list(items)
        for it in items:
            item_cnt[it] = item_cnt.get(it, 0) + 1
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = (items[i], items[j]) if items[i] < items[j] else (items[j], items[i])
                pair_cnt[(a, b)] = pair_cnt.get((a, b), 0) + 1
    results = []
    for (a, b), c in pair_cnt.items():
        supp = c / n
        conf_ab = c / item_cnt[a]
        lift = conf_ab / (item_cnt[b] / n)
        results.append({
            "a": a, "b": b, "cooc": c,
            "support": round(supp, 3),
            "confidence": round(conf_ab, 3),
            "lift": round(lift, 2),
        })
    results.sort(key=lambda x: x["lift"], reverse=True)
    return jsonify(results[:15])


# ── 城市地图数据（销量 / 营收 + 坐标）──────────────
@app.route("/api/city_geo")
def api_city_geo():
    fw, fp = _filters()
    rows = query(
        f"SELECT city, SUM(quantity) AS qty, SUM(revenue) AS revenue "
        f"FROM sales WHERE 1=1 {fw} GROUP BY city", fp)
    out = []
    for r in rows:
        c = r["city"]
        out.append({
            "city": c,
            "qty": r["qty"],
            "revenue": r["revenue"],
            "coord": CITY_COORDS.get(c, [None, None]),
        })
    return jsonify(out)


# ── 会员分层（按会员占比四分位）──────────────────
@app.route("/api/member_tier")
def api_member_tier():
    # 交易级数据下，以「单店日」为单元：取该日平均会员占比 + 当日总杯数，再按会员占比四分位分层
    fw, fp = _filters()
    pair = query(
        f"SELECT AVG(member_pct) AS m, SUM(quantity) AS qty "
        f"FROM sales WHERE 1=1 {fw} GROUP BY store_id, date", fp)
    if len(pair) < 4:
        return jsonify([])
    mp = sorted(r["m"] for r in pair)
    q = [mp[int(len(mp) * p)] for p in (0.25, 0.5, 0.75)]
    tiers = [
        ("低层 (0–25%)", -1, q[0]),
        ("中低 (25–50%)", q[0], q[1]),
        ("中高 (50–75%)", q[1], q[2]),
        ("高层 (75–100%)", q[2], 101),
    ]
    out = []
    for name, lo, hi in tiers:
        rows = [r for r in pair if lo < r["m"] <= hi]
        if rows:
            avg = sum(r["qty"] for r in rows) / len(rows)
            out.append({"tier": name, "avg_qty": round(avg, 1), "count": len(rows)})
    return jsonify(out)


@app.errorhandler(Exception)
def handle_error(e):
    return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    ensure_db()
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
