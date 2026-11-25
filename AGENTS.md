# 城市管网智能体开发约定

- GitHub `main` 是统一代码基线。燃气在 129 Linux 开发与验收，Windows 用于同步和差异审阅。每个任务从最新 `main` 建立短期分支；同步用 Git 快进或经校验的同历史 bundle。流程见 docs/SERVER_WORKFLOW.md。
- 新功能只开发燃气中控及其接口、模型工具和验证。工程网页从 `web` 执行 `npm run build:project`，唯一入口为 `#/gas`。供水和历史双业务代码冻结，仅供本地复现。
- React 在 web，FastAPI、Agent 和计算在 engineering，本机模型网关在 gateway。server 保留兼容演示接口。
- 保留0.1.1 Hs/Rs语义、冻结Agent源码和Report身份合同；修改需明确任务、测试与独立审查。
- 图可达不代表实际供水达标；风险评分不等于失效概率。unknown、partial、insufficient_data不得补零包装成功。
- 默认只用合成数据、Mock模型及127.0.0.1。真实数据/模型/公网部署另行授权。
- 不提交凭据、业务原件、数据库、依赖环境、审计全量日志或离线bundle。
- 原生Windows完整后端受fcntl阻断；不得伪造模块、关闭锁或改弱测试。
- 所有变更从feature分支提交PR，由实际成员个人账号署名；不加AI机器人协作者或自动共同作者尾注。
