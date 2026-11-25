# CUV与管网应用边界（B_ONLY）

负责人：@chengege985。现有 `pipe_health`、`simulation`、`decision` 和燃气候选继续在原位置维护；其算法/业务公式本轮未修改，冻结或待审门禁仍生效。本包不复制算法。

`api_catalog.list_tool_definitions()` 是面向共享Tool API的候选映射目录，不是运行时注册表。它只描述真实HTTP端点、输入/响应Schema来源、读写属性和缺口；所有端口尚未绑定。新增智能体不得从这个目录调用内部算法。

- 已有评分查询、DatasetView、Run及Report可通过授权HTTP读取；完整度是评分数据覆盖情况，不能作模型预测置信度。
- simulation-runs、decision-runs、assessment-runs会创建持久Run，明确属于副作用；必须先确认具体用户操作或人工批准并通过平台权限校验。
- timeline/check与evaluate使用POST但不持久化业务结果，属于计算型只读，仍执行平台run权限和快照授权。
- get_asset、get_data_completeness尚无独立稳定工具；Agent evidence响应尚无稳定OpenAPI DTO；标注adapter_required。
- 燃气候选尚未注册统一API，get_gas_assessment为unavailable，不能使用供水评分代替；当前正式CUV能力亦不能由目录命名推定已实现。

只依赖共享合同及本领域业务边界，不依赖AI编排或Prompt。未来具体适配器必须经SHARED_CONTROLLED集成PR连接平台，保持默认拒绝、Snapshot/Run身份及Report零重算合同。失败不能静默切Demo，null/unknown/partial保持原值，图可达不等于实际供水或供气恢复。
