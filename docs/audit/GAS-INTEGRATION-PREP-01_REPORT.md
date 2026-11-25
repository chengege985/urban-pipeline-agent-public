# GAS-INTEGRATION-PREP-01 报告

## 范围与基线

本候选只在179服务器隔离工作树完成，未修改正式工程主工作树、main、比赛工作树、真实数据库、真实燃气服务、真实模型或部署配置。

- 来源冻结提交：7bf4c8529f22ecd5593fbed95f5eff8a8101db78
- 修复分支/提交：fix/gas-adapter-dashboard-audit / 0918605104c83728fc843e4f47f250deac667432
- 集成分支：integration/gas-synthetic-candidate
- 集成基线：模块化main 69fcac25383e3110904cf77b1c18144695b9d7ef
- 当前代码提交：442ce7414fd2e112506a026aacce811015a1533c（文档提交后以最终HEAD为准）
- 正式工程不是Git仓库；正式 app.py SHA 为 c535b8255ad653deda770bdd7adc9ce82b24a0b84decde078e6f1ef455767142，正式OpenAPI SHA为 a41bce933f8701d179c3e79f40e0ce80dbf6938ccdbc346ab591ce106a22e4f0。未覆盖正式树。

## 三项修复

FIX-01：燃气DTO类型只从生成的 web/src/generated/platformOpenapi.ts 通过 shared/contracts/platform.ts 门面重导出；HTTP transport通过 shared/api/platformTransport.ts 门面使用，避免feature直接跨域依赖。OpenAPI SHA为 9a97933e5a36335efa0ae6569f63897a1e3f1230c8f841973d3e35f14f23a591。

FIX-02：Node、Edge、Pipe、Valve、Source、Consumer、Service、GasCase及场景/隔离/时间线/评分/安全嵌套对象均为Pydantic结构化DTO。额外字段、非法枚举、缺失字段、类型错误、非法对象关联有专项测试。无向拓扑与node_cut语义保持，不新增压力、流量、泄漏、复气或正式健康等级。

FIX-03：WATER_GAS_FIXTURE_ROOT经过真实路径、目录和符号链接边界校验；缺失、损坏、编码错误及内部校验失败返回脱敏503；未知case_id在读取前404；伪造请求头不能改变主体模式；接口仍只有GET只读路由。

## 数据与业务语义

案例 syn-gas-case-K-001：54节点、73无向边、73管段、31候选阀节点、2气源、46消费者/服务对象，坐标为 schematic/px。结果只表示图连通性候选证据；评分状态 unavailable/not_business_validated，健康评分未知；P不是失效概率，E不是预测置信度，H未选择正式公式。

唯一燃气接口为 GET /api/v1/gas/cases 和 GET /api/v1/gas/cases/{case_id}，没有写入、关阀、复气或设备控制接口。

## 独立实测结果

证据根目录：
/workdata/WaterPipNet/01_工程项目/experiments/gas_adapter_dashboard_20261105/evidence/gas_integration_prep_01/

- 燃气Adapter/DTO/OpenAPI专项：11 passed。
- OpenAPI生成检查：85 schemas，SHA一致；合同测试7 assertions passed。
- TypeScript RC=0，生产构建RC=0。
- Vitest 189 passed、12 skipped（12项为显式RUN_BROWSER=1浏览器测试）。
- 真实Chromium既有交互12 passed。
- 真实Chromium燃气页面1 passed；仅GET /api/v1/gas/cases/syn-gas-case-K-001，显示54节点、候选未业务验证、健康评分未知。
- Report冻结回归324 passed、1 warning。
- 模块边界PASS：424 files、1199 edges、103 initialization edges、23历史许可例外、0 violations。
- HTTP探针覆盖200/401/403/404/503、伪造Header、损坏/缺失Fixture，错误正文不泄露路径。
- 平台受影响回归252 passed、5 failed、1 warning。5项均因隔离树缺少既有供水样例 V3.0健康评价结果.xlsx，应用bootstrap读取该文件时失败；该文件在来源候选、集成候选和模块化main均不存在。未复制生产数据、未修改测试。
- 含test_m1fix2_run_context.py的集合因环境缺langgraph在收集阶段失败；不计为通过。全工程pytest未作为通过依据。

## 冻结与集成

Agent20、algorithm22、Report5冻结清单SHA全部一致。Report回归通过，未发现燃气路由触发Report重算或写入。集成仅在app.py增加gas_router导入和include，没有整文件覆盖；正式工程主树未写入。

## 准入结论

允许Antigravity独立审查和隔离合成数据联调；不允许合并main、部署、对公网提供或接入真实燃气数据。正式/受控部署必须设置服务端Token和主体ACL；本地无Token时沿用既有local-demo匿名合成开发例外，不能作为真实数据授权。