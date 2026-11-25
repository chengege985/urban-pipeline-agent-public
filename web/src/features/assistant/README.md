# 智能助手领域（A_ONLY）

实际实现位于 pages/AssistantPage、components/AssistantChat、components/ToolCallCard；旧 pages/components 路径兼容重导出同一实现。本轮只移动文件和调整 import，不改变回答、数据上下文、工具调用及异步旧响应保护。

日常界面开发使用本目录；只依赖本领域与 shared 公共入口，通过注入的 WaterPulseProvider 访问已有受控能力。禁止直接导入 CUV/network/gas 内部实现、业务 engine 或自行绕过平台鉴权。平台模式失败不得静默调用 Demo。

当前仍是现有演示助手界面，不等于冻结 LangGraph Agent 已完成前端接入。IntentPanel、HumanApprovalPanel、独立 AgentEvidence 和 ToolCallView 尚未新增；它们是后续 A 分支任务，不能用目录命名宣称能力完成。证据徽标来自 shared/ui/EvidenceList，不表示自然语言结论已被事实验证。

新路由、Provider 方法、鉴权和共享类型变更由集成责任人安排共享合同 PR；不直接改 App、Report 或冻结 Agent。业务状态、null、unknown、partial 按响应保留。
