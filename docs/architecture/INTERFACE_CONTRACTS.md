# Agent与CUV共享接口合同

本轮建立代码边界和合同，并新增只读的合成燃气候选接口；不改变既有供水、Agent或Report路径。Python领域合同唯一实现位于 `engineering/src/shared_contracts/`；新智能体与CUV应用通过共享Protocol交互。现有HTTP业务DTO仍由FastAPI Pydantic模型和导出OpenAPI定义，不复制Scenario、Assessment或Report模型。

## 调用信封

| 字段 | 含义与要求 |
|---|---|
| request_id | 本次请求关联ID；不是权限凭证 |
| actor_context | 平台认证与授权层提供主体和当前授权上下文；客户端自报字段不可信，不因为Schema通过就授予权限 |
| domain | water / gas / unknown；不得把unknown或gas默认解释为water |
| data_mode | synthetic / fixture_replay / real / unknown等严格声明；来源分类由可信服务端确定，输入标签不替代分类证据 |
| tool_name、tool_version | 服务端登记能力及合同版本；用户或模型文本不能自由选择高权限实现 |
| snapshot_id、run_id | 明确绑定读取对象；缺失允许与否依具体能力，不搜索外来Snapshot补齐 |
| input_schema、result_schema | 现有OpenAPI schema引用或新共享schema标识；不复制一份业务类型 |
| model_version | 保留业务算法/模型版本，未知为null；不能把demo-score改成legacy_demo或正式H |
| evidence_refs | 来源、ID、版本与哈希；访问范围另由actor_context及文档授权确定；证据ID不授予权限，也不证明散文结论正确 |
| limitations | partial、insufficient_data、unsupported、not_applicable及模型适用范围 |
| error_code | 区分认证、权限、找不到、身份冲突、输入无效、未支持等原因；不把HTTP200误作业务完整 |

当前新合同不作为HTTP入口，不改变冻结OpenAPI。不把ActorContext结构、confirmation_ref或某个布尔值当作认证、授权或用户已批准的证据。未来平台Adapter必须从请求认证上下文构造主体，验证dataset/Snapshot/Run授权和确认记录后才调用底层业务API。

## 真实能力复用与缺口

| 能力意图 | 实际可复用API | 属性/限制 |
|---|---|---|
| 查询能力 | GET /api/v1/capabilities | 需按当前鉴权执行；demo_policy不等于正式CUV |
| 查询资产/完整度 | GET /api/v1/datasets/{dataset_id}/view | 已有聚合读取；独立get_asset/get_data_completeness工具仍需Adapter |
| 查询健康评价 | GET /api/v1/assessments | 现有模型家族、版本和覆盖率语义保留 |
| 情景分析 | POST /api/v1/simulation-runs，随后GET /api/v1/runs/{run_id} | 创建持久Run，有副作用；不能标为只读纯函数 |
| 方案比较 | POST /api/v1/decision-runs，随后读取Run | 创建持久Run，需明确操作/确认和平台权限 |
| 重新评价 | POST /api/v1/assessment-runs | 创建Run/结果，不自动允许模型调用 |
| 读取报告 | GET /api/v1/runs/{run_id}/report | 严格只读、身份一致；未完成/冲突409，不重算补齐 |
| Agent证据 | GET /api/v1/agent/runs/{run_id}/evidence | 仅对应Agent执行档案；不是所有普通Run的通用证据接口 |
| Gas候选图分析 | `GET /api/v1/gas/cases`、`GET /api/v1/gas/cases/{case_id}` | 只读合成数据；仅图连通性；不借用供水评分、不操作真实阀门 |
| Gas正式CUV/现场执行 | 尚无验收的受控工具Adapter | unsupported，不操作真实阀门 |

完整精确能力目录见cuv_application及其合同测试。目录登记不意味着对应Agent工具已经上线；现有API可复用和工具Adapter已实现是两个状态。

## 意图、RAG和编排

结构化意图描述领域、问题类型（解释/检索/查询/比较/推演/建议）、目标对象、时间/区域约束、候选工具、不确定点和人工确认需求；不实现新的意图模型。

知识合同区分公开规范、授权内部文档、合成资料和未确认草案，保留文档/切片ID、来源版本、范围、原文哈希和实际发送哈希。授权过滤在读取和外发前都必须执行。本轮仅声明DocumentIndex和KnowledgeRetriever Protocol；Embedding客户端、独立Reranker和实际索引均未实现或接入。没有业务授权不得外发内部材料。

工作流边界：用户输入 → 结构化意图 → 平台权限与参数检查 → 权限内检索/候选工具 → 受控API → 证据聚合 → 回复 → 必要人工确认。所有副作用必须在实际调用前完成确认；回复后的确认不能追认此前写入。新端口没有直接数据库、Snapshot写入、算法对象或燃气阀门执行能力。既有冻结LangGraph保持原实现，历史耦合见MODULE_DEPENDENCIES.md。

## OpenAPI与前端类型

冻结的 `engineering/04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json` 是前端平台类型生成输入；实际Pydantic导出必须与之结构全等。脚本默认从本仓库engineering读取，`node web/scripts/generate-platform-types.mjs --check`只比对、不写文件。生成文件不可手改；shared/contracts仅转发唯一生成类型和既有前端合同，不建立第二份同名DTO。

后续新工具若要暴露HTTP，应在独立集成PR注册Pydantic请求/响应，生成OpenAPI和TS，并添加权限/空值/版本/异步/错误回归。本轮不预先伪造新路由或宣称已接入。
