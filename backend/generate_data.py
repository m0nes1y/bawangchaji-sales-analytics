# -*- coding: utf-8 -*-
"""
生成「霸王茶姬」模拟销售数据（交易级 / 含购物篮）。

设计目标：
- 15 个城市 = 15 个门店，每个门店常备 6/8 款产品（解决原数据"一城一品"问题）。
- 引入真实「订单（购物篮）」维度：一张订单含 1~4 款产品，产品间存在
  互补（提升度>1）与替代（提升度<1）关系，使 Apriori 能产出有意义规则。
- 保留 日期/城市/门店/天气/季节/节假日/营销活动/会员占比/折扣 等维度，
  保证月度 LAG、季节、节假日、会员分层等原有分析照常成立。
- 固定随机种子，保证每次重建数据一致、数值可复现。

产物：../data/bawangchaji_sales.csv （新增 订单号 列）
"""
import csv
import os
import random
from collections import defaultdict
from datetime import date, timedelta

SEED = 20250808
random.seed(SEED)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(BASE_DIR), "data", "bawangchaji_sales.csv")

# ── 产品主数据（名称 / 类别 / 单价）──────────────
PRODUCTS = [
    ("伯牙绝弦", "原叶鲜奶茶", 16.0),
    ("桂花弄", "原叶鲜奶茶", 17.8),
    ("茉莉雪芽", "原叶鲜奶茶", 17.2),
    ("鸭屎香柠檬茶", "柠檬茶", 20.4),
    ("青柑普洱", "现萃茶", 18.6),
    ("桃桃乌龙", "鲜果茶", 19.2),
    ("满杯橙意", "鲜果茶", 18.0),
    ("芝芝葡萄", "鲜果茶", 23.0),
]
P_NAMES = [p[0] for p in PRODUCTS]
P_CAT = {p[0]: p[1] for p in PRODUCTS}
P_PRICE = {p[0]: p[2] for p in PRODUCTS}

# 基础人气权重（单店选购概率，不含亲和）
BASE_W = {
    "伯牙绝弦": 30, "桂花弄": 14, "茉莉雪芽": 16, "鸭屎香柠檬茶": 18,
    "青柑普洱": 10, "桃桃乌龙": 15, "满杯橙意": 12, "芝芝葡萄": 20,
}

# 产品亲和矩阵（购物篮内共现偏置）：>1 互补，<1 替代
AFF = defaultdict(lambda: 1.0)
def set_aff(a, b, v):
    AFF[(a, b)] = v
    AFF[(b, a)] = v
set_aff("伯牙绝弦", "桂花弄", 4.0)        # 招牌双拼
set_aff("芝芝葡萄", "满杯橙意", 4.0)      # 鲜果双拼
set_aff("鸭屎香柠檬茶", "青柑普洱", 3.5)  # 柠檬+现萃
set_aff("桃桃乌龙", "茉莉雪芽", 3.5)      # 果茶+原叶
set_aff("伯牙绝弦", "芝芝葡萄", 2.5)      # 招牌+果茶
set_aff("伯牙绝弦", "茉莉雪芽", 0.25)     # 原叶替代
set_aff("桂花弄", "茉莉雪芽", 0.30)       # 原叶替代
set_aff("芝芝葡萄", "桃桃乌龙", 0.30)     # 鲜果替代

# ── 城市 / 门店 ────────────────────────────────
# tier1 高客流城市，tier2 一般；每城 1 店，常备 6/8 款（必含招牌）
CITIES = {
    "北京": ("BJ-001", 12), "上海": ("SH-001", 14), "广州": ("GZ-001", 13),
    "深圳": ("SZ-001", 13), "成都": ("CD-001", 12), "杭州": ("HZ-001", 11),
    "武汉": ("WH-001", 10), "南京": ("NJ-001", 10), "重庆": ("CQ-001", 9),
    "西安": ("XA-001", 8), "苏州": ("SZ-002", 8), "天津": ("TJ-001", 8),
    "长沙": ("CS-001", 9), "郑州": ("ZZ-001", 7), "青岛": ("QD-001", 7),
}
# 每店常备产品：招牌 + 5 款随机（种子固定）
STORE_ASSORT = {}
for city, (sid, _) in CITIES.items():
    others = [p for p in P_NAMES if p != "伯牙绝弦"]
    random.shuffle(others)
    STORE_ASSORT[city] = ["伯牙绝弦"] + others[:5]

