# 首批迁移映射与未迁移项

基线为main 602b568；不是ui-enterprise-01。代码变更只在179新隔离GitHub历史仓库完成，不覆盖旧生产工作树及dirty文件。

| 原位置 | 新领域入口 | 处理 |
|---|---|---|
| pages/AssistantPage、components/AssistantChat、ToolCallCard | features/assistant | 实际机械迁移，原文件保留兼容export；不增加AI功能 |
| components/DecisionPanel、FactorBars、HealthGauge、PlanCards、ScenarioCards | features/cuv | 实际迁移展示，不迁移/重算业务算法 |
| components/NetworkMap、PipeRiskList、ServiceCards、TimelinePanel | features/network | 实际迁移；Timeline辅助导出保留，图可达语义不改 |
| 多领域共用UI | shared/ui | 提取现有实现，原路径兼容导出 |
| domain/types、generated/platformOpenapi、Provider接口 | shared/contracts等公共入口 | 唯一原定义保留，只重导出，不复制合同 |
| App、ScenarioPage、Report页、Provider、useDataSource | app公共壳入口、features/reports领域说明 | Report与Scenario仍从原pages加载，没有创建第二个Report入口；登记历史共享耦合，不借重构改行为 |
| features/gas | 燃气候选中控页面 | 只消费 `/api/v1/gas/cases/{case_id}`；不在浏览器重算，不把候选结果当正式健康评价 |
| engineering/src/agents | 原位冻结 | 不移动、不改写20个内核文件 |
| engineering/src/ai_application | 新A领域 | Schema使用共享合同；新增端口和模块说明，不替换已验收Graph |
| engineering/src/cuv_application | 新B领域 | 真实API能力目录/受控边界，不复制评分或拓扑 |
| engineering/src/shared_contracts | 新共享领域 | 严格请求/结果/意图/知识合同；未注册为新HTTP API |

文件级映射与依赖见本目录机器矩阵。compatibility wrapper仍算受控文件，不能让新代码借它越界。旧代码保留历史；实际迁移不删除原导入路径。

## 有意保留的结构冲突

1. App.tsx、ScenarioPage和types.ts仍是旧实现的共享节点；新领域可独立改内部组件，但新增全局路由或跨域字段仍需集成PR。
2. PlatformProvider和四个Adapter继续共享受控。平台失败不自动调用Demo，Report读取不前端重算。
3. 既有冻结Agent的直接算法/平台依赖按具体边豁免并冻结；新增ai_application不得复制这种导入方式。
4. main无V3 FlowAnimationLayer或V3 AppShell，不能为填目录导入BLOCK候选。
5. 现有Demo兼容server与前端engine未在本轮重写/删除；不把既有演示算法当作正式供水/燃气规则。
6. Gas-M0候选已迁移到 `engineering/src/gas_application` 并以只读合成接口接入；`engineering/experiments/gas_m0` 仅保留原始资料、夹具和回归证据，不再作为运行时包或兼容导入路径。业务评分门禁仍保留。

## 后续独立PR

- 集成责任人：受审App/Scenario路由注册与全局Provider解耦；需要M1-B-03-P2独立审查后确定基线。
- A与平台集成：可信逐请求actor_context、确认记录和受控HTTP工具Adapter；不得从客户端复制身份。
- B：燃气业务Adapter、CUV能力和中控；先完成GAS-M0独立验收与业务口径确认。
- V3：先独立复审修复，再讨论单独集成；本轮不合并ui-enterprise-01。
