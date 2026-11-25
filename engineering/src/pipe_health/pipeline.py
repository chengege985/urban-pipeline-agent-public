"""评价流程编排。

evaluate()  纯计算：两张 DataFrame + 评价时点 → 结果 DataFrame（不读写文件）
run()       完整运行：读文件 → evaluate → 写 Excel/TXT → 记录元信息 →（可选）与参考结果比对

原程序 main()（L263-535）把以上全部写在一个函数里，这里拆开以便复用与回归。

落盘约定（见 00_索引/分块与命名约定 §六）
    experiments/pipe_health/<run_id>/metadata.json     运行元信息：时点、输入哈希、环境、汇总
    experiments/pipe_health/<run_id>/comparison.json   与参考结果的逐格比对（启用比对时）
    experiments/pipe_health/latest.json                指向最近一次运行，读取时要同时看 status
    outputs/pipe_health/<run_id>/V3.0健康评价结果.xlsx  三张表
    outputs/pipe_health/<run_id>/V3.0分析报告.txt       文本报告
每次运行新建 run_id 目录，不覆盖旧结果。
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .aggregate import aggregate_scores
from .compare import compare_with_reference
from .config import (
    DYNAMIC_COLUMNS,
    OUTPUT_TXT_NAME,
    OUTPUT_XLSX_NAME,
    PACKAGE_VERSION,
    STATIC_COLUMNS,
)
from .data_io import ResolvedColumns, load_tables, resolve_columns, write_excel, write_text_report
from .indicators import (
    age_score,
    chlorine_score,
    depth_score,
    diameter_score,
    dyn_pressure_score,
    flow_score,
    material_score,
    pressure_level_score,
    risk_point_score,
    turbidity_score,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "02_核查证据/程序包/运行程序结果260918"
DEFAULT_STATIC = PACKAGE_DIR / "输入/input_pipe_data.xlsx"
DEFAULT_DYNAMIC = PACKAGE_DIR / "输入/input_pipe_data_动态.xlsx"
DEFAULT_REFERENCE = PACKAGE_DIR / "输出/V1.0健康评价结果.xlsx"
CHINA = ZoneInfo("Asia/Shanghai")
MODEL_LABEL = "pipe_health"

#: 动态列 → (指标名, 打分函数)，顺序即输出顺序（原 L352-367）
_DYNAMIC_SCORERS = [
    (DYNAMIC_COLUMNS["pressure"], "动态压力风险", dyn_pressure_score),
    (DYNAMIC_COLUMNS["chlorine"], "余氯风险", chlorine_score),
    (DYNAMIC_COLUMNS["turbidity"], "浊度风险", turbidity_score),
    (DYNAMIC_COLUMNS["flow"], "瞬时流量风险", flow_score),
]


# ---------------------------------------------------------------------------
# 单管段
# ---------------------------------------------------------------------------


def read_risk_point(dgroup: pd.DataFrame | None, cols: ResolvedColumns) -> tuple[int, str]:
    """取该管段的风险点数量与 ID 列表文本。原 L369-383。

    - 只取动态分组的**第一条**记录（原注释"取该管线的第一条即可"）。
      同一管段多条记录数量不一致时结果取决于行序；样例每段 24 条记录数量一致，未暴露。
    - 数量无法转 int（含 NaN）→ 0；没有动态记录 → 0。[已知问题 P03]
      "未采集"与"确认 0 个"被混为一谈，且不记入"数据问题"。
    - ID 列表用 str() 转文本：空单元格会变成字面 "nan"（样例中有 209 条），
      下游做去重计数时需要排除这个占位值。
    """
    if dgroup is not None and len(dgroup) > 0:
        count = dgroup[cols.risk_count].iloc[0]
        try:
            count = int(count)
        except (ValueError, TypeError):
            count = 0
    else:
        count = 0
    if cols.risk_ids is not None and dgroup is not None and len(dgroup) > 0:
        ids = str(dgroup[cols.risk_ids].iloc[0])
    else:
        ids = ""
    return count, ids


def score_pipe(pid, srow: pd.Series, dgroup: pd.DataFrame | None, cols: ResolvedColumns, as_of_year: int) -> dict:
    """计算一条管段，返回结果表的一行（列顺序与原程序一致）。原 L334-431。

    参数
    ----
    pid      管线编号
    srow     静态表中该管段的一行
    dgroup   动态表中该管段的全部记录；没有则为 None（4 项动态指标全部 None）
    cols     resolve_columns() 的结果
    as_of_year  评价时点年份，供管龄计算

    输出列顺序：
        管线编号, 材质, 管径, 投运年份, 风险点数量, 风险点ID列表,
        综合风险指数Rs, 健康度Hs, 健康等级, 数据可信度(%),
        5 项静态分, 4 项动态分, 风险点风险,
        动态统计量（均值/CV/最小/最大，有几项算几项）,
        数据问题, 主要风险因子
    Rs 保留 3 位、Hs 保留 2 位、可信度保留 2 位；等级按未舍入 Hs 判定。
    """
    # 静态 5 项：srow.get 在列缺失时返回 None → 该项"无法评价"
    s_scores = {
        "材质风险": material_score(srow.get(STATIC_COLUMNS["material"])),
        "管龄风险": age_score(srow.get(STATIC_COLUMNS["commission_year"]), as_of_year),
        "管径风险": diameter_score(srow.get(STATIC_COLUMNS["diameter"])),
        "埋深风险": depth_score(srow.get(STATIC_COLUMNS["depth_start"]), srow.get(STATIC_COLUMNS["depth_end"])),
        "压力等级风险": pressure_level_score(srow.get(STATIC_COLUMNS["pressure_level"])),
    }

    # 动态 4 项：先全部置 None，有记录且有该列才计算
    d_scores = {name: None for _, name, _ in _DYNAMIC_SCORERS}
    d_stats: dict = {}
    if dgroup is not None and len(dgroup) > 0:
        for column, name, scorer in _DYNAMIC_SCORERS:
            if column in dgroup.columns:
                d_scores[name], stats = scorer(dgroup[column])
                d_stats.update(stats)

    # 风险点
    rp_count, rp_ids = read_risk_point(dgroup, cols)
    rp_score = risk_point_score(rp_count)

    # 汇总（字典顺序 = 权重表顺序，不能变）
    all_scores = {**s_scores, **d_scores, "风险点风险": rp_score}
    agg = aggregate_scores(all_scores)

    return {
        "管线编号": pid,
        "材质": srow.get(STATIC_COLUMNS["material"]),
        "管径": srow.get(STATIC_COLUMNS["diameter"]),
        "投运年份": srow.get(STATIC_COLUMNS["commission_year"]),
        "风险点数量": rp_count,
        "风险点ID列表": rp_ids,
        "综合风险指数Rs": round(agg.rs, 3) if agg.rs is not None else None,
        "健康度Hs": round(agg.hs, 2) if agg.hs is not None else None,
        "健康等级": agg.grade,
        "数据可信度(%)": round(agg.completeness, 2),
        **s_scores,
        **d_scores,
        "风险点风险": rp_score,
        **d_stats,
        "数据问题": ";".join(agg.problems) if agg.problems else "",
        "主要风险因子": agg.main_risk,
    }


# ---------------------------------------------------------------------------
# 全表
# ---------------------------------------------------------------------------


def _normalize_clock(clock: datetime) -> datetime:
    """统一为上海时区；无有效时区的时间按上海当地时间理解，不改变所表示的时刻。"""
    if clock.tzinfo is None or clock.utcoffset() is None:
        clock = clock.replace(tzinfo=CHINA)
    return clock.astimezone(CHINA)


def evaluate(df_static: pd.DataFrame, df_dynamic: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """纯计算入口：两张表 + 评价时点 → 结果表。原 L291-434。

    1. 识别列名（可能给动态表加派生数量列，因此先 copy）。
    2. 动态表按管线编号分组；静态表按编号建字典——编号重复时后一行覆盖前一行（原行为）。
    3. 逐管段 score_pipe；静态表有、动态表没有的管段，动态 4 项为 None、风险点按 0 处理。
    4. 按 健康度Hs 升序排列（最差在前）。原用非稳定排序，同分行序不保证一致，
       比对结果请按管线编号对齐。

    评价时点与 run() 一样先统一为上海时区，再取年份，避免同一时刻跨时区产生不同管龄。
    不做评价时点的时间窗过滤：传入多少动态记录就统计多少。
    """
    as_of = _normalize_clock(as_of)
    df_dynamic = df_dynamic.copy()
    cols = resolve_columns(df_static, df_dynamic)

    dyn_groups = {pid: group for pid, group in df_dynamic.groupby(cols.dynamic_id)}
    static_rows: dict = {}
    for _, row in df_static.iterrows():
        static_rows[row[cols.static_id]] = row

    rows = [
        score_pipe(pid, srow, dyn_groups.get(pid), cols, as_of.year)
        for pid, srow in static_rows.items()
    ]
    return pd.DataFrame(rows).sort_values("健康度Hs", ascending=True)


# ---------------------------------------------------------------------------
# 完整运行（含落盘与元信息）
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    """分块计算文件哈希；缺文件、无读取权限等异常由 run() 记录。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    """以 UTF-8 保存运行记录；目录或文件不可写时明确抛出文件系统异常。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _relative(path: Path, root: Path) -> str:
    """项目内路径转为相对路径；项目外路径保留原表示。"""
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def summarize(result: pd.DataFrame) -> dict:
    """结果表的几个总览数字（与 TXT 报告、统计表口径一致）。"""
    return {
        "pipe_count": int(len(result)),
        "mean_health": float(result["健康度Hs"].mean()),
        "health_levels": {str(k): int(v) for k, v in result["健康等级"].value_counts().items()},
        "mean_completeness": float(result["数据可信度(%)"].mean()),
        "pipes_with_risk_points": int((result["风险点数量"] > 0).sum()),
        "risk_point_attachment_sum": int(result["风险点数量"].sum()),
    }


def run(
    static_path: Path = DEFAULT_STATIC,
    dynamic_path: Path = DEFAULT_DYNAMIC,
    as_of: datetime | None = None,
    reference: Path | None = DEFAULT_REFERENCE,
    root: Path = ROOT,
) -> dict:
    """读文件 → 评价 → 写出 → 记录元信息 →（可选）比对。返回 metadata 字典。

    参数
    ----
    static_path / dynamic_path  两张输入表；默认用 M07 程序包自带样例
    as_of      评价时点（决定管龄）。None 表示用当前时间；naive 时间按 Asia/Shanghai 理解
    reference  参考结果工作簿；传 None 跳过比对
    root       项目根目录，决定 experiments/ 与 outputs/ 的位置

    status 取值：passed（比对通过或未比对）、comparison_failed、failed（异常，详见 error.log）。
    未比对时 comparison 为 None；passed 本身不代表参考结果验证通过。
    输入哈希、依赖查询和计算失败均写入失败记录；若运行记录目录或日志不可写，
    文件系统异常仍向上传递，不能保证在不可写目录留下记录。
    """
    started = datetime.now(CHINA)

    run_id = f"{MODEL_LABEL}_{started:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    experiment_dir = root / "experiments" / MODEL_LABEL / run_id
    output_dir = root / "outputs" / MODEL_LABEL / run_id
    experiment_dir.mkdir(parents=True, exist_ok=False)

    # 先落盘最小记录，再进行可能失败的依赖查询、输入哈希和读取。
    # 哈希为 None 表示尚未取得，不能把它当作空文件的有效指纹。
    metadata: dict = {
        "run_id": run_id,
        "status": "running",
        "model_label": MODEL_LABEL,
        "package_version": PACKAGE_VERSION,
        "reimplementation_of": _relative(PACKAGE_DIR / "V1.0管道健康评价模型.py", root),
        "started_at": started.isoformat(),
        "evaluation_clock": None,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "package_versions": {},
        "inputs": {
            "static": {"path": _relative(static_path, root), "sha256": None},
            "dynamic": {"path": _relative(dynamic_path, root), "sha256": None},
        },
        "comparison": None,
        "reference": _relative(reference, root) if reference else None,
        "output_directory": _relative(output_dir, root),
        "metadata": _relative(experiment_dir / "metadata.json", root),
    }
    _write_json(experiment_dir / "metadata.json", metadata)

    timer = time.perf_counter()
    try:
        clock = _normalize_clock(as_of if as_of is not None else started)
        metadata["evaluation_clock"] = clock.isoformat()
        for name in ("numpy", "pandas", "openpyxl"):
            metadata["package_versions"][name] = importlib.metadata.version(name)
        for name, path in (("static", static_path), ("dynamic", dynamic_path)):
            metadata["inputs"][name]["sha256"] = _sha256(path)
        _write_json(experiment_dir / "metadata.json", metadata)

        df_static, df_dynamic = load_tables(static_path, dynamic_path)
        metadata["input_summary"] = {
            "static_rows": int(len(df_static)),
            "dynamic_rows": int(len(df_dynamic)),
        }
        result = evaluate(df_static, df_dynamic, clock)

        output_dir.mkdir(parents=True, exist_ok=False)
        xlsx = output_dir / OUTPUT_XLSX_NAME
        txt = output_dir / OUTPUT_TXT_NAME
        write_excel(result, xlsx)
        write_text_report(result, txt, clock, datetime.now(CHINA))
        metadata["outputs"] = [
            {"path": _relative(p, root), "bytes": p.stat().st_size, "sha256": _sha256(p)} for p in (xlsx, txt)
        ]
        metadata["summary"] = summarize(result)

        if reference:
            comparison = compare_with_reference(result, reference)
            _write_json(experiment_dir / "comparison.json", comparison)
            metadata["comparison"] = {
                "passed": comparison["passed"],
                "unique_keys": comparison["unique_keys"],
                "cell_mismatches": comparison["cell_mismatches"],
                "max_numeric_difference": comparison["max_numeric_difference"],
                "detail": _relative(experiment_dir / "comparison.json", root),
            }
            metadata["status"] = "passed" if comparison["passed"] else "comparison_failed"
        else:
            metadata["comparison"] = None
            metadata["status"] = "passed"
    except Exception as exc:  # 记录后继续写元信息，不让调用方拿不到 run_id
        metadata["status"] = "failed"
        metadata["error"] = repr(exc)
        (experiment_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")

    metadata["elapsed_seconds"] = round(time.perf_counter() - timer, 3)
    metadata["finished_at"] = datetime.now(CHINA).isoformat()
    _write_json(experiment_dir / "metadata.json", metadata)
    _write_json(root / "experiments" / MODEL_LABEL / "latest.json", {
        "run_id": run_id,
        "status": metadata["status"],
        "metadata": metadata["metadata"],
        "output_directory": metadata["output_directory"],
    })
    return metadata
