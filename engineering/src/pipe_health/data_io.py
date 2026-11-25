"""输入输出：读取静态/动态表、识别列名、写 Excel 与 TXT 报告。

对应原程序 main() 的读表部分（L284-328）、导出部分（L438-525）和 format_sheet（L538-550）。

与原程序的差异
--------------
- 路径由调用方传入，不再写死桌面；文件缺失抛异常而不是 sys.exit。
- TXT 报告在原格式基础上增加"评价时点"一行；权重段由 config.WEIGHTS 生成，
  不再手工抄写一遍。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .config import (
    DYNAMIC_INDICATORS,
    PIPE_ID_KEYWORDS,
    RISK_COUNT_DERIVED_COLUMN,
    RISK_COUNT_KEYWORDS,
    RISK_IDS_KEYWORDS,
    RISK_POINT_INDICATOR,
    SHEET_GRADES,
    SHEET_MAIN,
    SHEET_STATS,
    STATIC_INDICATORS,
    WEIGHTS,
)

# ---------------------------------------------------------------------------
# 读取与列名识别
# ---------------------------------------------------------------------------


def find_field(df: pd.DataFrame, keywords: list[str]) -> str | None:
    """在 df 的列名中查找关键字，返回命中的列名；都不命中返回 None。原 L65-77。

    两轮匹配：先对每个关键字做**精确**匹配；全部未命中再做**子串**匹配
    （长度 < 2 的关键字跳过，避免单字误配）。关键字列表的顺序即优先级。
    """
    cols = [str(c) for c in df.columns]
    for k in keywords:
        for col in cols:
            if col == k:
                return col
    for k in keywords:
        if len(k) < 2:
            continue
        for col in cols:
            if k in col:
                return col
    return None


@dataclass
class ResolvedColumns:
    """两张输入表中实际使用的列名。"""

    static_id: str              #: 静态表管线编号列
    dynamic_id: str             #: 动态表管线编号列
    risk_count: str             #: 风险点数量列（可能是派生列 RISK_COUNT_DERIVED_COLUMN）
    risk_ids: str | None        #: 风险点 ID 列表列，可能没有
    risk_count_derived: bool    #: True 表示数量由 ID 列表拆分计数得到


def load_tables(static_path: Path, dynamic_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """读取静态表与动态表（原 L279-285）。缺文件抛 FileNotFoundError。

    用 pandas 默认参数读取，与原程序一致；注意默认会把 "nan"、"NA"、"" 等文本当缺失值。
    """
    for path in (static_path, dynamic_path):
        if not Path(path).is_file():
            raise FileNotFoundError(f"未找到输入文件：{path}")
    return pd.read_excel(static_path), pd.read_excel(dynamic_path)


def resolve_columns(df_static: pd.DataFrame, df_dynamic: pd.DataFrame) -> ResolvedColumns:
    """识别管线编号列和风险点列；必要时在 df_dynamic 上新增派生数量列。原 L291-318。

    - 管线编号：静态、动态各自按 PIPE_ID_KEYWORDS 模糊匹配；找不到抛 ValueError。
    - 风险点数量：按 RISK_COUNT_KEYWORDS 匹配。未命中但有 ID 列表列时，
      按英文逗号拆分 ID 列表计数写入派生列（空值计 0）；两者都没有抛 ValueError。

    注意：拆分只认英文逗号 ","，中文逗号或分号会被当成一个 ID；
    派生列会直接加到传入的 df_dynamic 上（调用方如需保护原表请先 copy）。
    """
    static_id = find_field(df_static, PIPE_ID_KEYWORDS)
    dynamic_id = find_field(df_dynamic, PIPE_ID_KEYWORDS)
    if static_id is None or dynamic_id is None:
        raise ValueError(
            f"未找到管线编号列：静态表={static_id!r}，动态表={dynamic_id!r}；"
            f"静态列={list(df_static.columns)}，动态列={list(df_dynamic.columns)}"
        )

    risk_count = find_field(df_dynamic, RISK_COUNT_KEYWORDS)
    risk_ids = find_field(df_dynamic, RISK_IDS_KEYWORDS)
    derived = False
    if risk_count is None:
        if risk_ids is None:
            raise ValueError(
                f"动态表既没有风险点数量列，也没有风险点ID列表列；现有列：{list(df_dynamic.columns)}"
            )
        df_dynamic[RISK_COUNT_DERIVED_COLUMN] = df_dynamic[risk_ids].apply(
            lambda x: len(str(x).split(",")) if pd.notna(x) and str(x).strip() else 0
        )
        risk_count = RISK_COUNT_DERIVED_COLUMN
        derived = True
    return ResolvedColumns(static_id, dynamic_id, risk_count, risk_ids, derived)


# ---------------------------------------------------------------------------
# 写出
# ---------------------------------------------------------------------------


def format_sheet(ws) -> None:
    """表头样式与列宽（原 L538-550）。

    表头：加粗、白字、深蓝底（1F4E78）、居中；冻结首行；整表自动筛选；
    每列宽度 = 该列最长内容字符数 + 5，上限 40。
    """
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    try:
        ws.auto_filter.ref = ws.dimensions
    except Exception:
        pass
    for col in ws.columns:
        width = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(width + 5, 40)


def write_excel(df_result: pd.DataFrame, path: Path) -> None:
    """写出三张表的工作簿（原 L440-467）。

    健康评价总表：df_result 全部列，行序沿用 df_result（调用方已按 Hs 升序，最差在前）
    统计结果：评价管线数 / 平均健康度 / 平均可信度 / 有风险点的管线数 / 风险点总数
    等级分布：健康等级 value_counts（按数量降序）

    注意："风险点总数"是各管段"风险点数量"字段之和，即挂接次数；
    同一风险点挂接到多条管段会重复计数，不是唯一风险点个数。
    """
    wb = Workbook()

    ws = wb.active
    ws.title = SHEET_MAIN
    ws.append(list(df_result.columns))
    for row in df_result.values.tolist():
        ws.append(row)
    format_sheet(ws)

    ws2 = wb.create_sheet(SHEET_STATS)
    ws2.append(["指标", "数值"])
    ws2.append(["评价管线数", len(df_result)])
    ws2.append(["平均健康度", round(df_result["健康度Hs"].mean(), 2)])
    ws2.append(["平均可信度", round(df_result["数据可信度(%)"].mean(), 2)])
    ws2.append(["有风险点的管线数", int((df_result["风险点数量"] > 0).sum())])
    ws2.append(["风险点总数", int(df_result["风险点数量"].sum())])
    format_sheet(ws2)

    ws3 = wb.create_sheet(SHEET_GRADES)
    ws3.append(["等级", "数量"])
    for grade, count in df_result["健康等级"].value_counts().items():
        ws3.append([grade, int(count)])
    format_sheet(ws3)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _weight_block(names: list[str]) -> str:
    """把一组指标名渲染成报告里的"  名称   权重"行。"""
    return "\n".join(f"  {name:<8}{WEIGHTS[name]:.2f}" for name in names)


def write_text_report(df_result: pd.DataFrame, path: Path, as_of: datetime, generated_at: datetime) -> None:
    """写出文本运行报告（原 L471-524）。

    内容：管线数、平均健康度、平均可信度、有风险点的管线数、风险点总数、
    等级分布、权重配置、风险点分档、公式；末尾给出评价时点（新增）与生成时间。
    """
    text = f"""
供水管网健康评价模型 V3.0 运行报告（含风险点指标）

评价管线数：
{len(df_result)}

平均健康度：
{df_result['健康度Hs'].mean():.2f}

平均可信度：
{df_result['数据可信度(%)'].mean():.2f}%

有风险点的管线数：
{int((df_result['风险点数量'] > 0).sum())}

风险点总数：
{int(df_result['风险点数量'].sum())}

健康等级分布：

{df_result['健康等级'].value_counts().to_string()}


权重配置（合计 {sum(WEIGHTS.values()):.2f}）：

静态指标：
{_weight_block(STATIC_INDICATORS)}

动态指标：
{_weight_block(DYNAMIC_INDICATORS)}

风险点指标：
{_weight_block([RISK_POINT_INDICATOR])}

风险点分档评分：
  0个 = 1分
  1~2个 = 2分
  3~5个 = 3分
  6~10个 = 4分
  >10个 = 5分

Rs = Σ(wi * xi) / Σ(wi)
Hs = 100 * [1 - (Rs - 1) / 4]

评价时点：{as_of.strftime('%Y-%m-%d %H:%M:%S')}
生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S')}
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
