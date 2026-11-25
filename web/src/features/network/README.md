# 管网可视化领域（B_ONLY）

已迁移 NetworkMap、PipeRiskList、ServiceCards、TimelinePanel；旧路径重导出相同对象。TimelineSnapshot、metricText、metricTone 的原导出继续保留，兼容既有 Report 页面。此目录只移动显示与交互代码，未修改拓扑、评分、时间线算法或业务值。

OverviewPage、PipePage、ScenarioPage 暂留旧路径作为当前页面组合边界。FlowAnimationLayer 仅在 ui-enterprise-01 的 V3 候选中，本轮未导入或合并；动态管网的独立验收门禁保持。

从 shared 获取类型、输入身份检查及 Provider 端口。新增组件不得导入 assistant 私有实现；复用其他 B 领域时先定义公共接口，不从旧兼容 components 入口绕过边界。图通路可达不代表实际供水或供气已恢复。
