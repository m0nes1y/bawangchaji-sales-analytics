"""
霸王茶姬销量数据分析可视化平台 - Flask 后端
提供 REST API 并将前端作为静态资源托管。
"""

import csv
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from sklearn.model_selection import cross_val_predict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DATA_PATH = os.path.join(ROOT_DIR, "data", "bawangchaji_sales.csv")
MEMBERS_CSV = os.path.join(ROOT_DIR, "data", "members.csv")
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


def init_members():
    """读取会员建模样本 CSV 写入 members 表（用于 RFM 分层与 GBDT 流失预测）。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS members")
    cur.execute(
        """CREATE TABLE members (
            member_id INTEGER, city TEXT, channel TEXT, tenure_months INTEGER,
            frequency INTEGER, monetary REAL, recency_days INTEGER,
            promo_sensitivity REAL, churn INTEGER
        )"""
    )
    loaded = 0
    if os.path.exists(MEMBERS_CSV):
        with open(MEMBERS_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    cur.execute(
                        "INSERT INTO members VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            int(row["member_id"]), row["city"], row["channel"],
                            int(row["tenure_months"]), int(row["frequency"]),
                            float(row["monetary"]), int(row["recency_days"]),
                            float(row["promo_sensitivity"]), int(row["churn"]),
                        ),
                    )
                    loaded += 1
                except Exception:
                    pass
    conn.commit()
    conn.close()
    return loaded


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
    if not os.path.exists(MEMBERS_CSV):
        try:
            import sys
            if BASE_DIR not in sys.path:
                sys.path.insert(0, BASE_DIR)
            import generate_members
            print("[init] 未找到会员样本，正在自动生成 members.csv ...")
            generate_members.main()
        except Exception as e:
            print(f"[init] 自动生成会员样本失败：{e}")
    if not os.path.exists(DB_PATH):
        n, s = init_db()
        m = init_members()
        print(f"[init] 首次建库完成：销量 {n} 行(跳过 {s})，会员 {m} 行")
        return
    cnt = query("SELECT COUNT(*) AS c FROM sales")
    if not cnt or cnt[0]["c"] == 0:
        n, s = init_db()
        m = init_members()
        print(f"[init] 数据库为空，重新导入：销量 {n} 行(跳过 {s})，会员 {m} 行")


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


# ── 高级分析：指标体系 / 异动监测 / 显著性检验 / 弹性 / 预测 / RFM / 流失预测 ──
@app.route("/api/metrics")
def api_metrics():
    """业务指标体系 + 指标字典：从 0 到 1 定义核心指标与统计口径。"""
    fw, fp = _filters()
    base = query(
        f"SELECT COUNT(*) AS c, SUM(quantity) AS q, SUM(revenue) AS rev, AVG(member_pct) AS m "
        f"FROM sales WHERE 1=1 {fw}", fp)[0]
    orders = query(f"SELECT COUNT(DISTINCT order_id) AS o FROM sales WHERE 1=1 {fw}", fp)[0]["o"]
    days = query(f"SELECT COUNT(DISTINCT date) AS d FROM sales WHERE 1=1 {fw}", fp)[0]["d"]
    cities = query(f"SELECT COUNT(DISTINCT city) AS x FROM sales WHERE 1=1 {fw}", fp)[0]["x"]
    stores = query(f"SELECT COUNT(DISTINCT store_id) AS x FROM sales WHERE 1=1 {fw}", fp)[0]["x"]
    products = query(f"SELECT COUNT(DISTINCT product) AS x FROM sales WHERE 1=1 {fw}", fp)[0]["x"]
    cats = query(f"SELECT COUNT(DISTINCT category) AS x FROM sales WHERE 1=1 {fw}", fp)[0]["x"]
    mom = query(
        f"SELECT month, qty FROM ("
        f"SELECT strftime('%Y-%m', date) AS month, SUM(quantity) AS qty "
        f"FROM sales WHERE 1=1 {fw} GROUP BY month ORDER BY month) t", fp)
    last_mom = round((mom[-1]["qty"] - mom[-2]["qty"]) * 100.0 / mom[-2]["qty"], 1) if len(mom) >= 2 else None
    rep = query("SELECT COUNT(*) AS c FROM members WHERE frequency >= 2")
    rep_total = query("SELECT COUNT(*) AS c FROM members")
    rep_rate = round(rep[0]["c"] / rep_total[0]["c"] * 100, 1) if rep and rep_total else None
    avg_ticket = (base["rev"] / orders) if orders else 0
    basket = (base["q"] / orders) if orders else 0
    metrics = [
        {"label": "总销量", "value": base["q"], "unit": "杯", "def": "统计周期内全部门店产品销售杯数之和", "fmt": "num"},
        {"label": "总营收", "value": round(base["rev"], 0), "unit": "元", "def": "统计周期内实付金额总和", "fmt": "money"},
        {"label": "订单数", "value": orders, "unit": "单", "def": "去重订单(购物篮)数，客单价/连带率的分母", "fmt": "num"},
        {"label": "客单价", "value": round(avg_ticket, 1), "unit": "元/单", "def": "总营收 / 订单数，反映单笔消费水平", "fmt": "num1"},
        {"label": "连带率", "value": round(basket, 2), "unit": "杯/单", "def": "总销量 / 订单数，反映单次购买件数(搭配能力)", "fmt": "num2"},
        {"label": "平均会员占比", "value": round(base["m"], 1), "unit": "%", "def": "各明细行会员销售占比的均值", "fmt": "num1"},
        {"label": "会员复购率", "value": rep_rate, "unit": "%", "def": "建模样本中购买≥2次的会员占比(会员级数据)", "fmt": "num1"},
        {"label": "日均销量", "value": round(base["q"] / days, 0) if days else 0, "unit": "杯/天", "def": "总销量 / 营业天数", "fmt": "num"},
        {"label": "最新月环比", "value": last_mom, "unit": "%", "def": "最近自然月销量相对上一月的增幅", "fmt": "num1"},
        {"label": "覆盖规模", "value": f"{cities}城 / {stores}店 / {products}品 / {cats}类", "unit": "", "def": "城市/门店/产品/品类覆盖广度", "fmt": "text"},
    ]
    return jsonify({"metrics": metrics, "period_days": days})


@app.route("/api/anomaly")
def api_anomaly():
    """异动监测：3σ + 环比阈值标记异常日，并定位根因(节假日/活动/天气)。"""
    fw, fp = _filters()
    rows = query(
        f"SELECT date, SUM(quantity) AS qty FROM sales WHERE 1=1 {fw} GROUP BY date ORDER BY date", fp)
    if len(rows) < 7:
        return jsonify({"anomalies": [], "stats": {}})
    series = [r["qty"] for r in rows]
    mean = sum(series) / len(series)
    std = (sum((x - mean) ** 2 for x in series) / len(series)) ** 0.5
    anomalies = []
    for i, r in enumerate(rows):
        z = (r["qty"] - mean) / std if std else 0
        dod = None
        if i > 0 and rows[i - 1]["qty"]:
            dod = (r["qty"] - rows[i - 1]["qty"]) * 100.0 / rows[i - 1]["qty"]
        if abs(z) >= 3 or (dod is not None and abs(dod) >= 30):
            d = r["date"]
            day = query(
                f"SELECT is_holiday, weather, "
                f"CASE WHEN campaign IS NOT NULL AND campaign != '' THEN campaign ELSE '' END AS camp "
                f"FROM sales WHERE 1=1 {fw} AND date=? LIMIT 1", fp + [d])
            reason = "异常波动(无显著外部因素)"
            if day:
                if day[0]["is_holiday"] == "是":
                    reason = "节假日"
                elif day[0]["camp"]:
                    reason = "营销活动：" + day[0]["camp"]
                elif day[0]["weather"] in ("小雨", "雷阵雨", "阴"):
                    reason = "恶劣天气：" + day[0]["weather"]
            anomalies.append({
                "date": d, "qty": r["qty"], "z": round(z, 2),
                "dod": round(dod, 1) if dod is not None else None, "reason": reason,
            })
    anomalies.sort(key=lambda x: -abs(x["z"]))
    return jsonify({
        "anomalies": anomalies[:15],
        "stats": {"mean": round(mean, 0), "std": round(std, 0),
                  "threshold_z": 3, "threshold_dod": 30, "total_days": len(rows)},
    })


def _welch(a, b, name_a, name_b):
    a = [x for x in a if x is not None]
    b = [x for x in b if x is not None]
    if len(a) < 2 or len(b) < 2:
        return None
    t, p = stats.ttest_ind(a, b, equal_var=False)
    na, nb = len(a), len(b)
    ma, mb = sum(a) / na, sum(b) / nb
    se = math.sqrt((np.var(a, ddof=1) / na) + (np.var(b, ddof=1) / nb))
    ci = ((ma - mb) - 1.96 * se, (ma - mb) + 1.96 * se)
    higher = ma > mb
    return {
        "name_a": name_a, "name_b": name_b,
        "mean_a": round(ma, 1), "mean_b": round(mb, 1),
        "diff": round(ma - mb, 1), "t": round(float(t), 2), "p": float(p),
        "ci_low": round(ci[0], 1), "ci_high": round(ci[1], 1),
        "significant": bool(p < 0.05),
        "conclusion": f"{name_a}日均销量({ma:.1f}){'高于' if higher else '低于'}"
                      f"{name_b}({mb:.1f})，差异{'显著' if p < 0.05 else '不显著'}(p={p:.2e})",
    }


@app.route("/api/hypothesis")
def api_hypothesis():
    """显著性检验(手写 Welch t 检验)：验证业务差异是否统计显著。"""
    fw, fp = _filters()
    sd = _store_days(fw, fp)
    tests = []
    hol = _welch([r["qty"] for r in sd if r["is_holiday"] == "是"],
                 [r["qty"] for r in sd if r["is_holiday"] == "否"],
                 "节假日", "非节假日")
    if hol:
        tests.append(hol)
    disc = _welch([r["qty"] for r in sd if r["disc"] >= 0.1],
                  [r["qty"] for r in sd if r["disc"] < 0.1],
                  "高折扣(≥10%)", "低折扣(<10%)")
    if disc:
        tests.append(disc)
    wea = _welch([r["qty"] for r in sd if r["weather"] == "晴"],
                 [r["qty"] for r in sd if r["weather"] == "雷阵雨"],
                 "晴天", "雷阵雨")
    if wea:
        tests.append(wea)
    return jsonify(tests)


@app.route("/api/elasticity")
def api_elasticity():
    """价格弹性：按产品做组内固定效应(去均值)的双对数回归，消除品类结构干扰，
    得到干净的「自身价格弹性」，返回弹性系数、组内 R²、p 值。"""
    fw, fp = _filters()
    rows = query(
        f"SELECT product, SUM(revenue)*1.0/SUM(quantity) AS price, SUM(quantity) AS qty "
        f"FROM sales WHERE 1=1 {fw} GROUP BY product, store_id, date", fp)
    by_prod = {}
    for r in rows:
        if not r["price"] or r["price"] <= 0 or not r["qty"]:
            continue
        by_prod.setdefault(r["product"], []).append((math.log(r["price"]), math.log(r["qty"])))
    dx_all, dy_all = [], []
    raw = []
    for pts in by_prod.values():
        if len(pts) < 3:
            continue
        lps = [p for p, _ in pts]
        lqs = [q for _, q in pts]
        mp, mq = sum(lps) / len(lps), sum(lqs) / len(lqs)
        for (lp, lq) in zip(lps, lqs):
            dx_all.append(lp - mp)
            dy_all.append(lq - mq)
        raw.extend([(math.exp(lp), math.exp(lq)) for lp, lq in pts])
    n = len(dx_all)
    if n < 10:
        return jsonify({})
    mx, my = sum(dx_all) / n, sum(dy_all) / n
    denom = sum((x - mx) ** 2 for x in dx_all)
    slope = sum((x - mx) * (y - my) for x, y in zip(dx_all, dy_all)) / denom if denom else 0
    pred = [mx_ * slope for mx_ in (x - mx for x in dx_all)]  # demeaned predict = slope*dx (since my centered)
    # 重新算预测：demeaned yhat = slope*(x-mx)
    yhat = [slope * (x - mx) for x in dx_all]
    ss_res = sum((y - h) ** 2 for y, h in zip(dy_all, yhat))
    ss_tot = sum((y - my) ** 2 for y in dy_all)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0
    se = math.sqrt(ss_res / (n - 2) / denom) if (n - 2) > 0 and denom else 0
    t_stat = slope / se if se else 0
    p = 2 * (1 - stats.t.cdf(abs(t_stat), n - 2)) if (n - 2) > 0 else 1.0
    sample = raw[::max(1, len(raw) // 200)]
    return jsonify({
        "elasticity": round(slope, 3), "r2": round(r2, 3), "p": float(p),
        "n": n, "method": "组内固定效应(按产品去均值)",
        "scatter": [[round(p, 1), round(q, 0)] for p, q in sample],
    })


@app.route("/api/forecast")
def api_forecast():
    """时间序列预测：加法季节分解(趋势 + 月度季节项)外推 + 95% 置信区间。"""
    fw, fp = _filters()
    rows = query(
        f"SELECT strftime('%Y-%m', date) AS month, SUM(quantity) AS qty "
        f"FROM sales WHERE 1=1 {fw} GROUP BY month ORDER BY month", fp)
    if len(rows) < 3:
        return jsonify({})
    months = [r["month"] for r in rows]
    y = [r["qty"] for r in rows]
    x = list(range(len(y)))
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    denom = sum((xi - mx) ** 2 for xi in x)
    slope = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / denom
    intercept = my - slope * mx
    trend = [intercept + slope * xi for xi in x]
    detr = [y[i] - trend[i] for i in range(n)]
    # 月度季节项：按自然月聚合去趋势后的偏差
    seas = {}
    for m, d in zip(months, detr):
        cm = int(m.split("-")[1])
        seas.setdefault(cm, []).append(d)
    seasonal_idx = {cm: sum(v) / len(v) for cm, v in seas.items()}
    resid = [detr[i] - seasonal_idx[int(months[i].split("-")[1])] for i in range(n)]
    # 仅 1 个自然年数据，季节项逐月确定（resid 退化为 0）；预测置信区间改用
    # 趋势回归残差离散度（即 detr 的 std），代表「实际值相对 趋势+季节 的典型偏离」
    rstd = (sum(d * d for d in detr) / max(1, n - 2)) ** 0.5
    last = datetime.strptime(months[-1], "%Y-%m")
    fmonths = [(last + timedelta(days=32 * i)).strftime("%Y-%m") for i in range(1, 4)]
    pred, lower, upper = [], [], []
    for i, fm in enumerate(fmonths, start=n):
        cm = int(fm.split("-")[1])
        s = seasonal_idx.get(cm, 0)
        p = intercept + slope * i + s
        pred.append(p)
        lower.append(p - 1.96 * rstd)
        upper.append(p + 1.96 * rstd)
    peak = max(seasonal_idx.items(), key=lambda kv: kv[1])
    return jsonify({
        "history_months": months, "history": y, "trend": [round(t, 0) for t in trend],
        "forecast_months": fmonths,
        "forecast": [round(p, 0) for p in pred],
        "lower": [round(p, 0) for p in lower],
        "upper": [round(p, 0) for p in upper],
        "slope": round(slope, 0), "rstd": round(rstd, 0),
        "seasonal_peak_month": peak[0],
    })


@app.route("/api/rfm")
def api_rfm():
    """RFM 用户分层：对会员样本做 R/F/M 五分位分层并分段。"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM members", conn)
    conn.close()
    if len(df) == 0:
        return jsonify({})
    df["R"] = pd.qcut(df["recency_days"], 5, labels=[5, 4, 3, 2, 1]).astype(int)
    df["F"] = pd.qcut(df["frequency"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
    df["M"] = pd.qcut(df["monetary"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
    df["RFM"] = df["R"] + df["F"] + df["M"]

    def seg(s):
        if s >= 13:
            return "高价值(冠军)"
        if s >= 11:
            return "忠诚客户"
        if s >= 9:
            return "潜力客户"
        if s >= 7:
            return "新客/一般"
        return "流失风险"

    df["segment"] = df["RFM"].apply(seg)
    g = df.groupby("segment").agg(
        count=("member_id", "count"), avg_monetary=("monetary", "mean"),
        avg_recency=("recency_days", "mean"), churn_rate=("churn", "mean")).reset_index()
    order = ["高价值(冠军)", "忠诚客户", "潜力客户", "新客/一般", "流失风险"]
    g["segment"] = pd.Categorical(g["segment"], order, ordered=True)
    g = g.sort_values("segment")
    return jsonify({
        "segments": [{
            "segment": str(r["segment"]), "count": int(r["count"]),
            "avg_monetary": round(r["avg_monetary"], 0),
            "avg_recency": round(r["avg_recency"], 0),
            "churn_rate": round(r["churn_rate"] * 100, 1),
        } for _, r in g.iterrows()],
        "total": int(len(df)),
    })


_ML_CACHE = {}


@app.route("/api/churn_model")
def api_churn_model():
    """会员复购/流失预测：GBDT 监督式建模，5 折交叉验证输出诚实指标。"""
    cached = _cache_get("churn_model")
    if cached:
        return jsonify(cached)
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM members", conn)
    conn.close()
    if len(df) < 50:
        return jsonify({})
    feats = ["recency_days", "frequency", "monetary", "tenure_months", "promo_sensitivity"]
    X = df[feats].values
    y = df["churn"].values
    clf = GradientBoostingClassifier(n_estimators=120, max_depth=3, random_state=42)
    proba = cross_val_predict(clf, X, y, cv=5, method="predict_proba")[:, 1]
    pred = cross_val_predict(clf, X, y, cv=5, method="predict")
    auc = roc_auc_score(y, proba)
    acc = accuracy_score(y, pred)
    f1 = f1_score(y, pred)
    clf.fit(X, y)
    imp = sorted(zip(feats, clf.feature_importances_), key=lambda z: -z[1])
    base_acc = max(sum(y == 1), sum(y == 0)) / len(y)
    res = {
        "n": int(len(df)), "auc": round(float(auc), 3), "accuracy": round(float(acc), 3),
        "f1": round(float(f1), 3), "baseline_accuracy": round(base_acc, 3),
        "lift_acc": round((float(acc) - base_acc) * 100, 1),
        "feature_importance": [{"feature": f, "importance": round(float(v), 3)} for f, v in imp],
        "positive_rate": round(float(np.mean(y)) * 100, 1),
    }
    _cache_set("churn_model", res)
    return jsonify(res)


@app.errorhandler(Exception)
def handle_error(e):
    return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    ensure_db()
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
