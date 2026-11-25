"""10 项指标的打分函数。

统一约定
--------
- 返回 1—5 的整数风险分（1 最好、5 最差），或 None 表示"无法评价"。
  None 会被汇总层剔除权重并记入"数据问题"；它**不等于**低风险。
- 静态指标输入单个值；动态指标输入该管段评价窗口内的一列监测值（pandas Series），
  同时返回用于解释的统计量字典（均值、CV 等），会原样写进结果表。
- 每个函数的 docstring 给出分档规则和对应原程序行号
  （02_核查证据/程序包/运行程序结果260918/V1.0管道健康评价模型.py）。

与原程序的差异
--------------
仅 age_score 把隐式的 datetime.now().year 改为显式参数 as_of_year（台账 P04）。
其余函数逐行等价，包括已知缺陷（标注 [已知问题 Pxx]），以保证与基线逐格一致；
修正这些缺陷前应先与评分责任方确认口径，并保留本版作为回归对照。
"""
from __future__ import annotations

import pandas as pd

from .config import MATERIAL_DEFAULT_SCORE, MATERIAL_SCORES

# ===========================================================================
# 一、静态指标（来自静态表，每条管段一个值）
# ===========================================================================


def material_score(material) -> int | None:
    """材质风险（权重 0.13）。原 L98-102。

    规则（精确匹配，先 strip）：
        钢塑 / 球墨铸铁 / PE / PPR / 涂塑 → 2
        钢                               → 3
        铸铁                             → 4
        其他非空文本                      → 3（默认）
        空值（NaN/None）                  → None

    注意：不做大小写与别名归一（"pe"、"PE管"都会落到默认 3），
    接入真实台账前需要补材质别名映射表。
    """
    if pd.isna(material):
        return None
    return MATERIAL_SCORES.get(str(material).strip(), MATERIAL_DEFAULT_SCORE)


def age_score(commission_year, as_of_year: int) -> int | None:
    """管龄风险（权重 0.13）。原 L105-124。

    管龄 = as_of_year − 投运年份：
        < 20 年 → 1
        20—29   → 2
        30—39   → 3
        40—49   → 4
        ≥ 50    → 5
        管龄 < 0（投运年份在评价时点之后）→ None
        投运年份为空或无法转为数字        → None

    与原程序差异：原用 datetime.now().year，结果随运行年份漂移；
    这里显式传入评价时点年份 as_of_year，回放同一时点可得同一结果（P04）。
    """
    if pd.isna(commission_year):
        return None
    try:
        year = int(float(commission_year))
    except (ValueError, TypeError):
        return None
    age = as_of_year - year
    if age < 0:
        return None
    if age < 20:
        return 1
    elif age < 30:
        return 2
    elif age < 40:
        return 3
    elif age < 50:
        return 4
    else:
        return 5


def diameter_score(diameter) -> int | None:
    """管径风险（权重 0.07，单位 mm）。原 L127-139。

    规则：
        无法转为数字 → None
        ≤ 0          → None
        < 100        → 4
        100—299      → 2
        ≥ 300        → 1

    [已知问题 P01] NaN 能通过 float()，且 NaN 与任何数比较都是 False，
    所以空管径会一路落到 else 返回 1（最低风险），且不记入"数据问题"。
    为与基线逐格一致此处保留原行为；修正方式：float() 之后加
    `if math.isnan(x): return None`。
    """
    try:
        x = float(diameter)
    except (ValueError, TypeError):
        return None
    if x <= 0:
        return None
    if x < 100:
        return 4
    elif x < 300:
        return 2
    else:
        return 1


