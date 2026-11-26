
# Pipeline Engineer 配气样例与西西里稳态复核

在既有燃气中控的右上角选择「NGN 配气样例」。这个入口读取 [Pipeline Engineer 教程](https://pipelineengineer.github.io/gas_distribution_network/) 的 [固定版本样例](https://github.com/pipelineengineer/examples/tree/27e9de906c35c650a347f1046967a6278409845b/gas-network-example)。原「NGN · The North」入口继续读取公开资产地图。

1. **综合态势**显示 103 条模型管段、98 个节点、2 个气源边界、24 个模型负荷点。地图读取 GeoPackage 的 EPSG:27700 几何，记录视图和地图均可选管段。
2. **应急推演**选择模型负荷工况和在役管段，再执行。结果显示节点表压、管段质量流量、断连模型负荷点和路径证据。退出管段及无源部分的水力值留空。推演成功后，回放从基准连通开始，可播放或手动选择「给定管段退出」「恢复基准连接」。地图、负荷点路径及压力流量随阶段联动，恢复阶段回看同次推演的基准结果。
3. **风险分析**点击「计算十个基准工况」，再选择节点。工况编号来自 CSV，十项是独立稳态工况。计算证据下载带执行 ID、输入 SHA-256、版本及稳定结果哈希。
4. **西西里**在风险窗口选择日和时，再点击「复核当前基准稳态」。发布压力、本站复核及退出演练分别显示。复核以最大压力差 0.25 bar 和最大节点标准流量残差 0.03 Sm³/s 为准，复核失败不会发布整网对比数值。

## 启动

已有本机数据的环境可直接使用 `scripts/start-gas-public.ps1`。新环境在仓库根目录执行：

```powershell
python engineering/scripts/import_ngn_sample.py engineering/outputs/research/ngn-sample
py -3.11 -m venv .venv-ngn-solver
& ./.venv-ngn-solver/Scripts/python.exe -m pip install -r engineering/configs/schutterwald_solver_requirements.txt
$env:NGN_SAMPLE_SOLVER_PYTHON = (Resolve-Path .venv-ngn-solver/Scripts/python.exe).Path
./scripts/start-gas-public.ps1 -Mode Backend -ApiPort 18234 -WebPort 5234
```

在另一个终端启动前端：

```powershell
./scripts/start-gas-public.ps1 -Mode Frontend -ApiPort 18234 -WebPort 5234
```

打开 [燃气中控](http://127.0.0.1:5234/#/gas)。FastAPI 服务需使用已经安装项目后端依赖的 Python，按需给启动脚本传入 `-Python`。西西里导入和求解器安装见 [西西里研究样本](SICILY_PUBLIC_BENCHMARK.md)。

## 口径与实测检查点

NGN 使用 pandapipes 0.12.0、甲烷物性及 Colebrook 摩阻。原始静态工况直接使用质量流量，CSV 体积流量按甲烷在 293.15 K 的模型密度换算。该温度用于样例换算；没有公开实测换算条件用于工程校准。每次检查节点质量平衡，阈值为 10⁻⁶ kg/s。管段方向用模型端点顺序表示流量正负。连接路径仅说明气源可达性。

工况 1 退出 `LINE_10077`，新增断连模型负荷点 `HOUSE_09868`，退出工况最大节点质量残差约 1.11×10⁻¹⁶ kg/s。十个 CSV 工况的基准均收敛。样例中的负荷点没有真实供气户数映射。

Windows Chrome 交互脚本为 `web/scripts/ngn-sample.browser.mjs`，生成的图片与验证记录在 `engineering/outputs/research/browser/ngn-sample/`。

### 西西里独立验收状态

| 作者发布工况 | 最大压力差（bar） | 最大节点标准流量残差（Sm³/s） | 结论 |
|---|---:|---:|---|
| 2025-11-15 12:00 | 0.199899 | 2.437645 | 流量复核失败 |
| 2026-01-08 18:00 | 2.066699 | 7.093981 | 压力和流量复核失败 |

GasNetSim 0.2.2（固定分支提交）返回的工况按项目验收阈值复核。另试供应方的交替耦合防震荡开关和更严格压力收敛限值，两个基准节点残差仍超限。默认工况下 254 号管段退出的拓扑显示 24 个新增断连节点，独立退出稳态因基准未通过而保持拒算。

## 软件验收

Windows 本机完成 24 项公开数据后端定向测试、407 项前端测试（原配置跳过 13 项）及项目构建。Windows Chrome 检查了 NGN 选管、切换工况、压力/流量结果、十工况图和数据集切换，也检查了西西里发布工况和拓扑回放。完整 Python 套件在当前 Windows 环境因缺少 LangGraph 无法完成收集。
