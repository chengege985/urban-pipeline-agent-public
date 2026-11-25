# 燃气候选闭环与公式合同

版本：`gas-adapter-candidate/0.1.0`。本文件描述当前可运行的合成能力，不把未签认的资料写成正式业务规则。

## 当前可运行能力

统一 FastAPI 提供两个只读接口：

- `GET /api/v1/gas/cases`：列出已登记的合成案例。
- `GET /api/v1/gas/cases/{case_id}`：重新读取固定输入并计算图可达性。

当前案例为 `syn-gas-case-K-001`，网络 `syn-gas-network-html54-0.1.0`，54 个节点、73 条边/管段、31 个阀节点、2 个气源、46 个消费者和46个服务对象。节点坐标的坐标系是 `schematic/px`。边是无向连通性边，阀门采用资料中明确的 `node_cut` 语义。

接口不计算压力、流量、泄漏扩散、爆炸后果、失效概率、真实供气恢复或现场关阀指令。`graph_reachable` 只表示当前合成图上存在路径，`physical_supply_status` 固定为 `unknown`。

## 候选评分算术

评分实现位于 `engineering/src/gas_application/scoring.py`，只接受已经由来源证据归一化为 `score_0_100` 的合成输入，不从原始字段猜测 G02—G15 的档位。

必填指标为 G01、G02、G04。对每个有效指标：

```text
e_i = w_i × q_i
E   = Σ e_i
P_base = Σ(required e_i × S_i) / Σ(required e_i)
P = Σ(all e_i × S_i) / E
P_low  = Σ(all e_i × S_i)
P_high = P_low + (1 - E) × 100
```

其中 `q` 只能取资料列出的 `0.4/0.6/0.8/1.0`；`E` 是有效权重覆盖，不是预测置信度；`P` 不是失效概率。必填缺失时 `P_base` 和 `P` 为 `null`，状态为 `insufficient_data`；可选项缺失时状态为 `partial`，不补零或虚构分数。

G01 另有候选比值归一化：服役年限 / 材质设计寿命，边界按当前候选的左闭右开解释，设计寿命必须来自版本化配置。G02—G15 的原始单位到 `S_i` 的阈值、统计窗、复合规则仍待业务确认。

P/C/R 的算术对照保留两版 C 与两版 H 候选，但 `H_selected=null`、`H_status=unsupported_pending_business_definition`。红线只展示高风险区域并要求人工复核，不自动给出精确四级或五级。

## 前端使用边界

`web/src/features/gas` 只读取上述接口。它展示合成拓扑、给定隔离边、新增图不可达服务、内容哈希及候选评分状态，不在浏览器重算图、评分或时间线；缺少评分证据显示“未知”。接口失败显示错误，不回退到供水 Demo。

正式健康评价需要业务方确认 H、C、M、每项阈值和可追溯的管段级记录后，另行注册版本化评分能力。确认前不得把本页的 candidate 结果用于生产决策或现场操作。
