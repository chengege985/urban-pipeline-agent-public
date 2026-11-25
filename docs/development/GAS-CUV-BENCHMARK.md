# 燃气稳态计算与公开基准

`weymouth-uscs-0.2.0` 在现有 `GasSteadySolverAdapter` 中支持三类边界求解。只读端点 `GET /api/v1/gas/engineering/cuv-replay/benchmark` 及 Agent 工具 `cuv.benchmark.query` 共用固定套件 `syn-gas-public-weymouth-v1` 版本 `2.0.0`。新版本保留原始单位、规范化输入、文献值、第三方软件值、参考误差和代回校验。两类输入均参与逐案 SHA-256，套件文件用独立摘要绑定，旧绑定返回 409。

## 单管适用性与单位

Menon 讲义式 2.30 的水平、等径、无主动设备、等温稳态形式采用压力平方差，介质压缩因子和效率为给定常数。输入或求解端点低于公开套件最低端点 750 psia 时返回超范围。750 psia 是本计算器的基准门禁。城市低压配气的方程适用性、摩阻模型和运行区间需要独立校核。正式能力为 `pending_field_validation`。[Menon 方程与适用范围，讲义 p.36](https://www.cedengineering.ca/userfiles/P06-001%20-%20Gas%20Pipeline%20Hydraulics.pdf#page=36)

```text
Qsc = K · D^2.667 · sqrt(P1² - P2²)
K   = 433.5 · E · (Tb / Pb) / sqrt(G · Tf · L · Z)
```

- 给定两端绝压时，用有界二分求标况流量。给定入口绝压、内径及流量时，出口绝压取正根。给定两端绝压及流量时，以 `D = [Qsc / (K · sqrt(P1² - P2²))]^(1/2.667)` 求满足目标的连续最小内径。反求结果重新进入正向适配器，校核标况流量闭合。正向流量随内径单调增加，测试同时核对内径上下扰动。
- 压力统一为 psia。psig 和 kPa 表压需声明独立的大气绝压，标况绝压另列。温度按 °R 计算，支持 °F、°C 和 K 的精确转换。文献使用整数 °R 的算式保留原值。内径和长度归一到 in 和 mi，支持 mm 和 km。
- 方程流量为 SCFD，适配器返回 MMSCFD。标况 m³/d 可在同一标况换算。工况体积流量需先用所测工况和目标标况的绝压、绝对温度、压缩因子折算，当前边界仅接收标况流量。比重和 Z 必须有限且大于零，效率须在 (0, 1]。绝对温度须大于零。数值测试覆盖非法值、方向、过定边界、缺项与迭代失败。[NIST 单位换算](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b9)

## 单管对照结果

完整数据见 `engineering/src/gas_application/fixtures/weymouth-public-v2.json`。Case 03 的文献采用 14.7 psia、520 °R 标况及 535 °R 流动温度。第三方软件按 14.696 psia、60 °F 标况及 75 °F 流动温度报告。Case 13 的原始设计输入为 1400/800 psig 和 100 MMSCFD。书中基准为 14.7 psia、520 °R，第三方软件基准为 14.696 psia、60 °F。

| 计算 | 参考类别 | 参考值 | 本版 | 绝对差 | 容差 |
| --- | --- | ---: | ---: | ---: | ---: |
| Case 03 文献正算，MMSCFD | Menon 第 2 章例 13 | 163.26 | 163.255858 | 0.004142 | 0.005 |
| Case 03 软件口径正算，MMSCFD | Pipe Flow Expert | 163.18 | 163.247016 | 0.067016 | 0.08 |
| Case 13 采用 13.305 in 正算，MMSCFD | Pipe Flow Expert | 100.012 | 100.022345 | 0.010345 | 0.03 |
| 讲义例 11 出口反算，psia | Menon 讲义 pp.38–39 | 946.24 | 946.240821 | 0.000821 | 0.005 |
| Case 13 取径 A 出口反算，psia | 800 psig 边界目标 | 814.7 | 813.496442 | 1.203558 | 2 |
| Case 13 取径 B 出口反算，psia | 800 psig 边界目标 | 814.7 | 815.143862 | 0.443862 | 2 |
| Case 13 原书口径内径反求，in | Menon 第 3 章例 3 | 13.30 | 13.303651 | 0.003651 | 0.005 |
| Case 13 软件口径内径反求，in | 第三方软件试径值 | 13.305 | 13.303885 | 0.001115 | 0.003 |

两位小数的文献值采用半个末位容差。Case 03 跨实现容差依据报告的两项既有实现结果差。Case 13 正算与出口边界反校沿用上一版容差。软件试径满足略超 100 MMSCFD 的条件，跨实现口径的内径容差按流量容差、试径超量和报告精度折算。各项依据随算例返回。五个反求项以二分法代回正向模型，最大闭合差 3.48×10⁻⁹ MMSCFD。[Case 03，p.7](https://www.pipeflow.co.uk/public/documents/PipeFlowExpertCompressibleResultsVerification.pdf#page=7) · [Case 13，p.19](https://www.pipeflow.co.uk/public/documents/PipeFlowExpertCompressibleResultsVerification.pdf#page=19) · [Menon 讲义例 11，pp.38–39](https://www.cedengineering.ca/userfiles/P06-001%20-%20Gas%20Pipeline%20Hydraulics.pdf#page=38)

A/B 比较须满足相同入口压力、指定流量、管长、标况和介质条件。满足比较条件时，取径 B 的出口绝压高 1.647420 psia，指定标况流量相等。差值描述该公开工况的内径敏感性，现场安全评价待工程校核。Agent 仅能选择工具返回的事实句。

## 城市多管段选型与原型

GAS-M0 提供连通关系和合成资产属性。现有台账口径尚缺求解所需的确认内径、高程、摩阻、同一时点气源和负荷边界、气质、设备特性和校核观测。保持 54 节点图连通能力。供水 `algorithm_validation/hydraulic_baseline.py` 使用供水模型，燃气沿用本模块的工程输入合同。

| 方法 | 验证范围及要求 |
| --- | --- |
| 单管 Weymouth | 公开高压水平等径管。可核验原始输入、代回以及单位转换。输配低压区适用性待验证。 |
| General Flow 固定 Darcy 摩阻 | 本轮实现一处分流与合流的水平管原型。压力平方差与同标况流量平方成正比，串联累计压降，平行支路的端点压力相同。 |
| 可压缩 General Flow 与变摩阻 | 拟校核的城市稳态方案。连接节点满足质量守恒，连通区有明确压力边界，管摩阻由内径、实际流态和粗糙度确定。求解同时核对节点残差、管段残差及环路压力闭合。 |
| 设备及负荷模型 | 阀门需要工况和流阻，调压设施需要设定点、容量和进口限制。服务可达校核需压力相关负荷与合规压力约束。设备状态切换及瞬态与本次稳态原型分别验证。 |

原型采用 Menon General Flow 式 2.7 及第 3 章 pp.116–118 例 7 的定摩阻工况。按 AB、BCE、BDE、EF 四个等径段求解，B/E 为分合流节点。相同基准和介质下可用标况流量核对质量平衡。100 MMSCFD 分成 51.004285 与 48.995715 MMSCFD，书中值为 51.0 与 49.0。B、E、F 表压依次为 1166.596107、1130.890149、1071.110469 psig，与书中差值均小于 0.01 psi。节点闭合残差 1.42×10⁻¹⁴ MMSCFD，最大管段残差 3.78×10⁻¹⁰ psi²。Pipe Flow Expert 的第三方值随报告提供，软件以粗糙度求摩阻。[General Flow 方程，讲义 p.20](https://www.cedengineering.ca/userfiles/P06-001%20-%20Gas%20Pipeline%20Hydraulics.pdf#page=20) · [Case 12，验证报告 p.18](https://www.pipeflow.co.uk/public/documents/PipeFlowExpertCompressibleResultsVerification.pdf#page=18)

下一阶段可用公开环网、变摩阻与调压算例交叉验证模型。GasLib-11 提供后续设备和多节点输入合同的候选核对数据。正式放行需运营方授权、资产和边界确认、适用区间校准、独立历史工况验证及专业签核。[GasLib 测试数据](https://gaslib.zib.de/testData.html) · [pandapipes 可压缩流模型说明](https://pandapipes.readthedocs.io/en/latest/components/pipe/pipe_component.html) · [工程输入合同与校核清单](GAS-CUV-ENGINEERING-INPUT.md)