def depth_score(depth_start, depth_end) -> int | None:
    """埋深风险（权重 0.06，单位 m）。原 L142-154。

    取起点、终点埋深的算术平均 d：
        任一端无法转为数字 → None
        d ≤ 0              → None
        d < 0.5            → 4
        0.5 ≤ d < 1.0      → 2
        d ≥ 1.0            → 1

    [已知问题 P02] 任一端为 NaN 时 d 为 NaN，同样落到 else 返回 1；
    不只是"两端都缺"才触发。修正方式同 diameter_score。
    """
    try:
        d = (float(depth_start) + float(depth_end)) / 2
    except (ValueError, TypeError):
        return None
    if d <= 0:
        return None
    if d < 0.5:
        return 4
    elif d < 1:
        return 2
    else:
        return 1


def pressure_level_score(level) -> int | None:
    """压力等级风险（权重 0.04）。原 L157-166。输入静态表"压力"列的文本。

    规则（子串匹配，先"高"后"中"）：
        空值      → None
        含"高"    → 4
        含"中"    → 3
        其他文本  → 2（低压或未标注）

    注意："中高压"会先命中"高"；数值型压力（如 0.35）会被 str() 后落到 2。
    """
    if pd.isna(level):
        return None
    text = str(level)
    if "高" in text:
        return 4
    elif "中" in text:
        return 3
    else:
        return 2


# ===========================================================================
# 二、动态指标（来自动态表，每条管段一列监测序列）
# ===========================================================================
# 公共步骤：pd.to_numeric(errors="coerce") 把非数字转 NaN，再 dropna；
# 序列为空返回 (None, {})。std 为 pandas 默认样本标准差（ddof=1），
# 因此只有 1 条记录时 std=NaN、CV=NaN，所有 "cv < 阈值" 比较都为 False，
# 会落到最差的波动档——这是原程序行为，接入稀疏数据时要留意。
# 原程序不按评价时点过滤序列：传进来多少记录就算多少，时间窗由调用方负责。


def _clean(series: pd.Series) -> pd.Series:
    """把监测列转为数值并去掉缺失值（原各动态函数开头两行）。"""
    return pd.to_numeric(series, errors="coerce").dropna()


def dyn_pressure_score(series: pd.Series) -> tuple[int | None, dict]:
    """动态压力风险（权重 0.13，单位 MPa）。原 L169-185。

    1. 基础分按均值区间（越偏离 0.20—0.40 越差）：
           0.20 ≤ mean ≤ 0.40                       → 1
           0.15 ≤ mean < 0.20 或 0.40 < mean ≤ 0.50 → 2
           0.10 ≤ mean < 0.15 或 0.50 < mean ≤ 0.60 → 3
           其他                                      → 4
    2. 波动加分（CV = std/mean，mean 为 0 时 CV 记 0）：
           CV < 0.10 → +0；0.10 ≤ CV < 0.20 → +1；CV ≥ 0.20 → +2
    3. 封顶 5 分。

    返回 (分值, {"动态压力均值": 3 位小数, "动态压力CV": 3 位小数})。
    """
    s = _clean(series)
    if len(s) == 0:
        return None, {}
    mean_v = s.mean()
    std_v = s.std()
    cv = std_v / mean_v if mean_v != 0 else 0
    if 0.20 <= mean_v <= 0.40:
        base = 1
    elif 0.15 <= mean_v < 0.20 or 0.40 < mean_v <= 0.50:
        base = 2
    elif 0.10 <= mean_v < 0.15 or 0.50 < mean_v <= 0.60:
        base = 3
    else:
        base = 4
    extra = 0 if cv < 0.10 else (1 if cv < 0.20 else 2)
    return min(base + extra, 5), {"动态压力均值": round(mean_v, 3), "动态压力CV": round(cv, 3)}


