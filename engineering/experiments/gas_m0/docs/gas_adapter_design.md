# 燃气最小业务闭环与行业适配合同

版本：gas-adapter-design-candidate-0.1.0。状态：GAS-M0设计交付，等待技术验收。以下“拟新增”均未注册到现有FastAPI、React或Agent；不能按接口名称认为已经部署。

## 当前最小闭环

```text
冻结的合成燃气结构与来源SHA
  → 选择一个给定故障和与之匹配的给定隔离方案
  → 在内存中应用故障边移除与明确的阀节点切断
  → 对比正常及当前多源图可达性
  → 保留原有不可达、新增不可达和当前可达的服务
  → 回放显式给定的合成事件
  → 保存候选计算结果和证据
  → 经批准后的平台Run与只读报告适配
```

GAS-M0实际交付前六项的离线结构化数据及纯函数验证。平台持久化Run、HTTP路由、正式Report和React接线属于下一阶段。这里没有实际阀门控制、真实气体求解、故障预测或复气审批。

## 与现有平台逐项对应

依据179本轮只读源码：`src/platform/schemas.py`、`app.py`、`dataset_api.py`、`timeline_api.py`、`src/simulation/topology.py`、`src/unified/report.py`及冻结清单。

| 平台机制 | 可复用内容 | 当前合同缺口与下一阶段处理 |
|---|---|---|
| 服务端身份与授权 | Principal、数据模式/具体dataset权限、Run归属、关联读取 | 燃气数据模式由服务端可信来源确定；HTML合成分类不代表可接受任意客户端标签。不能把内部目录加入公开RAG |
| 快照 | 固定as_of、来源、ID、内容哈希、版本、不可变结果引用 | 当前SnapshotPayload是0.1.0，extra=forbid，没有industry字段。GAS-M0独立结构不能直接POST到当前导入接口；须注册版本化GasSnapshot适配合同 |
| Dataset视图 | nodes/edges/services以及partial和缺字段状态 | 现评分只接受legacy_demo/0.1.1；燃气不提供虚构Assessment，不使用供水Hs替代。正式燃气评分未确认前只显示未提供 |
| 拓扑基础 | 多源遍历、并行边、前后可达对比、any/all服务规则 | 原型阀门为节点切断；平台IsolationPlan.valve_ids指向is_valve边。需要显式node-cut语义与映射，不能把管段改标为阀门 |
| 异步Run | 排队、执行状态、幂等、主体/快照绑定、持久化结果 | 拟注册`gas_topology`能力或经批准的领域任务适配；现有assessment任务不会调用燃气候选评分 |
| 时间线 | 逐输出可用性、带时区as_of、小时、窗口边界 | 本例回放合成状态切片。物理复气、任务结束和图通路恢复分开；无记录保持null，不能用最后动作代替恢复 |
| Report | 从已完成Run只读取得结果、身份与证据一致性、409拒绝冲突 | 现有冻结Report并不接受任意燃气结果。先评审新增领域结果/附件合同及版本，保持原身份校验；本轮不扩展或绕过Report验证器 |
| React Provider | 只消费API、202轮询、null/unknown/partial/409、旧响应保护 | 尚无Gas DTO/Adapter，下一阶段新增映射；不在前端执行G01—G15、图遍历或事故计算 |
| Agent | 冻结内核、证据与工具运行回执、外发门禁 | 本轮不注册工具或改提示。待业务工具和HTTP验收后另行评审接入；不把有页面/有工具名当接线完成 |

以下为本轮从179源码装饰器及APIRouter前缀核对的已有路径，**仅复用工程机制；当前不接收本目录的Gas独立JSON**。行号对应`evidence/remote_protected_after.json`记录的版本。

| 已有接口 | 源码位置 | 燃气接入前的条件 |
|---|---|---|
| `GET /api/v1/capabilities` | app.py:159 | Gas能力须在真实实现和验收后声明 |
| `POST /api/v1/data-batches` | app.py:180 | Gas导入合同、可信来源及版本先批准 |
| `GET /api/v1/snapshots`及`/{snapshot_id}` | app.py:199、205 | 同一身份/数据集授权，GasSnapshot须经适配注册 |
| `GET /api/v1/datasets/{dataset_id}/view` | dataset_api.py:370、378 | 新增Gas类型映射，不注入供水legacy_demo评分 |
| `POST /api/v1/simulation-runs`及`/api/v1/decision-runs` | app.py:241、245 | 现任务合同不自动变成燃气任务；Gas能力注册另审，不借旧入口调用供水算法 |
| `GET /api/v1/runs`及`/{run_id}` | app.py:254、260 | 保留异步状态、归属和结果版本，新增Gas结果合同 |
| `GET /api/v1/runs/{run_id}/report`及`/export` | app.py:288、280 | 保留冻结Report身份验证；Gas附件/结果类型获批后才可消费 |
| `POST /api/v1/timeline/check`及`/evaluate` | timeline_api.py:427、440、473 | 注册Gas逐输出能力，不将给定事件或工期当实际复气 |

本轮没有实际调用这些HTTP接口，不以只读路由分析替代后续集成测试。

## Node Valve和Pipe身份

提取数据使用独立`node_id`、`edge_id`、`pipe_id`、`valve_id`、`source_id`、`consumer_id`和`service_id`；保留`prototype_id`可回到原HTML对象。合成ID统一`syn-gas-`前缀。

