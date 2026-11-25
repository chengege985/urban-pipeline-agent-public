# 本轮实际验证与保留门禁

最新FIX2开发验证见文末“2026-11-05 FIX2”节；以下原交付表和中间轮次按历史记录保留，不能与最新测试数混用。

本文件为CODEX-MODULAR-COLLAB-01开发交付记录，不替代独立集成验收。代码来自main 602b568，通过Bundle导入179隔离仓库；未覆盖原服务器树。

| 验证 | 本轮结果 |
|---|---|
| Python/TS导入边界负例 | 32 passed；包括相对/动态导入、别名、转发绕界、循环及冻结SHA |
| 离线Git Bundle负例 | 10 passed；dirty、错误SHA/分支/祖先、已有分支和main目标均拒绝 |
| 新工具/意图/RAG合同及实际OpenAPI | 41 passed（40新合同＋1实际OpenAPI全等） |
| Report身份与平台回归 | 346 passed，0 failed，0 skipped；245条record_property/JUnit兼容警告原样留存 |
| 最终前端 | 201 passed：原171单元＋12真实Chromium交互＋18兼容；0 failed/0 skipped |
| 前端typecheck/build | 每个迁移单元均通过；迁移单元产物JS/CSS哈希名称保持一致 |
| 真实FastAPI＋Chromium | 137断言通过；22次Report请求覆盖200/401/403/404/409 |
| 生成TS漂移反例 | 7断言通过；--check不覆盖错误文件，默认源为仓库engineering |
| 扩展Agent/API | 初次完整范围419 passed、11 failed、12 skipped；后续定向3项守卫误报已通过，7项旧失败基线/当前一致，另1项缺原始500条重算输入 |
| PostgreSQL/pgvector | 12项因未配置独立DSN跳过，UNVERIFIED；未连接已有实例 |
| 外部测试资产/部署辅助模块 | 500条原始重算输入及deploy.ppu相关5项未在本轮补入；不计通过 |

扩展回归不能写成“全量全绿”。已有7项身份失配：test_agent_api的health/materials、idempotency、unsupported、clarification、approval、explicit_pipe，以及test_workbench四页。旧fixture依赖AGENT_LOCAL_DEMO/X-Agent-Actor或无Token写入，现行认证应拒绝。已在原602b568和当前候选分别执行同7项，失败表现逐项相同；两轮关键源码和测试SHA相等。需要单独更新可信身份fixture及旧Streamlit工作台接线，不能降低安全边界。本轮没有修改这些测试或受控实现。

三项守卫修正是测试环境问题：SQLite的file:只读URI被错误当作外部路径，以及两个既有固定Python子进程被一概拒绝。修正运行器后，备份与两个跨进程恢复测试3/3通过；子进程同样安装网络、数据库路径和写入限制。运行器保留在本轮离线证据，不进入产品源代码。

原测试需要的500条既有评分结果以fixture_replay/source_unknown核验历史SHA后仅留179隔离测试资产；不声明其为真实或合成，不上传GitHub。原始输入表没有为了重算而扩大复制范围。最初缺少该离线输出的运行和/workdata临时清理失败日志均保留，最终Report和合同运行已使用独立/tmp目录。没有改业务测试断言来适配失败。

## Report零副作用

22次报告读取：计算0、SQL写0、文件写0、外来Snapshot读取0，持久化前后摘要全部相等。场景准备/页面正常创建Run共执行8次Runtime，单独计数，不与Report读取混淆。图可达、未知值、partial、409及旧响应行为由原回归验证，不新增前端重算。

## 冻结与行为一致

- Agent20、供水算法22、Report5完整SHA均匹配固定参考。
- 18个迁移组件去除导入语句后正文与基线逐字一致，旧路径仍可导入。
- 28个App、Scenario、Report、Provider、hooks、domain和engine等受控前端文件SHA不变。
- 179原产品映射282文件与审计开始时SHA一致，旧dirty/工作树保留。
- OpenAPI0.1.1、65个schema、27个路由操作未改；生成TS只有来源路径注释改变。

## 本轮不解除的门禁

V3整体BLOCK；V3-FIX1、M1-B-03-P2-FIX、GAS-M0-FIX仍待独立复审。本次不导入UI候选，不注册Gas API，不改Agent核心、不调整公式，不部署、不合并main。

新ai_application、cuv_application是开发边界、结构合同和Protocol，不是已接入的业务Agent/模型/索引。旧冻结Agent仍保留23条历史跨域依赖；新的领域代码不得增加这种依赖。静态检查不构成生产级安全证明。

## 实际执行入口

