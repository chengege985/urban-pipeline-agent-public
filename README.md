# Urban Pipeline Agent · 城市管网智能体

基于 React、FastAPI、LangGraph 和 Python 计算内核的城市管网分析与人机协同项目。统一开发同一产品；合成演示数据与授权工程数据分别管理。

**以 GitHub `main` 为基线，在 129 Linux 开发和验收燃气功能。** Windows 用于同步和审阅。[服务器开发与同步流程](docs/SERVER_WORKFLOW.md)。

## 前端产品入口

赛题3比赛版包含供水与燃气；项目演示版仅开放燃气。项目演示站点由Codex发布，比赛站点由Qoder发布。项目构建不含水务页面、入口或运行模块。同一套源码分别构建到 `web/dist/competition/` 和 `web/dist/project/`。运行方法、数据模式及接口限制见[前端入口与构建说明](docs/FRONTEND_EDITIONS.md)。

## 从这里开始

- [公开版交接、数据准备与演示验收](docs/PUBLIC_HANDOFF.md)

- [同事协作：邀请、clone、修改、Pull Request](docs/COLLABORATION.md)
- [服务器开发、变更暂存与GitHub更新](docs/SERVER_WORKFLOW.md)
- [本地安装与启动](docs/WINDOWS_LOCAL_SETUP.md)
- [燃气一键演示、模型与恢复](docs/GAS_DEMO_QUICKSTART.md)
- [燃气数据接收、质量校验与授权缺项](docs/development/GAS-DATA-INTAKE.md)
- [燃气合成监测预警与证据](docs/development/GAS-MONITORING.md)
- [燃气 CUV 合成隔离与恢复方案对比](docs/development/GAS-CUV-REPLAY.md)
- [历史导入基线与验收](docs/BASELINE.md)
- [导入源码逐文件 SHA-256](docs/SOURCE_MANIFEST.json)

## 双人模块协作

- [A/B职责和共享文件规则](docs/architecture/MODULE_OWNERSHIP.md)
- [实际依赖图及自动边界检查](docs/architecture/MODULE_DEPENDENCIES.md)
- [Agent与CUV共享接口合同](docs/architecture/INTERFACE_CONTRACTS.md)
- [首批迁移与保留门禁](docs/architecture/MIGRATION_MAP.md)
- [共同历史Bundle双向离线流程](docs/architecture/OFFLINE_SYNC.md)

A 维护助手及 AI 应用边界，B 维护 CUV、燃气和管网。共享合同由集成负责人安排审阅。

## 当前能力

- 燃气中控、双主题、资产和事件证据、区县 GIS 与合成拓扑。
- 燃气只读助手和 Qwen3.8-27B 回环推理，模型不可用时回到规则查询。

供水功能暂停开发，保留以下功能和验收基线。

- 0.1.1 简化健康评分，保留 `Hs/Rs`、版本及指标解释。
- 基于图拓扑的指定故障与给定隔离影响分析。
- 方案约束、固定效用比较、服务时间线及缺项状态。
- 统一异步 Run 与只读 Report、身份与证据一致性校验。
- Agent 的工具调用、人机意图、反馈与共识结构；本地合成启动器默认关闭 Agent，使用 Mock。

图通路可达不代表供水压力、水质或流量达标。风险评分不是失效概率。真实模型、真实数据及公网部署没有随代码上传而获准。

## 目录

```text
engineering/             FastAPI、Agent、评分/拓扑/决策、测试和合成fixture
web/                     React/Vite/TypeScript前端
gateway/                 燃气演示本机模型网关
server/                  保留的兼容 API 及其测试
public_demo_data/        明确标记的合成演示数据
scripts/                 本地合成启动与恢复检查工具
docs/                    开发、协作和版本说明
```

日常修改从 `main` 建短期 `feature/*` 分支并提交 Pull Request。

## Windows先运行前端

```powershell
git clone https://github.com/catiseyeqaq/urban-pipeline-agent.git
cd urban-pipeline-agent/web
npm ci
npm run dev -- --host 127.0.0.1
```

访问终端提示的本地地址并加 `/?mode=demo`。**原生 Windows 完整后端目前受 `fcntl` 锁限制；不要安装假模块或关闭锁。** 完整后端在Linux隔离环境已验证；本机WSL尚未安装，WSL路线需另行准备。详细启动和测试命令见安装说明。

本仓库只包含选定开发资产、必要合同和合成数据，不包含提供方500条样例、原始业务资料、真实数据库、凭据、部署产物或完整历史归档。旧Git及发布记录保存在本地迁移交付中；当前仓库从可追溯的源码快照开始，不把新导入提交冒充旧验收版本。
