# Charlottesville 和 Schutterwald 本地演示

在项目版中控 `#/gas` 右上角选择数据集。沿用现有窗口布局。

- **Charlottesville** 使用市政府 ArcGIS 在线服务。选择主管、服务线、阀门或分区，在地图或目录选择对象。在事件栏分开筛选泄漏记录和泄漏报告，点击一条查看处置时间线。选中服务线后，详情显示主管编号的匹配情况，以及仅按唯一服务线编号关联的表计记录数。事件与资产的直接关系和时间字段语义待核实，当前界面不计算处置耗时。全部使用字段白名单。演示限本机，不把原始记录和详细地图打包公开发布。
- **Schutterwald** 是研究模型。选择基准、两种预设故障，或从地图选一个配气管段，确认后执行。处置时间线可以切换基准、故障和恢复示意。选中模型建筑后可查看接入节点和路径；在故障影响窗口对比两次执行，在证据窗口查看版本、哈希或下载结果。模型没有阀门或真实户数。

## 配置 Schutterwald

从项目根目录取得原始研究文件。文件放在忽略目录，运行时核对 SHA-256。

```powershell
New-Item -ItemType Directory -Force engineering/outputs/research
Invoke-WebRequest 'https://raw.githubusercontent.com/e2nIEE/pandapipes/v0.12.0/src/pandapipes/networks/network_files/gas_net_schutterwald_1bar.json' -OutFile engineering/outputs/research/model.json
$env:SCHUTTERWALD_MODEL_FILE = (Resolve-Path engineering/outputs/research/model.json).Path
python -m venv "$env:TEMP/schutterwald-solver"
& "$env:TEMP/schutterwald-solver/Scripts/python.exe" -m pip install -r engineering/configs/schutterwald_solver_requirements.txt
$env:SCHUTTERWALD_SOLVER_PYTHON = "$env:TEMP/schutterwald-solver/Scripts/python.exe"
```

后端和前端使用 [公开数据运行说明](PUBLIC_GAS_DATASETS_RUNTIME.md) 中的项目版、回环地址与只读配置。保留上述两个环境变量再启动后端。Linux 使用独立环境的 `bin/python` 并按仓库约定运行项目。

稳态计算使用原模型给定入口压力和质量流量负荷。先核对基准求解，再计算故障后的连通部分。仅在收敛、质量平衡和有限输出检查通过时显示压力 bar 与质量流量 kg/s。断连负荷单列。稳态失败仍可看本次拓扑结果。

## 验证

- 后端：在 engineering 运行 `python -m pytest -q tests/test_cville_schutterwald.py tests/test_public_gas_datasets.py`。
- 前端：在 web 运行 `npm run build:project` 和 `npm test`。
- 双服务启动后，在 web 运行 `node scripts/cville-schutterwald.browser.mjs`，Chrome 验收证据保存在 `engineering/outputs/research/browser/`。



## 2026-11-24 验收记录

- Windows Chrome 实际连接本机后端。完成 Charlottesville 资产加载、事件选择和处置阶段核对，以及 Schutterwald 模型加载、预设故障、稳态计算、不可达地图标记和切回后清除旧结果。
- 实际查询市政接口，主管 9,359 条、服务线 42,559 条、阀门 2,169 条、分区 15 条。泄漏记录 5,040 条，泄漏报告 1,496 条，按来源分开。
- 后端定向回归 14 项通过。包括完整 1,053 条配气管线单段失效回归、稳定哈希、基准压力边界、故障断连负荷和结果隔离。前端 405 项通过，原浏览器测试组有 13 项按配置跳过；本轮 Chrome 脚本单独通过。
- Windows 全量后端收集未通过，仍需在项目的 129 Linux 环境完成。开发基线保留此前可视化工作树的未提交成果，新的远端 main 尚未合并进这一分支。

## 历史事件分析和故障压力对比

- 选择 **Charlottesville → 风险分析**。默认最近 12 个完整 UTC 自然月。日期、原因、行政区筛选同时影响统计与追溯表。行政区是原始记录字段，阀门分区仍用于地图。选择「泄漏报告」可单独查看报告统计。没有有效日期的记录单列。相同工单号仅用于寻找候选，不作为事件发生管段的证据。来源不可用或分页对不上时不填零。
- 打开一条泄漏记录的「处置时间线」，可见派遣至到场、上报至修复两个区间。逆序及不可解析时间排除，相同时刻单列，不解释为即时到场或即时修复。
- 选择 **Schutterwald → 风险分析**。清单支持按管段编号、有影响、零影响筛选。点击「定位并选为情景」后，在应急情景窗口明确执行。勾选稳态后，风险分析窗口会展示全网压力变化统计和节点基准／故障后压力。断连节点没有故障后压力。
- 完整的 Chrome 复验入口为 `node scripts/cville-schutterwald.browser.mjs`。新增截图名为 `03-charlottesville-analysis.png` 和 `04-schutterwald-pressure.png`。
