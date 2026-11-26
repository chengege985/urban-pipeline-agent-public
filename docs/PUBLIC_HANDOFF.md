# 城市管网智能体公开版交接

交接日期为 2026-11-26。本版从公开仓库基线 `230490c` 整合本地的 NGN 配气样例、西西里研究推演和 CUV 助手。原 GAS-M0 合成工作台、M2 数据与质量工作台、专题对话和现有工程接口随公开基线保留。默认进入 GAS-M0，右上角切换独立数据源。

## 1. 五分钟演示

本地启动后打开 `http://127.0.0.1:5173/#/gas`。可按以下三条路径验收。

| 路径 | 操作 | 可复核结果 |
|---|---|---|
| 西西里三阶段 | 右上角选「西西里」，保持默认工况，选 254 号管段，点「执行拓扑推演」，切换或播放阶段 | 基准 340 个可达节点，退出后 316 个。新增 24 个断连节点和 23 条断连支网管段。恢复阶段回看基准 |
| 西西里智能体 | 打开「任务与智能体入口 → 打开西西里助手」，问「254 号管段连接哪两个节点」，再问「它的流量」。随后问「按新增断连节点数优先，比较 342 和 254 号管段退出」，核对确认卡后执行 | 资产记录来自当前工况。两案分别新增 0 和 24 个断连节点。可点各自结果回放，执行 ID 和输入哈希在证据卡中 |
| NGN 配气样例 | 切到「NGN 配气样例」，选工况 1 和 `LINE_10077`，点「计算管段退出」；点「播放」或直接选阶段 | 98 个基准可达节点，退出后 97 个。模型负荷点 `HOUSE_09868` 断连。压力、流量和路径跟随阶段切换，可下载本次结果 |

上述数字是冻结版本的验收检查点。界面从运行结果读取数值。

## 2. 源码入口与职责

| 位置 | 维护内容 |
|---|---|
| `web/src/app/GasSurface.tsx` 与 `web/src/features/gas/GasCockpit*.tsx` | 燃气入口、页面布局、数据源切换、专题与助手容器。数据源切换会更新工作区上下文 |
| `web/src/features/gas/PublicDatasetViews.tsx`、`SicilyWorkspace.tsx`、`NgnSampleWorkspace.tsx` | 研究数据地图、工况、阶段回放、结果和来源显示 |
| `web/src/features/gas/SicilyAgentPanel.tsx`、`publicDatasets.ts` | 对话与确认卡、证据跳转、前端响应校验 |
| `engineering/src/gas_application/standalone.py`、`router.py` | 本机独立燃气 API 与主工程接口装配 |
| `engineering/src/gas_application/public_datasets.py` 与相邻来源模块 | 公开来源目录、快照校验、分页、模型结果与受控执行路由 |
| `engineering/src/gas_application/sicily.py`、`sicily_solver.py`、`ngn_sample_solver.py` | 既有拓扑算法、独立水力求解器、研究阈值 |
| `engineering/src/gas_application/sicily_agent.py` | CUV 工具选择、服务端证据、确认、比较、模型设置和本地执行登记 |
| `engineering/configs/`、`engineering/scripts/` | 锁定依赖、公开样本导入和部分数据准备 |
| `web/scripts/*browser.mjs` 与 `engineering/tests/test_*.py` | 页面端到端与数据、算法回归 |

供水历史界面与合成基准留在仓库原结构。开发时优先从对应来源适配器修改，界面只负责展示结果和执行状态。

## 3. 环境与启动

工程版使用 React、TypeScript、Vite，服务使用 FastAPI 和 Python 3.11。Windows 用独立燃气服务预览。完整平台的 Linux 依赖、部署和既有回归见 [服务端工作流](SERVER_WORKFLOW.md)。建议 Node.js 22 或与项目运行环境一致的更高 LTS，按仓库锁文件安装前端依赖。

从仓库根目录在 PowerShell 执行：

```powershell
py -3.11 -m venv .venv-platform
& ./.venv-platform/Scripts/python.exe -m pip install -r engineering/configs/platform_requirements.lock.txt
npm --prefix web ci
$platformPython = (Resolve-Path .venv-platform/Scripts/python.exe).Path
./scripts/start-gas-public.ps1 -Mode Check -Python $platformPython
```

启动脚本进入 `engineering` 运行，请给 `-Python` 传绝对路径（下面示例先解析）。

