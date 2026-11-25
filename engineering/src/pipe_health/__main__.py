"""命令行入口。

用法（在项目根目录执行）：
    .venv-baseline/bin/python -m src.pipe_health                      # 默认样例 + 基线时点 + 比对
    .venv-baseline/bin/python -m src.pipe_health --as-of now          # 用当前时间算管龄
    .venv-baseline/bin/python -m src.pipe_health --no-compare         # 不与参考结果比对
    .venv-baseline/bin/python -m src.pipe_health --static a.xlsx --dynamic b.xlsx --no-compare

退出码：0 通过 / 2 比对不一致 / 1 运行异常。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .pipeline import CHINA, DEFAULT_DYNAMIC, DEFAULT_REFERENCE, DEFAULT_STATIC, run

#: 默认评价时点 = 提供方报告的生成时间，保证默认运行能复现基线 82.4535
BASELINE_CLOCK = "2026-09-18T00:01:11+08:00"


def parse_clock(text: str) -> datetime | None:
    """'now' → None（由 run 用当前时间）；否则按 ISO 8601 解析，缺时区按 Asia/Shanghai。"""
    if text == "now":
        return None
    clock = datetime.fromisoformat(text)
    return clock if clock.tzinfo else clock.replace(tzinfo=CHINA)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.pipe_health",
        description="供水管网健康评价简化模型（提供方 V3.0 的逐函数复现）",
    )
    parser.add_argument("--static", type=Path, default=DEFAULT_STATIC, help="静态表 xlsx")
    parser.add_argument("--dynamic", type=Path, default=DEFAULT_DYNAMIC, help="动态表 xlsx")
    parser.add_argument("--as-of", default=BASELINE_CLOCK,
                        help=f"评价时点（ISO 8601），决定管龄；'now' 用当前时间。默认 {BASELINE_CLOCK}")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE,
                        help="参考结果 xlsx，用于逐格比对")
    parser.add_argument("--no-compare", action="store_true", help="跳过与参考结果的比对")
    args = parser.parse_args(argv)

    metadata = run(
        static_path=args.static,
        dynamic_path=args.dynamic,
        as_of=parse_clock(args.as_of),
        reference=None if args.no_compare else args.reference,
    )
    brief = {k: metadata.get(k) for k in (
        "run_id", "status", "evaluation_clock", "summary", "comparison", "output_directory", "metadata", "error",
    ) if metadata.get(k) is not None}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return {"passed": 0, "comparison_failed": 2}.get(metadata["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
