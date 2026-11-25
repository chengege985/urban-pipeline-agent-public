# 报告领域边界（SHARED_CONTROLLED）

当前实现仍在 pages/PlatformReportPage.tsx 和 pages/ReportPage.tsx，平台解析在 adapters/platformReport.ts，读取通过既有 PlatformProvider。为保留当前审查快照，本轮不移动或重写这些实现，也不建立第二套报告渲染器。

M1-B-03/P2-FIX 的待独立复审状态不因本次结构调整改变。平台报告只读已有 Run，保持 null、unknown、partial、409、异步响应身份检查，不在前端重算，也不在平台失败时转入 Demo。

后续报告迁移、路由或合同改动必须走受控集成 PR。当前新领域组件的修改不得借助共享报告页改变权限、持久化或计算行为。
