# 129 燃气平台一键演示

## 打开

在 129 远程 IDE 打开持久化发布目录 `/workdata/WaterPipNet/01_工程项目/releases/gas-release-04-20261119`，从「任务 → 运行任务」选择 **启动 WaterPipNet 燃气平台**。终端也可执行以下命令。

    bash scripts/start_gas_demo.sh

终端会显示本机回环链接。VS Code Remote-SSH 对 24673 启用私有端口转发，并在客户端支持时打开浏览器。浏览器型 code-server 可运行任务。预览需要已有的私有本机转发能力。默认地址为 http://127.0.0.1:24673/#/gas。

浏览器首次提示登录时，打开任务 **查看燃气演示临时登录**。临时登录信息只在交互终端显示，开发会话到期时撤销。命令入口如下。

    bash scripts/gas_demo.sh credentials

## 日常操作

    bash scripts/gas_demo.sh start
    bash scripts/gas_demo.sh status
    bash scripts/gas_demo.sh stop
    bash scripts/gas_demo.sh restart

前端、FastAPI 和 GAS-M0 用例检查通过后，启动入口即返回。Qwen 在后台预检和预热，首页会自动刷新健康状态。模型未就绪时，可继续使用已授权的合成规则查询。输入「查看K-001管段的信息」或「解释隔离事件」可检查工具结果。

重复启动会复用受管实例。默认 25 分钟后自动停止。结束时也可运行停止任务。停止只清理本任务识别出的进程及临时凭据。环境变量 WPN_GAS_DEMO_MODEL=off 会以规则模式启动。

## 合成监测到审计

Release 04 启动时核验并恢复隔离的 `demo-v2`、`demo-v3` 和一份示例拒收回执。在「事件与情景 → 合成监测预警 → 试点历史回放」选择 `demo-v3`，点选压力图中的 01:03 UTC 观测，打开阈值 `pilot-r1`、管段和事件证据，向 Qwen 询问触发原因。切换到 `demo-v2` 会清除上一个版本的详情和问答。

从合成试点观测详情选择「用 GAS-M0 观测进入 CUV 演示」。后续比较和人工确认使用既有 GAS-M0 合成案例的观测和拓扑。审计记录关联该案例的原始证据。真实试点的资料准备见[运营方交付与验收](development/GAS-PILOT-04-HANDOFF.md)。

也可从「事件与情景 → 合成监测预警」展开 K-001 压力通道的 01:12 UTC 持续越限，点击「打开异常观测与关联资产」。详情显示原始记录、样例规则和同一案例的故障事件，中央拓扑高亮 K-001。可从详情向 Qwen 查询，或打开 CUV 比较与关联事件。

发版默认启用合成处置和 CUV 协同，普通登录只有查询权限。运行 `bash scripts/gas_demo.sh operator-credentials` 在交互终端取得操作登录。CUV 的「让 Qwen 解释待确认差异」按当前输入摘要与修订只读计算。授权人员确认后，助手可解释本次变化。处置和 CUV 审计均可点回原观测和资产，工程供气指标保留未知。

## 合成事件处置演示

从干净源码启动时，需要显式开启合成处置模式。

    WPN_GAS_DISPOSITION_DEMO=1 bash scripts/start_gas_demo.sh
    bash scripts/gas_demo.sh operator-credentials

操作身份与原 credentials 命令给出的普通查询身份分别创建，在交互终端显示。两个浏览器会话可分别使用操作身份和普通身份，验证相同事件的权限差异。操作只通过事件详情中的人工确认推进，智能助手仍只能调用只读工具。处置进度属于本次合成实例，重启后从待确认开始。

## 合成 CUV 人机协同演示

使用含此功能的源码构建或发布包，在私有运行目录启动协同模式。

    WPN_GAS_CUV_COLLABORATION_DEMO=1 bash scripts/start_gas_demo.sh
    bash scripts/gas_demo.sh operator-credentials

普通登录可查询合成推演。操作登录可在中控人工确认目标和约束，并保留同一运行目录中的修订与反馈。详细入口和证据口径见[燃气 CUV 人机协同演示](development/GAS-CUV-COLLABORATION.md)。

## 恢复与开发

持久化演示目录保存 Git bundle 和预建 React 产物。Pod 重建后重新打开该目录，运行同一任务即可恢复本机缓存。恢复会核对归档、提交与 Tree，无需重新安装 Node 依赖。临时 Python 环境缺失时使用现有的持久化 Agent 环境。模型网关随同一源码提交恢复，并核对四个运行文件的固定 SHA。Qwen 需要学校 Pod 的 PPU、SDK、SGLang 和可读的现有模型权重。

有 Node 22 和锁定依赖的干净开发树可以使用 `python3 -B scripts/package_gas_demo.py <新发布目录> --commit <完整SHA>` 构建并生成源码、合约与产物清单，也可单独构建演示前端。

    cd web
    npm run build:gas-demo
    cd ..
    bash scripts/start_gas_demo.sh

Release 04 的私有运行目录 `/nix/wpn-gas-release-04-runtime` 保存临时状态、日志、网关数据库和按发布提交隔离的合成试点库。持久化发布目录保存源码及构建摘要、合同和带原始文件摘要的合成样例。

## 回滚

先在当前发布目录执行 `bash scripts/gas_demo.sh stop`。返回上一版时，从已验回滚目录启动相应固定版本。发生回滚时记录恢复包的提交与 Tree。

- Release 03 固定提交 `5dfce5ea2b226a6d049da820b2d8cf37169c0b07` 位于 `/workdata/WaterPipNet/01_工程项目/releases/gas-release-03-20261118`。先停止 Release 04，再在 Release 03 目录执行原启动任务。恢复 Release 04 时先停止 Release 03。
- Sprint 01 回滚版 `2035f05` 位于 `/workdata/WaterPipNet/01_工程项目/releases/gas-sprint-01-20261118`，保留固定源码、构建产物及原有操作入口。
- 统一主线回滚版 a16bea3 位于 /workdata/WaterPipNet/01_工程项目/releases/gas-mainline-20261118，使用原一键入口及规则查询。
- 历史版本 `6d7c198` 位于 `/workdata/WaterPipNet/01_工程项目/experiments/gas_oneclick_20261117`。
- 首版 `4ea97f9` 与上一版备份见 `/workdata/WaterPipNet/01_工程项目/experiments/_mainline_unify_backups_20261118/demo` 内的清单。