# ── 天气 / 节假日 / 营销 ─────────────────────────
WEATHERS = ["晴", "多云", "阴", "小雨", "雷阵雨"]
HOLIDAYS = {
    "2025-01-01": ("元旦", "元旦促销"),
    "2025-01-29": ("春节", "春节不打烊"), "2025-01-30": ("春节", "春节不打烊"),
    "2025-01-31": ("春节", "春节不打烊"), "2025-02-01": ("春节", "春节不打烊"),
    "2025-02-02": ("春节", "春节不打烊"), "2025-02-03": ("春节", "春节不打烊"),
    "2025-04-04": ("清明", "清明踏青季"), "2025-04-05": ("清明", "清明踏青季"),
    "2025-05-01": ("劳动节", "五一狂欢"), "2025-05-02": ("劳动节", "五一狂欢"),
    "2025-05-03": ("劳动节", "五一狂欢"),
    "2025-05-31": ("端午", "端午安康"), "2025-06-01": ("端午", "端午安康"),
    "2025-10-01": ("国庆", "国庆黄金周"), "2025-10-02": ("国庆", "国庆黄金周"),
    "2025-10-03": ("国庆", "国庆黄金周"), "2025-10-04": ("国庆", "国庆黄金周"),
    "2025-10-05": ("国庆", "国庆黄金周"),
    "2025-10-06": ("中秋", "中秋团圆"),
}
MEMBER_BASE = {  # 各城市会员占比基线(%)
    "北京": 72, "上海": 70, "广州": 64, "深圳": 68, "成都": 66, "杭州": 67,
    "武汉": 61, "南京": 63, "重庆": 60, "西安": 58, "苏州": 62, "天津": 59,
    "长沙": 65, "郑州": 57, "青岛": 56,
}

def season_of(m):
    return {12: "冬季", 1: "冬季", 2: "冬季", 3: "春季", 4: "春季", 5: "春季",
            6: "夏季", 7: "夏季", 8: "夏季", 9: "秋季", 10: "秋季", 11: "秋季"}[m]

def pick_weather(m):
    # 夏季多雷雨，冬季多晴
    pool = WEATHERS[:]
    if m in (6, 7, 8):
        pool = ["晴", "多云", "阴", "小雨", "雷阵雨", "雷阵雨"]
    elif m in (12, 1, 2):
        pool = ["晴", "晴", "多云", "阴", "小雨"]
    return random.choice(pool)

# 天气对单店订单量的影响（雨天客流下降，晴好正常）
WEATHER_MULT = {"晴": 1.0, "多云": 0.97, "阴": 0.9, "小雨": 0.8, "雷阵雨": 0.7}

def weighted_choice(items, weights):
    return random.choices(items, weights=weights, k=1)[0]

# ── 生成日期样本（每周约 2 天，覆盖全年 12 月 / 4 季）──
def sample_dates(year=2025):
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    days = []
    d = start
    weekday_count = defaultdict(int)
    while d <= end:
        # 每周随机挑 2 天（含周末概率更高）
        if weekday_count[d.isocalendar()[1]] < 2:
            # 周末更可能被选中
            if d.weekday() >= 5 or random.random() < 0.25:
                days.append(d)
                weekday_count[d.isocalendar()[1]] += 1
        d += timedelta(days=1)
    return days

