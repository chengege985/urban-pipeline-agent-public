"""单管段汇总：把 10 项指标分合成 Rs、Hs、等级、可信度、问题清单与主要风险因子。

对应原程序 main() 中 L387-412 的内联代码。公式：

    Rs = Σ(w_k × score_k) / Σ(w_k)      仅对 score_k 不为 None 的指标求和
    Hs = 100 × [1 − (Rs − 1) / 4]        再裁剪到 [0, 100]
    可信度(%) = Σ(有效 w_k) / Σ(全部 w_k) × 100

含义与边界
----------
- Rs 取值 1—5：全部指标 1 分 → Rs=1 → Hs=100；全部 5 分 → Rs=5 → Hs=0。
- 某指标为 None 时，它的权重从分母中剔除，相当于"其余指标重新归一化"。
  因此缺失**不会**拉低 Hs，只会降低可信度并出现在"数据问题"里。
- 可信度是"有效权重占比"，不是预测准确率，也不能证明原始输入完整
  （P01/P02/P03 的缺失值还会被打成 1 分，连可信度都不降）。
- 这是提供方的简化评分，不是正式方案的 P/C/R/H 体系：没有后果 C、
  证据充分度 E、质量系数 q，也不是失效概率。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import GRADE_LOWEST, GRADE_NO_DATA, GRADE_THRESHOLDS, TOTAL_WEIGHT, WEIGHTS


def health_grade(hs: float | None) -> str:
    """Hs → 健康等级。原 health_level，L80-92。

        Hs ≥ 90 → 健康；≥ 75 → 基本健康；≥ 60 → 关注；≥ 40 → 较差；< 40 → 严重
        Hs 为 None（10 项全部无法评价）→ 数据不足

    注意：原程序用**未四舍五入**的 Hs 判级，再把 round(Hs, 2) 写入结果表；
    所以可能出现表中显示 74.995→"75.0" 却判为"关注"的情况。
    本函数保持该约定，调用方应传未舍入值。
    """
    if hs is None:
        return GRADE_NO_DATA
    for lower, name in GRADE_THRESHOLDS:
        if hs >= lower:
            return name
    return GRADE_LOWEST


@dataclass
class PipeAggregate:
    """一条管段的汇总结果（未舍入；舍入在写结果行时进行，见 pipeline.score_pipe）。"""

    rs: float | None            #: 综合风险指数 Rs，1—5
    hs: float | None            #: 健康度 Hs，0—100
    grade: str                  #: 健康等级
    completeness: float         #: 数据可信度(%) = 有效权重 / 总权重 × 100
    problems: list[str] = field(default_factory=list)  #: 形如 "管径风险无法评价"
    main_risk: str = "无"       #: 主要风险因子，如 "材质+管龄"


def aggregate_scores(scores: dict[str, int | None]) -> PipeAggregate:
    """把 10 项指标分汇总为 PipeAggregate。原 L394-412。

    参数
    ----
    scores : {指标名: 1—5 或 None}
        键必须与 config.WEIGHTS 一致且**顺序相同**（材质→管龄→…→风险点）：
        浮点累加顺序决定与基线能否逐格一致；并列时主要风险因子按此顺序取舍。

    步骤
    ----
    1. 逐项累加 weighted 与 valid_w；None 的项记入 problems。
    2. valid_w > 0 时算 Rs、Hs，否则两者为 None。
    3. 可信度 = valid_w / TOTAL_WEIGHT × 100。
    4. 主要风险因子：按**原始分**降序取前 2 项，去掉名字里的"风险"二字，用 "+" 连接。

    [已知问题 P06] 第 4 步按单项分值而非 w×分 的贡献排序：例如压力等级 4 分（权重 0.04）
    会排在风险点 3 分（权重 0.15）前面。若要表达"加权贡献最大"，应改用 WEIGHTS[k]*v 排序。
    另注："风险点风险".replace("风险", "") 会把两个"风险"都删掉，只剩"点"——这是原程序行为。
    """
    weighted = 0.0
    valid_w = 0.0
    problems: list[str] = []
    for name, value in scores.items():
        if value is not None:
            weighted += WEIGHTS[name] * value
            valid_w += WEIGHTS[name]
        else:
            problems.append(name + "无法评价")

    if valid_w > 0:
        rs = weighted / valid_w
        hs = max(0.0, min(100.0, 100 * (1 - (rs - 1) / 4)))
    else:
        rs = hs = None

    valid = {k: v for k, v in scores.items() if v is not None}
    top = sorted(valid.items(), key=lambda kv: -kv[1])[:2]
    main_risk = "+".join(k.replace("风险", "") for k, _ in top) if top else "无"

    return PipeAggregate(
        rs=rs,
        hs=hs,
        grade=health_grade(hs),
        completeness=valid_w / TOTAL_WEIGHT * 100,
        problems=problems,
        main_risk=main_risk,
    )