def chlorine_score(series: pd.Series) -> tuple[int | None, dict]:
    """余氯风险（权重 0.13，单位 mg/L）。原 L188-205。

    1. 基础分按均值区间（0.3—0.8 为理想区）：
           0.3 ≤ mean ≤ 0.8                        → 1
           0.1 ≤ mean < 0.3 或 0.8 < mean ≤ 1.2     → 2
           0.05 ≤ mean < 0.1 或 mean > 1.2          → 3
           mean < 0.05                             → 4
    2. 低值加罚：序列中 < 0.05 的比例 > 10% → +1，封顶 5。

    返回 (分值, {"余氯均值", "余氯最小值"})。
    """
    s = _clean(series)
    if len(s) == 0:
        return None, {}
    mean_v = s.mean()
    min_v = s.min()
    below_rate = (s < 0.05).sum() / len(s)
    if 0.3 <= mean_v <= 0.8:
        base = 1
    elif 0.8 < mean_v <= 1.2 or 0.1 <= mean_v < 0.3:
        base = 2
    elif mean_v > 1.2 or 0.05 <= mean_v < 0.1:
        base = 3
    else:
        base = 4
    if below_rate > 0.1:
        base = min(base + 1, 5)
    return base, {"余氯均值": round(mean_v, 3), "余氯最小值": round(min_v, 3)}


def turbidity_score(series: pd.Series) -> tuple[int | None, dict]:
    """浊度风险（权重 0.08，单位 NTU）。原 L208-225。

    1. 基础分按均值：
           mean < 0.5 → 1；< 1.0 → 2；< 3.0 → 3；≥ 3.0 → 4
    2. 超标加罚：序列中 > 1.0 的比例 > 10% → +1，封顶 5。

    返回 (分值, {"浊度均值", "浊度最大值"})。
    """
    s = _clean(series)
    if len(s) == 0:
        return None, {}
    mean_v = s.mean()
    max_v = s.max()
    over_rate = (s > 1.0).sum() / len(s)
    if mean_v < 0.5:
        base = 1
    elif mean_v < 1.0:
        base = 2
    elif mean_v < 3.0:
        base = 3
    else:
        base = 4
    if over_rate > 0.1:
        base = min(base + 1, 5)
    return base, {"浊度均值": round(mean_v, 3), "浊度最大值": round(max_v, 3)}


def flow_score(series: pd.Series) -> tuple[int | None, dict]:
    """瞬时流量风险（权重 0.08）。原 L228-236。

    只看波动，不看流量绝对水平：CV = std/mean（mean 为 0 时记 0）
        CV < 0.2 → 1；< 0.4 → 2；< 0.6 → 3；≥ 0.6 → 4
    无加罚项，最高 4 分。

    返回 (分值, {"瞬时流量均值", "瞬时流量CV"})。
    """
    s = _clean(series)
    if len(s) == 0:
        return None, {}
    mean_v = s.mean()
    std_v = s.std()
    cv = std_v / mean_v if mean_v != 0 else 0
    score = 1 if cv < 0.2 else (2 if cv < 0.4 else (3 if cv < 0.6 else 4))
    return score, {"瞬时流量均值": round(mean_v, 3), "瞬时流量CV": round(cv, 3)}


# ===========================================================================
# 三、风险点指标（来自动态表的"风险点数量"列）
# ===========================================================================


def risk_point_score(count) -> int | None:
    """风险点风险（权重 0.15）。输入该管段挂接的风险点数量。原 L240-257。

    规则：
        None / NaN / 无法转 int → None
        0        → 1
        1—2      → 2
        3—5      → 3
        6—10     → 4
        > 10     → 5

    [已知问题 P03] 原主流程在调用本函数前已把缺失或非法的数量强制置 0
    （见 pipeline.read_risk_point），所以这里的 None 分支在原流程中实际不会触发：
    "未采集"会被当成"确认 0 个风险点"得 1 分，可信度仍 100%。
    """
    if count is None or pd.isna(count):
        return None
    try:
        count = int(count)
    except (ValueError, TypeError):
        return None
    if count == 0:
        return 1
    elif count <= 2:
        return 2
    elif count <= 5:
        return 3
    elif count <= 10:
        return 4
    else:
        return 5