def build_order(city, d, seq, promo_depth, weather, campaign):
    """构造一张订单（1~4 款产品），返回若干行。promo_depth=None 表示当日无促销。
    campaign 在「单店日」层级确定并传入，保证与节假日/会员日口径一致（避免按订单拆分导致聚合被腰斩）。"""
    sid = CITIES[city][0]
    assort = STORE_ASSORT[city]
    # 主品
    primary = weighted_choice(assort, [BASE_W[p] for p in assort])
    basket = [primary]
    # 追加附属品，受亲和偏置影响
    max_extra = random.choices([0, 1, 2, 3], weights=[45, 35, 15, 5])[0]
    candidates = [p for p in assort if p != primary]
    for _ in range(max_extra):
        if not candidates:
            break
        w = [BASE_W[c] * AFF[(primary, c)] for c in candidates]
        nxt = weighted_choice(candidates, w)
        basket.append(nxt)
        candidates = [c for c in candidates if c != nxt]
    # 订单级属性
    ds = d.isoformat()
    hol = HOLIDAYS.get(ds, (None, None))
    is_hol = "是" if hol[0] else "否"
    member = max(20, min(95, int(MEMBER_BASE[city] + random.gauss(0, 6))))
    season = season_of(d.month)
    order_id = f"{sid}-{ds}-{seq:04d}"
    rows = []
    for p in basket:
        qty = random.choices([1, 2, 3], weights=[70, 22, 8])[0]
        # 促销日：约 65% 明细行享受当日促销折扣，其余原价
        disc = 0.0
        if promo_depth is not None and random.random() < 0.65:
            disc = promo_depth
        price = P_PRICE[p]
        revenue = round(price * qty * (1 - disc), 1)
        rows.append({
            "日期": ds, "城市": city, "门店编号": sid, "订单号": order_id,
            "产品名称": p, "产品类别": P_CAT[p], "单价(元)": price,
            "销量(杯)": qty, "折扣": disc, "实付金额(元)": revenue,
            "会员占比(%)": member, "天气": weather, "季节": season,
            "是否节假日": is_hol, "营销活动": campaign,
        })
    return rows