- Python合同与Report：179 .venv-agent/Python3.12.3，临时HOME/TMP/cache/SQLite全在本次/tmp目录；清除真实模型/数据库/代理环境变量。
- 前端：179 Node22.18.0及已有依赖，Vitest固定maxWorkers=2/minWorkers=1；RUN_BROWSER=1使用已有Chromium。
- HTTP浏览器：原web/tests/integration/m1b03/run.mjs，显式传入隔离dist与engineering源码路径；服务仅绑定本轮127.0.0.1临时端口。
- 全量SHA、Diff、JUnit、实际请求/副作用、前后身份与同步记录放在179本轮evidence及Windows接收目录；不把原始数据库/Token/完整审计包放入GitHub。
- CI配置提供边界、合同、Report及构建自动检查；远端CI是否完成应另看PR状态，不能用本地声明冒充CI成功。

## 最终所有权一致性校正

交付只读核对发现旧兼容入口被机器矩阵错误按目标领域归属。已将当前所有权改为直接解析CODEOWNERS，并分列target_owner；新增4项回归，模块边界专项共32项。仅边界检查器、其直接测试、责任说明和矩阵变化；既有产品实现、原回归测试、冻结47项、23条历史依赖例外与导入准入规则均不变。新证据使用`evidence/module_boundaries/ownership-final*`独立名称保留；原346、201及137项结果仍引用未改变的受测产品源码，不伪装成本轮重新执行。


## 2026-11-05 MODULAR-01/02/03最小修复开发验证

本段是新一轮开发验证，旧段落保留其原日期与范围，不沿用旧结果代替本轮执行。

- 相同最终测试文件SHA下：修复前原32项通过，新增35项中27项失败、8项通过（subTest失败事件32次，0error）；修复后原32＋新增35共67项通过，0失败、0跳过。原32个测试函数AST逐项不变，未改既有断言。
- 本轮重新执行201项前端、346项Report、137条真实HTTP浏览器断言，全部通过；typecheck/build/生成类型一致性及41项共享合同/OpenAPI也通过。受测产品完整SHA与最终候选交叉核对。
- 22次Report读取重算、SQL写入、文件写入、外来Snapshot读取均0，持久化摘要不变；239个Report回归计数用例重算/SQL写/外来Snapshot读均0。正常情景8次Runtime执行单列。
- Python namespace、显式package、仓库helper链及.mts/.cts/.jsx现均有拒绝反例和合法对照；燃气未知动态导入及__import__不确定相对level亦拒绝。6组真实CLI输入前后对照保留。
- 新检查器补出7条已有合法集成依赖，当前805边；M0/23条例外/冻结清单不改。没有调整Agent、算法、Report业务逻辑或前端功能。
- 本轮没有复跑旧419项扩展范围或那7项已由独立复审证实的基线失败，不能将它们计入本轮通过数；关联业务及旧测试SHA未变。原7项仍需独立授权修复可信身份fixture/旧工作台接线。
- 新测试、JUnit、浏览器计数、前后SHA、增量Diff和完整PR Diff放在179 experiments/modular_collab_fix_20261105/evidence及Windows接收记录。等待独立复审，不合并main、不部署，不解除原有候选门禁。


## 2026-11-05 FIX2：fromlist与父包初始化最小修复

本段为第二轮修复的新执行结果，不复用上轮独立复审或开发自测。

- 原67个测试方法AST逐项不变，新增27项。相同最终测试文件下，修复前原67通过、新27中23方法失败/4通过（50次失败事件，含39次subTest失败；0error/skip）；修复后94/94通过。
- 最终checker SHA为4d514ec09d6235b921cf9240909e36c66f263abd551503ce485c28615ae6d69c；测试SHA为bb4dee7dff8377d25cf3d6d6c5a10987109da3e3e5b00afd249a0b15aba28654。四组before/after分别留JUnit、命令和起止SHA。
- 最终检查器重新执行上一轮45组CLI输入，45组全部符合预期，包括2组完整HEAD探针。另25组实现只读核查均符合预期；探针和长期测试存在覆盖重叠，不相加宣称全量通过数。
- 本轮新跑201项前端（含12项真实React Chromium）、346项Report、137条真实FastAPI浏览器断言及41项合同/OpenAPI，全部通过；typecheck/build/contracts:check通过。
- 22次Report GET覆盖200/401/403/404/409，重算、SQL写、文件写、外来Snapshot读均0，22份持久化摘要不变。239个后端Report计数用例及额外6个身份用例计数为0；原42深层及11组双层22例逐项按本轮JUnit核对。
- 测试独立/tmp/SQLite/cache/log，真实模型调用及对外请求0；不用历史500条fixture。种子准备和正常Run的8次Runtime/2次simulate/6次compare单列，不能把整个pytest运行误称零写。测试服务已结束。
- 受测373产品文件、199 engineering文件与最终源码逐SHA复核；冻结Agent20/算法22/Report5保持不变。业务逻辑、前端功能、共享合同和部署配置未改。
- 当前图411文件/1171边/0环；新增366条父包初始化边补齐旧Python语义。23条例外和M0未变，历史许可派生父边另需初始化文件原始SHA匹配。
- 原7项身份fixture/工作台失败、旧419扩展范围本轮未复跑，不能计为通过；已有静态分析的保守限制不借本轮扩大修复。等独立复审，不合并main、不部署、不解除旧候选门禁。

