# CUV与业务展示领域（B_ONLY）

已迁移 DecisionPanel、FactorBars、HealthGauge、PlanCards、ScenarioCards；通过 index.ts 提供稳定入口，旧 components 路径继续重导出。本轮实现去掉 import 后与旧文件逐字一致，评分、权重和业务显示含义均未重写。

ScenarioPage 仍在 pages/ScenarioPage.tsx：它同时承担共享上下文、异步 Run 与报告入口，含未完成独立验收的接线，暂作为 SHARED_CONTROLLED 集成文件保留。B 的普通展示改动应优先落在本目录，不顺带改受控页面。独立 CUV 工作台、技能状态和价值配置没有在此次重构中实现。

仅通过共享 Provider 合同消费现有能力；不得依赖 assistant 私有状态、Prompt 或 Agent 编排，不复制供水或燃气算法。平台评分必须保留模型语义，目录归类不解除既有 UI V3 或 Report 验收门禁。