```powershell
$platformPython = (Resolve-Path .venv-platform/Scripts/python.exe).Path
./scripts/start-gas-public.ps1 -Mode Backend -Python $platformPython -ApiPort 18096 -WebPort 5173
```

另开一个 PowerShell 窗口，从同一仓库启动前端：

```powershell
./scripts/start-gas-public.ps1 -Mode Frontend -ApiPort 18096 -WebPort 5173
```

改端口时两条命令一起改。数据快照环境变量应在启动**后端**的终端设置。检查路由可用 `/health` 和 `/api/v1/gas/public-data/catalog`。既有工程目录另有 `/api/v1/gas/engineering/public-data/catalog`。

## 4. 数据交付与复现

公开 Git 仓库提供源码、导入器和运行说明。运行快照、数据库、接口凭据和求解器安装目录由接手人按来源配置。第三方数据的访问和再分发遵循来源条款。

| 数据源 | 从哪里准备 | 能做什么或如何核验 |
|---|---|---|
| GAS-M0 | 随当前公开基线的合成案例运行 | 原有综合态势、风险工作台、应急图推演和专题对话 |
| NGN The North | 获取官方 ODP17、ODP4、ODP5 原件，按《NGN The North 公开配气数据》准备 `GAS_NGN_DATA_ROOT` | 运营商公开管段地图、筛选及按来源范围显示历史信息 |
| Pipeline Engineer NGN 样例 | 用 `engineering/scripts/import_ngn_sample.py` 下载并校验固定提交的 GeoPackage 和 CSV；设置 `NGN_SAMPLE_DATA_ROOT`、`NGN_SAMPLE_SOLVER_PYTHON` | 教学模型，十个独立稳态工况可计算。户数、阀门和现场工况未提供 |
| 西西里 | 按下方导入作者快照；可选配置独立 GasNetSim 环境 | 340 个节点、350 条管线、456 个小时输入、436 个小时有完整发布结果。研究拓扑推演可运行 |
| Schutterwald | 用版本固定的 pandapipes 样本文件和模型环境 | 研究配气拓扑、可达性、验算样本及基准／退出压力对比，建筑模型接入点按模型口径解释 |
| AER | 合规取得数据及已校验 SQLite 快照，将快照目录交给 `GAS_AER_DATA_ROOT` | 资产、Release 报告和来源关联，需核对快照哈希 |
| SGN、Charlottesville | 依赖所配置的官方公开地理服务 | 按实际可用图层、事件或关联证据展示，服务不可用时界面提示 |
| GasLib-582 | 准备官方网络文件并设置 `GASLIB_582_DATA_ROOT` | 文件拓扑、网络属性与图可达性 |
| Merrimack Valley | 随代码提供公开调查引用 | 查看事故时间线、证据和调查统计 |

最短的 NGN 样例准备命令如下：

```powershell
$platformPython = (Resolve-Path .venv-platform/Scripts/python.exe).Path
& $platformPython engineering/scripts/import_ngn_sample.py engineering/outputs/research/ngn-sample
py -3.11 -m venv .venv-ngn-solver
& ./.venv-ngn-solver/Scripts/python.exe -m pip install -r engineering/configs/schutterwald_solver_requirements.txt
$env:NGN_SAMPLE_SOLVER_PYTHON = (Resolve-Path .venv-ngn-solver/Scripts/python.exe).Path
```

西西里数据从作者记录为 `4c3bf375338913c6f88749bac89cc534e4a1c0f0` 的提交导入：

```powershell
git clone https://github.com/Filippo-L-A-Munafo/GasNetSim_4Qgis.git engineering/outputs/research/sicily-upstream
git -C engineering/outputs/research/sicily-upstream checkout 4c3bf375338913c6f88749bac89cc534e4a1c0f0
& $platformPython engineering/scripts/import_sicily.py engineering/outputs/research/sicily-upstream engineering/outputs/research/sicily-snapshot
```

导入成功后重启后端。使用自选目录时显式设置 `GAS_SICILY_DATA_ROOT`。附加来源及许可、版本、检查方法见 [西西里](SICILY_PUBLIC_BENCHMARK.md)、[NGN 样例](NGN_DISTRIBUTION_EXAMPLE.md)、[NGN The North](NGN_THE_NORTH.md)、[Charlottesville 和 Schutterwald](CHARLOTTESVILLE_SCHUTTERWALD.md)、[公开来源运行说明](PUBLIC_GAS_DATASETS_RUNTIME.md)。

