# FASTDEV-06：受控开发预览与可复用中文场景

基于7e96dfd0a821d87fe03ab091a157b201e4bc4b24。仅GAS-M0合成数据，不是生产认证或真实工程系统，未合并main。

## 启停与访问

在本地高速隔离仓根执行：

    python3 scripts/gas_dev.py doctor
    python3 scripts/gas_dev.py start --model auto --ttl-minutes 60
    python3 scripts/gas_dev.py status
    python3 scripts/gas_dev.py credentials
    python3 scripts/gas_dev.py stop

credentials只允许交互式SSH终端，显示当次临时用户名和密码。每次新启动轮换凭据；默认60分钟，最多180分钟。重复start不重复启动健康实例，也不延长已有预算。外部占用端口/PPU时不重复启动模型。不配置开机启动。

均绑定127.0.0.1：24673 React、24681 FastAPI、24695既有网关、30006 Qwen。24073/24081保留给旧V1。Windows转发：

    ssh -p 2048 -L 24673:127.0.0.1:24673 root@172.30.60.129

打开 http://127.0.0.1:24673/#/gas，用上述临时凭据。Host不接受其他域名。

这是个人开发访问层：临时Basic凭据+SSH通道，服务端仅注入限定synthetic及GAS-M0 dataset的Token，后端仍执行统一ACL。不是生产BFF或多人登录。浏览器不保存模型Key和平台Token。仅转发燃气GET及只读Agent Query POST；POST校验Origin，拒绝转发头、异常Host和其他写操作。

可用start --model off明确禁用模型。Qwen路径显示qwen_intent、模型ID、调用次数、耗时；规则路径显示local_rule_not_llm。健康状态与单次结果分开，加载期间不宣称模型就绪。

## 资源与日志

默认/nix/wpn-gas-fastdev06-runtime是当前UID拥有的700本地目录，拒绝NFS/FUSE，凭据600。SQLite仅用于新开发网关，不读写原业务库或旧网关。此运行目录禁止复制到对象存储或Git。

预算：2块PPU、bf16、8192上下文、mem-fraction-static=0.65、最多2并行推理；应用Key为test scope、单并发、60RPM、100000 token。480秒加载超时清理后降级。status报告模型ID、推理来源、服务RSS和PPU显存。

events.jsonl每30秒记录资源；services.log记录request_id、工具参数、模型/总耗时与错误码，不记录用户原文及凭据。不要公开私有运行日志。

主管、子进程组、独立清理进程绑定PID/启动时间；stop、TTL、异常退出均清理自有进程。死亡组长的残留工作进程按PGID和继承主管身份清理，不终止其他服务。启动失败不无限重试；修好环境再start。

## Pod重建恢复

1. 持久化交付目录保存完整源码bundle及SHA。先bundle verify，再clone到新的本地高速目录，核对交付HEAD/Tree。不得覆盖其他工作树。
2. Node22.23.5归档在/workdata/WaterPipNet/toolchains/node-v22.23.5-linux-x64.tar.xz/，按同目录校验信息验证后本地解压。web执行npm ci及npm run build，不改package-lock。
3. Python3.12建立隔离venv，使用engineering/configs/platform_requirements.lock.txt。本轮为/tmp/gas-fix21-venv/bin/python，恢复路径不同用--python指定。本轮新燃气Agent不依赖冻结LangGraph内核；此venv无LangGraph，不代表历史Agent全量环境恢复。
4. 权重使用已有授权/workdata/VLM/models/Qwen3.8-27B。doctor校验18分片、总字节55,563,006,776、配置/索引/tokenizer配置SHA及qwen3_5架构。未重算55.56GB所有权重SHA，不作完整来源鉴证，不下载替代模型。
5. PPU须学校原兼容镜像/驱动：SGLang0.5.18+asllm2.2.0.9、torch2.13、PPU SDK、/usr/local/bin/ninja。本轮没有制作专用运行时离线包；镜像丢失需平台恢复，不能在CPU启动27B。
6. 网关复用独立历史caf64df4d8f6e0fe5a2d794c75f88ca46fa3aef8。先恢复local_llm_fix2的58d7b3cfc8c8d88612e7f19784ac31023790b758，再验证并导入FASTDEV05交付local-gateway-fastdev05.bundle。用--gateway-repo指向clean的固定HEAD，不复制旧数据库、不新建第二套网关。
7. Playwright1.64.1对应Chromium headless shell1248；恢复匹配浏览器及系统库，BROWSER_EXECUTABLE、LD_LIBRARY_PATH可覆盖路径。
8. doctor → start → status。依赖缺失明确失败/降级；只有显式start才占用PPU，不配置公网或自动启动。

## 场景与合同

engineering/tests/fixtures/gas_agent_zh_scenarios.json含33项：26条任务分别执行规则/真实Qwen，4条ACL/输入错误分别复验，3条仅故障注入。每项含输入、意图、工具、参数、服务端查询、证据与界面动作。超时、错误模型身份及离线测试不冒充真实成功推理。

上下文只引用服务端返回或明确选中ID，最多20轮内存记录。显式新问题不继承旧资产；缺参澄清、未知ID不让模型补造。关联事件按实际asset_ids过滤，短阀门ID对应真实合成阀门资产。GIS/区县返回接口实际空状态，不生成坐标。

模型只选白名单计划及完整证据句，不是通用语义蕴含验证。新增parameters、elapsed_ms、model_elapsed_ms、error_code；OpenAPI仍98 schemas，TS按运行时导出生成。前端不重算业务风险或物理状态。

## 复验

先生产构建，再用隔离venv运行：

    # engineering
    PYTHONPATH=. python -m pytest tests/test_gas_agent_scenarios.py tests/test_gas_model_provider.py tests/test_gas_agent_http.py tests/test_gas_dev_lifecycle.py tests/test_gas_agent_tools.py tests/test_gas_accel.py tests/test_gas_application.py tests/test_gas_auth_default.py tests/test_gas_s2_fix1_acl.py tests/test_gas_sprint01_fix2_integration.py tests/test_s1fix2_security_contract.py tests/test_bff_runtime_config.py -q
    # root
    python3 scripts/check_module_boundaries.py
    python3 engineering/scripts/gas_scenario_smoke.py --runtime /nix/wpn-gas-fastdev06-runtime --output /nix/06-scenarios.json
    # web
    npm run typecheck
    npm run contracts:check
    npm run contracts:test
    npm test
    npm run build
    node scripts/gas-dev-preview.browser.mjs

另运行 tests/test_gas_standalone_contract.py：完整平台与独立燃气使用同一DTO及WaterToken声明。完整平台POST额外要求协议头X-Water-Client（local-prototype/unified-react）；独立只读Agent Query不依赖此标识，仍严格Token+ACL。它不是认证，不得作为放行依据。本轮纠正独立案例GET的旧匿名声明并补齐securitySchemes，未改变运行时权限。

场景需status ready后运行，浏览器和场景顺序运行以遵守单并发。生命周期含真实60秒TTL、SIGKILL、同端口重启与访问拒绝。最终测试记录在交付目录；不沿用祖先结果。

未完成：真实燃气授权资料、生产BFF/多人身份、RAG、物理风险模型、持久化事件处置。下一步事件闭环、证据管理和授权导入适配。回滚stop后回到FASTDEV05独立源码环境，不触碰旧V1。