每条图边端点来自HTML中的`e.a/e.b`。坐标仅为示意布局，`from/to`只表示序列化端点顺序，不是上游/下游；当前模型为无向连通，真实流向未知。任何真实气力流向、调压器方向或止回关系均须显式数据，不能从地图朝向补齐。

阀门节点切断表示该节点不参与可达路径。其关联边由明确端点引用得到，**不是按地理距离匹配**。该计算语义是HTML原型的建模假设，不代表现实阀门恰好控制所有相邻管段。候选数据用`Valve.cut_semantics=node_cut`、原节点ID、`incident_edge_ids`及来源表达；燃气模型不可假冒现有边阀合同。新增适配接口之前，保持离线候选格式，不向平台导入有损转换结果。

隔离方案包含适用故障/情景ID和明确的闭阀/禁用边集合。GAS-M0按原型`unitValves`等构造逻辑提取首个给定候选，但不称为最小、最优或现场可执行方案。未来给定方案和自动搜索隔离应是两个能力；后者本期不实现。

## 结果和时点合同

| 字段 | 必须表达的内容 |
|---|---|
| 身份 | network_id/case_id、内容哈希、算法版本；接平台后再由服务端签发dataset/snapshot/run身份，客户端不能代签 |
| 来源 | 原HTML完整SHA、提取段SHA、固定种子、Node/V8版本、原始对象ID、新增合成案例出处 |
| normal_unreachable | 原来就不存在图通路的对象；不计入新增影响 |
| newly_unreachable | 正常存在通路、当前失去通路的对象 |
| current/graph_reachable | 当前是否存在模型中的通路；不得显示为“供气压力合格”或“可以复气” |
| count | M0只接受原型明确的正整数聚合计数，未知/null会拒绝且不会补0；设施权重不当人数。M1计划支持未知总量null及已知小计，届时另增合同和测试 |
| events | 明确事件来源、相对小时与完整状态切片；绝对时间接入时必须带时区并绑定同一as_of |
| graph_service_restoration_hours | M0固定故障案例没有恢复事件，返回null+insufficient_data；计算图通路恢复是M1待扩展能力，不能冒充实际复气 |
| actual_gas_restoration/pressure/flow | 本期unsupported或未提供，不输出伪造数值 |
| plan_completion_hours | 没有任务结束证据时insufficient_data；不取最后事件时间代替 |
| business_status | M0网络整体为partial、单项可用为available；算术模块可用为ok，冲突/不足分别标记。M1拟统一succeeded/partial/insufficient_data/not_applicable/unsupported的对外枚举，须经合同评审，不能宣称现在已完全统一 |

原型的8h/5h工期、0.15阀失效、点火概率和PIR/积聚参数保留在演示参数清单，默认不参与最小图通路闭环。事件案例明确注明是本轮新增合成输入，不能标成原HTML或真实运营记录。

## 报告与错误的边界

燃气候选结果仅为本地JSON，不能以`ReportResponse`名义发到正式页面。批准接入后，Report阶段仍只读已冻结Run结果，零重算、零写入、零外来快照补齐；top-level与nested身份冲突仍拒绝。不得向冻结验证器添加“gas时跳过”分支。

将来HTTP沿用现有区别：401无可信身份、403没有资源权限、404当前可见范围内资源不存在、409运行未完成或身份/版本冲突、422输入格式/引用/数值非法。合法局部结果可200+partial；缺少专业模型不能填0后返回成功。实际HTTP路径和返回类型要在下一阶段OpenAPI评审时冻结，本轮没有声称这些Gas路由已实现。

## 拟新增模块

| 模块 | 职责 | 本期状态 |
|---|---|---|
| `waterpipnet_gas/network.py` | 独立结构验证与合成图通路检查 | 本隔离副本实现；不导入供水服务 |
| `waterpipnet_gas/scoring_candidate.py` | 仅对已给标准分做候选算术复核和冲突展示 | 本隔离副本实现；不是正式评分入口 |
| `tools/extract_gas_network.mjs` | 可复现HTML结构提取及首例构建 | 本期实现 |
| `tools/build_gas_field_matrix.py` | 438字段来源与用途分类 | 本期实现 |
| 未来`src/gas_adapter/contracts.py`、`snapshot.py` | 领域模型、来源和现平台快照映射 | GAS-M0批准后新增，不直接改现Schema接收任意对象 |
| 未来`src/gas_adapter/topology.py`、`timeline.py` | 经审查的node-cut投影、只对显式事件计算 | 先进行合同与等价性测试，再注册能力 |
| 未来平台Run/Report领域适配及React Gas Adapter | 身份绑定、只读报告、业务状态展示 | 专项批准后实现，保留现Report和Agent冻结基线 |

本例的Node vm提取与Python连通检查属于确定性合成结构验证；检验工具没有专业模型、网络、数据库或控制设备的调用权限。`validate_network`中的来源SHA检查只验证字段格式，不证明任意调用方声明的来源真实。当前交付的来源保证来自固定原HTML SHA、固定提取段、重提取字节比对和交付清单；未来平台必须独立验证可信来源和权限，不能相信客户端的synthetic、source_ref或SHA标签。
