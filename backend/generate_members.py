"""
会员级建模样本生成器（独立数据集，用于 RFM 分层 与 GBDT 复购/流失预测演示）。

说明：
- 销量主表 sales 是「交易明细」级别；会员分析需要「人」级别特征，二者尺度不同。
- 这里生成一份会员样本（模拟），含 Recency/Frequency/Monetary 等可解释特征，
  流失标签由 logistic 函数 + 噪声生成，确保模型可学到真实信号但非完全可分离（更贴近实战）。
- 固定随机种子，结果可复现；CSV 默认不入库（.gitignore 排除），保证仓库精简、克隆后可重建。
"""
import csv
import math
import os
import random

import numpy as np

random.seed(20260809)
np.random.seed(20260809)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
OUT_CSV = os.path.join(ROOT_DIR, "data", "members.csv")

# 与销量城市权重大致对齐（上海/北京/广深/成都/杭州 等一线更强）
CITIES = ["上海", "北京", "广州", "深圳", "成都", "杭州", "武汉", "南京",
          "西安", "重庆", "长沙", "苏州", "郑州", "天津", "青岛"]
CITY_W = [0.12, 0.11, 0.09, 0.09, 0.08, 0.08, 0.07, 0.06,
          0.06, 0.06, 0.05, 0.05, 0.04, 0.03, 0.03]
CHANNELS = ["门店", "小程序", "外卖平台", "企微私域"]
CHANNEL_W = [0.40, 0.25, 0.20, 0.15]
N = 6000


def make_member(mid):
    city = random.choices(CITIES, CITY_W)[0]
    channel = random.choices(CHANNELS, CHANNEL_W)[0]
    tenure = int(np.random.gamma(2.2, 4.0)) + 1          # 会员时长（月）1~约30
    # 活跃度：长会员 + 私域/小程序渠道更频繁
    ch_boost = 1.0 + (0.35 if channel in ("企微私域", "小程序") else 0.0)
    frequency = max(1, int(np.random.normal(tenure * 0.85 * ch_boost, tenure * 0.3 + 1)))
    aov = float(np.random.normal(24, 4))                 # 客单价 18~32 区间
    aov = min(max(aov, 14), 40)
    monetary = round(frequency * aov * float(np.random.uniform(0.85, 1.15)), 1)
    recency = int(min(np.random.exponential(34) + (0 if channel == "企微私域" else 6), 220))
    promo = round(random.random(), 2)

    # 流失标签：logistic 函数（recency↑、frequency↓、monetary↓、promo↑ → 更易流失）+ 噪声
    logit = (recency - 70) / 22 - (frequency - 9) / 7 + (monetary - 900) / 900 - (promo - 0.5) * 0.4
    p = 1.0 / (1.0 + math.exp(-logit))
    churn = 1 if random.random() < p else 0

    return [mid, city, channel, tenure, frequency, monetary, recency, promo, churn]


def main():
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["member_id", "city", "channel", "tenure_months",
                    "frequency", "monetary", "recency_days", "promo_sensitivity", "churn"])
        for i in range(1, N + 1):
            w.writerow(make_member(i))
    print(f"[members] 生成会员建模样本 {N} 条 -> {OUT_CSV}")


if __name__ == "__main__":
    main()
