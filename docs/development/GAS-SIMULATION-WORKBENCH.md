
# 燃气仿真工作台候选实现

版本：gas-simulation-workbench-0.1.0
分支：feature/gas-simulation-workbench-20261124

## 能力边界

| mode | 状态 | 数据身份 | 可返回内容 |
| --- | --- | --- | --- |
| synthetic_topology | ready | GAS-M0 synthetic | 固定合成拓扑的图连通、事件证据、方案步骤与合成监测关联 |
| experiment_replay | ready | US-DRYAD-CH4 public_reference | 实验文件摘要、记录数、字段和缺测统计 |
| historical_incident | ready | US-PHMSA-INCIDENT public_reference | 美国历史事故查询、质量状态和源文件/源行证据 |
| solver_benchmark | candidate | syn-gas-public-weymouth-v1 synthetic | 已登记公开算例的求解、参考误差和正向复算 |
| physical_chongqing | unavailable | real_authorized | 仅返回缺失输入和禁止输出声明 |

synthetic_topology 使用当前登记的 gas54_network.json、gas54_case.json 和 gas_cuv_replay.json。结果只说明图连通性，不能当作实际供气、压力、流量、泄漏或恢复预测。

experiment_replay 和 historical_incident 只调用已有的公共数据只读适配器。两类数据不与 K-001 资产、事件或重庆行政区进行连接。

solver_benchmark 复用现有公开 Weymouth 和串并联基准，benchmark_passed 只说明软件对公开算例的复算达到登记容差，仍保持 pending_field_validation。

## API

- GET /api/v1/gas/engineering/simulation/catalog
- POST /api/v1/gas/engineering/simulation/run

POST 示例：

~~~json
{"mode":"synthetic_topology"}
~~~

~~~json
{"mode":"experiment_replay","experiment_id":"Experiment_7"}
~~~

~~~json
{"mode":"historical_incident","category":"gas_distribution","year_from":2010,"year_to":2020,"limit":20}
~~~

~~~json
{"mode":"solver_benchmark"}
~~~

所有接口走现有 X-Water-Token 和服务端数据集/公共语料 ACL。无权限主体返回 401/403，错用数据集身份返回 409。每次结果携带 report.report_sha256 和 evidence_refs，可作为审计入口。

## Agent

现有 /api/v1/gas/engineering/agent/query 增加只读工具 simulation.query。问题中出现“仿真、模拟、回放”等词时，服务端确定性选择执行路径。模型没有权限生成物理数值，工具结果中的未知项和限制会原样保留。

## 下一阶段

- 已加入 GasLib 只读 XML 质量适配器，校验 ZIP 回执、.net/.cs/.scn 跨文件引用、成员哈希、单位和值，并输出规范化研究视图。
- 用独立求解器复核公开 GasLib 小型网络，记录节点守恒、压力边界和残差。
- 在取得运营方授权前，不开启 physical_chongqing。
- GasLib 接口为 GET /api/v1/gas/engineering/public-data/gaslib/instances 和 GET /api/v1/gas/engineering/public-data/gaslib/instances/{instance_id}/quality。结果只用于研究基准和求解器准备，不改变供水页面。
