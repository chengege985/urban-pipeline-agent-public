# 应用入口（SHARED_CONTROLLED）

AppShell继续重导出App。App按构建身份只加载一个入口：

- GasProjectApp：项目燃气演示，发布由Codex负责；只加载GasSurface。
- CompetitionApp：赛题3比赛，发布由Qoder负责；保留水务工作区并复用GasSurface。
- GasSurface：两种构建共用的燃气容器，只挂载一个GasCockpitProvider，不引入水务useDataSource。

水务Report直达、离线选择及错误状态保留在比赛入口。项目构建不包含水务页面、DemoProvider、评分、旧助手或Report运行代码；共享API传输和授权合同不改变。修改入口或跨领域依赖应由集成负责人统一处理，避免组件私自绕过领域边界。
