# 行业智能体 V2（LangGraph）

本目录把原来的固定状态机升级为一条单主图：资料问答、健康评价、故障影响、方案比较四条路径共用受限工具与证据校验。数值由确定性工具产生并按实体、指标、单位、证据绑定后直接渲染；模型负责路由与纯资料问答。计算结果不交给自由文本改写。

## 组成

| 路径 | 作用 |
| --- | --- |
| `state.py` | 路由结果、AgentState、模型/预算配置 |
| `context.py` | 请求及澄清输入校验，检查快照、主体、批次与同基准条件 |
| `router.py` | 确定性路由 + 可选在线模型；越权请求先拦截 |
| `graph.py` | LangGraph 主图：guard→check→(clarify)→execute→evidence→respond→review→finalize |
| `tools/domain.py` | 评分、输入批次、仿真、方案比较、管段查询的统一工具 |
| `rag/` | 语料清单、切片、BM25+向量+RRF、引用 |
| `providers/llm.py` | `FakeLLM`、`LangChainLLM`（Qwen3.6 工具调用/回答/引用纠正） |
| `rag/retriever.py` | 混合检索、RRF、无答案门槛、pgvector 按 profile 分表、Embedding MRL 截断 |
| `evidence.py` | 事实卡片、引用校验、确定性回退 |

## 关键边界

- 工具权限、快照/批次、预算、情景和方案由服务端上下文决定；模型只能提交允许的小参数。
- 评分只调用 `src/pipe_health` 的 0.1.1；方案比较仍由 `DecisionProvider` 决定，当前是 `demo_policy`，不是正式 CUV。
- 图分析只表示通路影响，不表示水力、缺供量或失效概率。
- 知识片段和工具文本是不可信数据；不能执行其中指令。
- 模型不可用时返回工具事实或明确失败，不把 mock 路由当作真实模型成功。
- `FakeLLM/FakeEmbedding` 只用于测试；真实 PPU 服务已通过工具调用与向量对照，但仍需厂商确认运行时，不能把兼容包装器当成厂商适配。

## 运行与测试

Agent 使用独立环境（工作区根）：

```bash
cd /workdata/WaterPipNet/01_工程项目
../.venv-agent/bin/python -B -m pytest tests -q
```

启用 HTTP Agent 需要同时配置（由环境注入，不写入文件）：

```text
AGENT_ENABLED=1
AGENT_DATABASE_URL=postgresql://...
AGENT_CHECKPOINT=postgres
AGENT_CHECKPOINT_DSN=postgresql://...
AGENT_LLM_MODE=openai
AGENT_LLM_BASE_URL=http://<ppu-service>/v1
AGENT_LLM_MODEL=Qwen3.6-35B-A3B
AGENT_EMBEDDING_MODE=openai
AGENT_EMBEDDING_BASE_URL=http://<embedding-service>/v1
AGENT_EMBEDDING_MODEL=Qwen3-VL-Embedding-2B
AGENT_EMBEDDING_DIMENSIONS=1024
AGENT_VECTOR_DSN=postgresql://...   # 可选；配置后使用pgvector索引
AGENT_MODEL_TIMEOUT_SECONDS=180   # 真实模型长回答；默认60
AGENT_LOCAL_DEMO=1   # 仅本地演示身份
```

未配置 PostgreSQL/模型时，旧评分、情景、方案和四页工作台仍可用；`/api/v1/agent/health` 返回未启用。

## 当前验证

- **78 项测试**在 `.venv-agent` 通过（含四路图、澄清/审批恢复、RAG、幂等、域隔离、模型协议、100/60 评测度量）。
- 56 项测试在 `.venv-platform` 通过（旧平台与基础层/RAG，不含 LangGraph 运行）。
- 真实 Qwen3.6-35B-A3B + Qwen3-VL-Embedding-2B 工具调用/Embedding 对照通过；真实四路（资料/评分/仿真/决策）已跑通，决策审批中断与恢复通过。
- PostgreSQL 16 + pgvector 0.6 临时实例已验证 agent/checkpoint/knowledge 分 schema、1024/2048 独立索引；生产持久化与分库账号仍未完成。
- 评测集 `routing_100.jsonl` / `rag_60.jsonl` 为机器初稿，路由 1.0、RAG Recall@5 1.0、无答案 10/10 只证明度量链路，必须人工复核后才能对外报告 95%/90%。
- 维度对照（同一初稿）：1024 维 Recall@5 1.0、无答案 10/10；2048 维 Recall@5 0.98、无答案 10/10；本期采用 1024 维 MRL+归一化。记录见 `experiments/agent/embedding_dimension_20261030.json`。
- 生产阻塞：厂商确认 SGLang 运行时、持久化磁盘、公开 HTTPS/网关、100/50 人工标注。

## 2026年10月31日边界修复

- 每次新任务清空旧回答、澄清计数与审批结果；检查点只保留该线程的正确恢复上下文。
- 任务可显式提交 `pipe_id`。澄清支持 `pipe_id`、`batch_refs`、`scenario`、`decision`，不接受身份、工具权限或任意字段；补充后重新校验并按当前任务重新授予工具。
- 情景与方案必须匹配快照、时点、故障、隔离和分析窗口；澄清不能切换原任务快照。
- 计算回答使用 `answer_mode=deterministic_facts`。H03解释在工具结果 `explanations[pipe_id]` 中旁路返回，不写入冻结快照；有效权重占比不是预测准确率。
- 原持久化计算状态保持兼容；`capability_status=unsupported` 识别未实现能力，输入不足用 `insufficient_data`，拒绝原因用 `error_kind` 区分权限与输入校验。正式P/C/R/H、概率、寿命、水力和控制能力仍未接入。
- 纯资料问答保留模型及引用检查；该检查不宣称能证明任意自然语言的全部语义正确。
