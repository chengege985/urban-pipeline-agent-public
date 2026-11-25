# 算法验证候选包

数据：synthetic / fixture；模型：candidate。2026-11-01。不注册到产品工具表，不改变0.1.1评分、T01图时间线、D01—D04效用或比赛RC。

- `service_time_value.py`：恒定需水、显式时间片、独立可加终端供给的缺口积分和同基准备用边际增益。
- `run_time_value.py`：8组时间价值情景及独立水力结果的分段稳态接入样例；输出输入快照、源码哈希、结果。
- `hydraulic_baseline.py`：复用已有cuv_reference合成网络，实际调用EPANET2.2；四工况、标准INP与质量平衡。

Windows根目录运行：

```powershell
.\.venv-agent\Scripts\python.exe -X utf8 -B -m unittest discover -s tests/algorithms -p test_service_time_value.py -v
.\.venv-cuv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests/algorithms -p test_hydraulic_baseline.py -v
.\.venv-cuv\Scripts\python.exe -X utf8 -B -m src.algorithm_validation.hydraulic_baseline
.\.venv-agent\Scripts\python.exe -X utf8 -B -m src.algorithm_validation.run_time_value
```

水力先生成结果，时间价值入口再读取；只运行后者不会重新求解水力。实验记录在 `experiments/algorithm_validation/time_value_v0_1/`。水力环境依赖WNTR1.4.0；标准库时间价值代码不需要水力/深度学习依赖。179工程`.venv-agent`当前没有WNTR，本轮未向其中安装依赖。

没有WNTR的环境会显式跳过7项水力回归，避免阻断现有产品测试；skip不等于物理验证通过。本轮7项水力通过指Windows已有`.venv-cuv`的实际运行。

合同见 `04_实施方案/02_接口与数据/服务时间价值算法与验证契约_v0.1.md`，结论见 `03_梳理结论/02_专项分析/服务时间价值与水力基准验证_20261101.md`。输入如要用于真实业务，须先补实际分配、服务阈值、供水路径、水质与资源时序等验证；此候选入口对real数据返回unsupported。