def main():
    dates = sample_dates(2025)
    rows = []
    for city, (sid, base) in CITIES.items():
        for d in dates:
            is_weekend = d.weekday() >= 5
            hol = d.isoformat() in HOLIDAYS
            m = d.month
            # 促销日模型：约 40% 的日子有促销，促销越深 → 单店订单越多
            is_promo = random.random() < 0.4
            promo_depth = None
            if is_promo:
                promo_depth = random.choices([0.05, 0.10, 0.12, 0.15],
                                             weights=[40, 30, 20, 10])[0]
            n = base
            if is_weekend: n = int(n * 1.3)
            if hol: n = int(n * 1.6)
            if m in (6, 7, 8): n = int(n * 1.2)
            if d.day == 18: n = int(n * 1.15)
            w = pick_weather(m)  # 单店日统一天气（同时用于客流与明细标注）
            n = int(n * WEATHER_MULT[w])                       # 天气影响
            if is_promo: n = int(n * (1 + promo_depth * 2.2))  # 折扣驱动增量
            # 会员占比越高的城市，单店客流越高（会员运营杠杆）
            member_factor = 0.75 + MEMBER_BASE[city] / 100 * 0.6
            n = int(n * member_factor)
            n = max(2, int(n * random.uniform(0.8, 1.2)))
            # 营销活动在「单店日」层级确定（与节假日/会员日口径一致）
            hol_campaign = HOLIDAYS.get(d.isoformat(), (None, None))[1]
            campaign = hol_campaign or ""
            if not campaign and d.day == 18:
                campaign = "会员日"
            if not campaign and m in (6, 7) and random.random() < 0.5:
                campaign = "夏日新品季"
            for seq in range(1, n + 1):
                rows.extend(build_order(city, d, seq, promo_depth, w, campaign))
    # 写 CSV
    fields = ["日期", "城市", "门店编号", "订单号", "产品名称", "产品类别",
              "单价(元)", "销量(杯)", "折扣", "实付金额(元)", "会员占比(%)",
              "天气", "季节", "是否节假日", "营销活动"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"[gen] 写入 {len(rows)} 行 -> {OUT}")
    print(f"[gen] 采样日期 {len(dates)} 天，覆盖月份 {sorted({d.month for d in dates})} / 季节 {sorted({season_of(d.month) for d in dates})}")

    # ── 自检：在生成数据上跑一次 Apriori，确认提升度有分化 ──
    baskets = defaultdict(set)
    with open(OUT, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            baskets[r["订单号"]].add(r["产品名称"])
    n = len(baskets)
    item_cnt, pair_cnt = defaultdict(int), defaultdict(int)
    for items in baskets.values():
        items = list(items)
        for it in items: item_cnt[it] += 1
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = (items[i], items[j]) if items[i] < items[j] else (items[j], items[i])
                pair_cnt[(a, b)] += 1
    res = []
    for (a, b), c in pair_cnt.items():
        supp = c / n
        conf = c / item_cnt[a]
        lift = conf / (item_cnt[b] / n)
        res.append((a, b, c, round(supp, 3), round(conf, 3), round(lift, 2)))
    res.sort(key=lambda x: x[5], reverse=True)
    print("\n[自检] 提升度 TOP5（互补）:")
    for r in res[:5]: print("  ", r)
    print("[自检] 提升度 BOTTOM5（替代）:")
    for r in res[-5:]: print("  ", r)
    lifts = [r[5] for r in res]
    print(f"[自检] 共 {len(res)} 对，lift 范围 {min(lifts)}~{max(lifts)}")

    # ── 自检：5 张对比图（按 单店日 聚合的平均杯数）是否分化 ──
    import csv as _csv
    from collections import defaultdict as _dd
    store_day = _dd(lambda: _dd(float))  # (city,date) -> {qty, rev, member, n}
    meta = _dd(dict)                     # (city,date) -> {weather,is_holiday,campaign,disc}
    with open(OUT, encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            k = (r["城市"], r["日期"])
            store_day[k]["qty"] += float(r["销量(杯)"])
            store_day[k]["rev"] += float(r["实付金额(元)"])
            store_day[k]["member"] += float(r["会员占比(%)"]) * float(r["销量(杯)"])
            store_day[k]["qn"] += float(r["销量(杯)"])
            meta[k]["weather"] = r["天气"]
            meta[k]["is_holiday"] = r["是否节假日"]
            meta[k]["campaign"] = r["营销活动"] or "无活动"
            meta[k]["disc"] = max(meta[k].get("disc", 0.0), float(r["折扣"]))
    sd = []
    for k, v in store_day.items():
        sd.append({
            "city": k[0], "date": k[1], "qty": v["qty"], "rev": v["rev"],
            "member": v["member"] / v["qn"], "weather": meta[k]["weather"],
            "is_holiday": meta[k]["is_holiday"], "campaign": meta[k]["campaign"],
            "disc": meta[k]["disc"],
        })
    def grp(key):
        g = _dd(list)
        for x in sd: g[x[key]].append(x["qty"])
        return {k: round(sum(v) / len(v), 1) for k, v in g.items()}
    print("\n[自检] 天气→单店日均杯数:", grp("weather"))
    print("[自检] 节假日→单店日均杯数:", grp("is_holiday"))
    print("[自检] 营销活动→单店日均杯数:", grp("campaign"))
    print("[自检] 折扣档→单店日均杯数:", grp("disc"))
    # 会员分层（按单店日会员占比四分位）
    ms = sorted(x["member"] for x in sd)
    q = [ms[int(len(ms) * p)] for p in (0.25, 0.5, 0.75)]
    tiers = [[] for _ in range(4)]
    for x in sd:
        i = sum(x["member"] > t for t in q)
        tiers[i].append(x["qty"])
    print("[自检] 会员四分位→单店日均杯数:", [round(sum(t) / len(t), 1) if t else 0 for t in tiers])

if __name__ == "__main__":
    main()
