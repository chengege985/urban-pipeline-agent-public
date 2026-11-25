# 共享基础（SHARED_CONTROLLED）

- ui：已迁入6个无领域私有依赖的公共组件，旧 components 路径兼容重导出。
- contracts/domain：只重导出原 domain/types，保留唯一UI合同定义。
- contracts/constants：只重导出原合同常量，不复制权重或算法。
- contracts/provider 与 api：重导出已有 WaterPulseProvider 端口和既有错误类型；不创建第二套 API Client。
- contracts/platform：只重导出 generated/platformOpenapi，API模型以 FastAPI OpenAPI 及生成器为唯一来源。
- utils/context、format、timelineContext：只转发既有输入身份校验、显示格式及时间线版本一致性校验，原 engine 文件未改；这是迁移期限定兼容，不允许引入评分/拓扑/决策算法。

shared 不能反向导入 features 私有实现。新增合同由集成责任人审阅，保留版本、来源、授权、单位及业务状态，不将未知值默认转成0或false。