## 5. CUV、模型与证据

- **C · 认知与选择**处理意图、来源、工况、选中对象和上轮证据。Qwen3.8-27B 输出受约束的工具建议。服务端确认参数或清楚提示缺项。
- **U · 工具**读真实的公开研究文件，调用现有图算法，输出版本、单位、执行 ID 和输入 SHA-256。问答中的关键数值由证据生成。
- **V · 约束与取舍**检查访问授权及工况可比性。每张确认卡最多两案，有效期五分钟。重复确认由服务端幂等处理。比较指标为模型断连节点及模型需求（Sm³/s）。

西西里助手路由在 `/api/v1/gas/public-data/sicily_transmission/agent/` 下。先查 `health`，`query` 返回答案或确认计划，`confirm` 使用当前会话与工况执行。已授权结果由 `execution/{execution_id}` 恢复到地图。页面手动运行与助手共用执行库，默认存放在 `engineering/outputs/research/sicily-agent.sqlite`。

如需使用受控 Qwen 网关，参考 [西西里助手运行配置](SICILY_CUV_AGENT.md)。院内模式需要在服务器侧设置 `GAS_SICILY_APPROVED_QWEN_ORIGIN` 并与 `OPENAI_BASE_URL` 配对。浏览器只提交业务问题和引用。Windows 此前的真实模型调用在连接阶段超时，离线验收使用有证据的规则查询。模型连接恢复后应重新走一遍真实请求。

## 6. 回归与问题排查

```powershell
Set-Location web
npm run typecheck
npm run build:project
npm test
$env:GAS_DEMO_WEB_URL='http://127.0.0.1:5173/#/gas'
node scripts/sicily-agent.browser.mjs
node scripts/ngn-sample.browser.mjs
```

后端公开数据回归从 `engineering` 运行：

```powershell
python -m pytest -q tests/test_public_gas_datasets.py tests/test_sicily.py tests/test_sicily_agent.py tests/test_ngn_sample.py tests/test_cville_schutterwald.py
```

相关真实样本尚未导入时，对应研究用例会跳过。截图、真实工具响应 ID、求解器版本和复核输入在本机输出目录核验。

| 现象 | 排查 |
|---|---|
| 前端打开后来源读取失败 | 检查 18096 端口、来源状态、所选版本与目录清单，核对前后端 CORS 端口 |
| Qwen 已配置但该条回答显示规则查询 | 检查网关是否可达、模型名、密钥注入和接口返回的 JSON，查看真实调用验收记录 |
| 确认按钮在切换工况后不可用 | 重新对当前工况发起问答，确认卡绑定版本、工况、主体和会话 |
| 西西里发布曲线能看，但独立水力不显示 | 打开独立复核状态。两个典型小时的节点流量残差约 2.438 和 7.094 Sm³/s，超过 0.03 Sm³/s 复核阈值 |
| NGN 配气样例计算不可用 | 检查 `NGN_SAMPLE_SOLVER_PYTHON` 指向的环境是否安装了锁定版 pandapipes |
| 页面中的恢复阶段与基准数据一致 | 回放展示基准工况，研究输入未提供实际抢险、关阀和恢复时间 |

## 7. 接手后的改动顺序

先确认数据快照及归属，再以本文三条演示逐项复测。修改来源适配需要同时核对样本哈希、字段单位、状态和前端绑定。完善西西里水力时，以作者工况复现和节点守恒值为验收依据。真实模型验收记录网络条件、模型名和是否降级。公开更新走功能分支和 PR，保留主线与数据源的版本关联。

### 本次公开发布验收记录

- Windows 工程版依赖按锁文件安装，TypeScript 和 `npm run build:project` 通过。
- 前端全量 Vitest 为 417 通过、13 跳过。浏览器用例另用 Chrome 运行。
- 公开数据、NGN、西西里与助手、Charlottesville／Schutterwald，以及公开主线的燃气仿真定向 pytest 共 38 项通过。
- Windows Chrome 通过西西里助手问答、确认、双案比较及回放，也通过 NGN 选管、计算、三阶段回放与证据下载。模型连通性与真实工程校验另行验收。
