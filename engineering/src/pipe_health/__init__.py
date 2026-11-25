"""供水管网健康评价模型 —— 提供方简化版（V3.0）的逐函数复现。

本包把 `02_核查证据/程序包/运行程序结果260918/V1.0管道健康评价模型.py`
（原始材料 M07，文件名 V1.0、代码内标题 V3.0）拆成可读、可比对、可扩展的模块，
作为后续模型开发的地基。

设计原则
--------
1. 计算结果与原程序逐格一致：权重、阈值、分档、汇总公式、输出列顺序均不改动；
   已知缺陷（台账 P01/P02/P03/P06）保留原行为并在代码处标注，待评分责任方确认后再改。
2. 只替换"环境性"部分：桌面硬编码路径 → 显式参数；datetime.now() → 显式评价时点；
   覆盖式输出 → 按运行 ID 落盘并记录元信息与输入哈希。
3. 每个函数的注释写清：做什么、输入、输出、分档规则、对应原程序行号、注意事项。

模块一览
--------
config      权重、分级阈值、列名、材质映射、输出文件名
indicators  10 项指标的打分函数（静态 5 / 动态 4 / 风险点 1）
aggregate   单管段汇总：Rs、Hs、等级、可信度、问题清单、主要风险因子
data_io     读取输入表、识别列名、写 Excel（3 张表）与 TXT 报告
pipeline    评价流程编排：evaluate()（纯计算）与 run()（含落盘与元信息）
compare     与参考结果按管线编号逐格比对（回归守门）
__main__    命令行入口

快速使用
--------
    cd /workdata/WaterPipNet/01_工程项目
    ../.venv-baseline/bin/python -m src.pipe_health

作为库调用：
    from src.pipe_health import evaluate
    result = evaluate(df_static, df_dynamic, as_of=datetime(2026, 9, 18))
"""
from .config import PACKAGE_VERSION as __version__
from .pipeline import evaluate, run

__all__ = ["evaluate", "run", "__version__"]
