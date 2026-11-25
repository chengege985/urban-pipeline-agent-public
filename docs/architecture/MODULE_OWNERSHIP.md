# 模块所有权与双人协作

本轮基线 main `602b56815f8c3d450197134668ad645dbc11bac7`。用户指定的集成负责人为开发者A；2026-11-04通过GitHub实际collaborators API核实A为@catiseyeqaq（admin）、B为@chengege985（write）。不把自动化工具列入负责人、协作者或共同作者。

| 分类 | 负责人 | 当前路径/职责 |
|---|---|---|
| A_ONLY | @catiseyeqaq | engineering/src/ai_application/；web/src/features/assistant/；意图、知识、编排和助手交互 |
| A_ONLY，冻结 | @catiseyeqaq | engineering/src/agents/，既有20个内核文件按SHA冻结，所有权不授予修改冻结内核的许可 |
| B_ONLY | @chengege985 | engineering/src/cuv_application/、pipe_health/、simulation/、decision/、algorithm_validation/、gas_application/；engineering/experiments/gas_m0/（仅历史资料、夹具和回归）；web/src/features/cuv/、gas/、network/ |
| SHARED_CONTROLLED | A集成，A/B共同审阅 | engineering/src/shared_contracts/、platform/、unified/、adapters/、capabilities.py；web/src/app/、shared/、providers/、adapters/、generated/、domain/；App、路由、鉴权、Snapshot/Run、Report与OpenAPI |
| SHARED_CONTROLLED，兼容保留 | A集成，B参与 | server/演示后端、web/src/engine/及旧pages/components兼容出口；尚未完成解耦，不在本轮改算法 |

B所属算法仍遵守现有冻结清单，燃气候选不等于正式规则。Report身份与只读合同保持冻结。自动检查的冻结SHA不是CODEOWNERS自动解除的。

## 日常协作

- A从验收后的main开 `feature/assistant-<topic>`，优先只改ai_application和features/assistant。
- B从同一main开 `feature/cuv-<topic>` 或 `feature/gas-<topic>`，优先只改cuv_application、对应业务模块及features/cuv|gas|network。
- 新路由、全局Provider、OpenAPI、公共DTO、Report、权限或跨域合同需要变化时，先约定合同；由A开独立 `integration/<contract>` PR。两人不同时编辑同一个共享实现。
- 旧兼容出口只转发，不新增业务逻辑。禁止通过旧路径、动态import或shared转发层绕过私有领域边界。
- 前端未知字段保持null/unknown；平台失败显示错误，不自动回退Demo。业务工具选择是建议，权限在服务端重新检查。
- 代码审查只看本任务Diff与实际测试。V3整体BLOCK、V3-FIX1待复审、M1-B-03-P2-FIX待复审、GAS-M0-FIX待复审均继续保留。

## CODEOWNERS的实际效力

CODEOWNERS用于请求真实负责人审阅，不能阻止本地编辑；共享路径列两个账号也不代表GitHub自动要求两人都批准。本轮不更改分支保护、权限、部署或成员。合并前必须由集成负责人确认A/B合同意见、边界检查和测试证据。测试和集成门禁是必要条件，不宣称已配置强制双人审批。

全文件职责/依赖/冻结/迁移/回归映射见本目录机器矩阵及MIGRATION_MAP.md。共享规则本身（CODEOWNERS、边界检查器和基线）也属于集成PR。

## 机器矩阵的责任字段

M0和当前矩阵的`owner`、`responsible_github_accounts`、`shared`表示该实际路径按当前CODEOWNERS规则的责任，采用大小写敏感、最后匹配规则；不是历史提交作者。`target_owner`及`target_responsible_github_accounts`单独记录迁移目的路径的责任。旧pages/components兼容导出、server、web/src/engine及engineering/src/adapters继续共享受控，不因目标组件属于A或B就转交旧入口。未列出专门规则的路径按CODEOWNERS默认责任处理。解析器只接受当前明确的根默认、目录和精确文件规则，未知语法或未经核实账号直接报错。