完整证据位于179 experiments/modular_collab_fix2_20261105/evidence及Windows接收目录。前端与后端从锁定b3f88a23的产品副本新跑，其产品SHA与最终提交一致；边界测试单独使用最终checker和新增测试。来源提交不与当前测试实现混记。


## FIX3 development validation — 2026-11-05

Scope: E7-EXTRA-01 (missing terminal in dotted static fromlist) and E7-EXTRA-02 (source ancestor initialization). Product, Agent, algorithms, Report, frontend behavior, deployment and the 23 exact historical permissions are unchanged. This is a development delivery awaiting independent review, not merge approval.

| Fresh 179 run | Result |
|---|---|
| Boundary suite | 117 passed: original 94 plus 23 appended tests; no failures/errors/skips |
| Same final tests against e7 checker | Original 94 passed; 16 of 23 new methods failed, 42 failure events, no errors/skips |
| Frontend | 201 passed, including 12 real React Chromium tests; typecheck/build/contracts:check passed |
| Report/platform | 346 passed, no failures/skips |
| Shared contracts/OpenAPI | 41 passed, no failures/skips |
| Real FastAPI + Chromium | 137 assertions passed, no failures |
| Historical permission SHA mutations | 184 rejected as required; unmodified real graph passed |
| Focused implementation probes | 31 synthetic cases matched expected behavior, including explicit cycles and legal re-exports |

The 22 Report GETs returned 200x14, 401x3, 403x1, 404x1 and 409x3. Recalculation, SQL writes, file writes and foreign-snapshot reads were zero in those read windows; all persisted digests were unchanged. The 239 backend Report counter cases and six additional identity cases remained zero at their monitored computation entries. Normal fixture preparation and Run creation are counted separately. No real model or external database was used.

Fresh product regressions used clean e7 product copies; all 373 product files (including 199 engineering files) are compared with the final FIX3 tree. All 47 frozen files retain their hashes. The current graph has 1171 ordinary edges and 98 separately recorded entry-initialization edges; authorization reachability combines them, while cycle detection uses ordinary imports. The original 94 test method ASTs are preserved.

Evidence: /workdata/WaterPipNet/01_工程项目/experiments/modular_collab_fix3_20261105/evidence. Temporary resources: /tmp/modular-fix3-cw0js9s4. Before-suite dependency preparation and the intermediate 114-test run are retained separately, not substituted for the final 117-test result. The prior seven legacy identity/workbench failures are not rerun or counted as passes; provided-500 samples, PostgreSQL and real models remain outside this run. Attribute-shadowing conservative rejection remains a declared static limit.


## FIX4 development validation — 2026-11-05

D8-REEXPORT-01: only the uniquely resolved package-container edge of an explicit-member `from . import ...` inside that same initializer is omitted. The child dependencies and the historical SHA-pinned permissions remain in force. The same final tests ran both against d8 and against the candidate:

| Fresh 179 run | Result |
|---|---|
| Boundary tests | 139/139 (117 existing methods and helpers unchanged, 22 new); zero failures, errors or skips |
| Before control on d8 | Original 117 passed; of the 22 new methods, 10 failed (23 assertion events), 12 passed |
| CLI and CPython controls | All 38 prior review cases plus 4 new controls matched; 33 inert Python executions matched the expected events; 9 full-tree fixtures preserved their original files |
| Historical permissions | 184 in-memory mutations rejected; original control passed |
| Frontend and real HTTP | 201/201 frontend (including 12 React browser tests), typecheck/build/contracts:check and 137/137 FastAPI + Chromium assertions passed |
| Backend | 346/346 Report/platform and 41/41 contracts/OpenAPI passed |

The initial development candidate also suppressed the container for wildcard imports. A synthetic review counterexample exposed that change. The final condition excludes wildcards: both plain star and a wildcard with an explicit child back import retain the conservative cycle refusal. The intermediate 138-test run is not counted as the final result. No wildcard/`__all__` resolution is claimed.

Report read windows: 239 backend counter cases (recomputation, database writes and foreign snapshot reads zero) and six additional identity cases (recomputation zero). All 22 real Report GETs (200x14, 401x3, 403x1, 404x1, 409x3) had zero recomputation, SQL/file writes or foreign snapshot reads and unchanged persistent digests. Seed and normal Run activity are counted separately. The tests used isolated synthetic stores, no provided-500 sample, real model or external database; the loopback test service stopped.

The business suites ran on newly isolated d8 product copies; all 373 product files are compared byte-for-byte to the final candidate. Agent20, algorithm22 and Report5 remain pinned. The seven historical identity/workbench cases were checked statically only, not run or counted as passes. The previous independent BLOCK remains in effect pending a new independent review; this delivery does not merge, deploy or open any product gate.
