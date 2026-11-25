# WaterPipNet 统一平台后端

本目录提供 FastAPI、异步任务、身份与数据权限、Agent 接入和历史证据读取。M1-FIX2 是开发交付基线，仍需 Qoder 独立复审；源码交付不表示已部署。

评分继续调用 Python 0.1.1；拓扑、时间线和效用算法未改变。`src/agents/` 按独立验收 SHA 冻结。平台不控制实际设施，不下发工单，不把图可达解释为供水达标。

## 当前接入范围

- `X-Water-Token` 由服务端解析为主体。禁止用 `X-Agent-Actor`、客户端自报 ID、全局 `agent_principal` 或 `AGENT_LOCAL_DEMO` 代替认证。
- 所有写接口及私人 Run、反馈、Agent 线程与证据读取都需认证。未配置凭据时仅保留受限的公开合成数据读取。
- `X-Water-Client` 接受 `local-prototype` 和 `unified-react`，只表示协议客户端；写操作仍需 `Idempotency-Key`，仍执行身份、权限与同源检查。
- 当前平台 Agent 仅允许 `AGENT_LLM_MODE=fake`、`AGENT_EMBEDDING_MODE=fake`。旧助手仅允许 mock 网关。真实模型配置明确失败，不自动退回其他模型。
- 检索器由服务端显式注入，启动 Agent 不会自动扫描或索引项目材料。旧资料检索同时要求 `legacy-materials` corpus 权限和显式 `WATER_LEGACY_MATERIALS_ROOT`。

## 服务端身份配置

`WATER_PRINCIPALS` 是令牌到身份的 JSON 映射，只能由可信配置提供。以下是格式示意，`<temporary-token>` 必须在隔离联调环境外部注入，不能作为发布凭据：

```json
{
  "<temporary-token>": {
    "actor_id": "test-operator",
    "scope": "internal",
    "modes": ["synthetic"],
    "datasets": ["<server-snapshot-id>"],
    "corpora": [],
    "permissions": ["run", "resume", "review", "import"]
  }
}
```

| 字段 | 约束 |
| --- | --- |
| actor_id / scope | 服务端可信身份；`local-demo` 和 `legacy-unverified` 是保留历史标签，不能用于认证 |
| modes | 允许的数据模式；不等于具体数据集授权 |
| datasets | 有值时精确匹配 snapshot ID；real 必须显式给出匹配范围，缺少字段也不放行 real |
| corpora | 默认空；检索前和历史引用读取时按当前授权复核 |
| permissions | `run/resume/review/import`；未配置时保留认证操作者的兼容权限，最小权限账号应显式配置 |

`WATER_API_TOKEN` 是单一开发身份的兼容配置；存在非空 `WATER_PRINCIPALS` 时优先使用主体映射，不能用共享令牌绕过它。`WATER_DEV_MODES/DATASETS/CORPORA/PERMISSIONS` 约束该兼容身份，不适合作为公网多用户认证。

内联重算请求目前没有可信来源 dataset 绑定，因此只接收获准的 synthetic 输入。授权读取 real 快照的合同已收紧，但不代表 real 重算或真实数据联调已获准。

## Run 与 Agent 合同

- 评分：提交 `/api/v1/assessment-runs` → 轮询 `/api/v1/runs/{run_id}` → 读取完成结果、报告。生成的合成快照由已完成的所属任务建立持久关联，不能由客户端自报取得权限。
- Agent：提交 `/api/v1/agent/chat` → 轮询 `/api/v1/agent/runs/{run_id}`；线程和证据分别在 `/threads/{thread_id}`、`/runs/{run_id}/evidence`。
- resume：`/threads/{thread_id}/resume`。入口和 worker 均重查当前身份、权限、数据、revision、中断与 checkpoint。审批动作另需 review 权限。
- 新问题由服务器生成 run ID、fingerprint 和 run_context。synthetic 数据集不证明用户文本为 synthetic；当前任意文本分类保持 unknown。
- 恢复创建新的队列任务 run_id，但沿用暂停执行的 execution_run_id；两者分开展示，禁止将继承的结果伪称新工具执行。

## 持久化与跨进程边界

AgentStore 增加执行绑定与事件档案。档案由受控 graph execute 更新写入，包含身份、快照、工具、版本、参数和结果哈希。读取必须通过所属主体及当前数据/语料授权，完整性失败明确拒绝。

档案标记 `platform_observed_execution`、`usable_as_live_receipt=false`。它可跨进程读取，但不能反序列化成内核当前 Ledger 的凭据。已经包含 verified_facts/执行回执的暂停状态跨进程恢复返回 `unsupported_cross_process_receipt_restore`；不能静默认证旧事实。尚未执行工具的澄清状态可以在身份和 checkpoint 复核后恢复。

表结构仅在隔离 SQLite/PostgreSQL 实例验证。本轮没有迁移正式数据库；正式迁移、备份、回滚和持久化恢复策略另行验收。

## 联调与测试

用 `.venv-agent`、`create_app(test_root, bootstrap=False, start_worker=False)` 和隔离 AgentStore/检查点创建测试应用。不得把测试根目录或 DSN 指向正式业务环境；默认 CLI bootstrap 会导入已有样例，本轮测试未使用该入口。

长期回归包括 `test_m1fix2_identity.py`、`test_m1fix2_agent_security.py`、`test_m1fix2_agent_boundary.py`、既有队列/契约测试及冻结 Agent 回归。完整执行命令、PostgreSQL DSN 类型说明和实际结果见 M1-FIX2 交付快照及报告。

实际浏览器/HTTP 联调、React 凭据注入、统一产品端到端验收尚未由本轮完成；公网跨源、单位认证、真实模型和真实工程数据均不在本轮准入范围。