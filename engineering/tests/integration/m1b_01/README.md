# M1-B-01 隔离合成浏览器 HTTP 回归

这是长期联调回归入口，不是产品页面或生产认证实现。使用实际 React、Chromium、现有 `PlatformProvider` / `FetchTransport` 和 FastAPI，通过 SSH 回环隧道调用合成数据计算。不会修改冻结 Agent、正式前端或部署。

## 文件

| 文件 | 用途 |
| --- | --- |
| `server.py` | 从隔离源码副本启动 FastAPI；新建测试 SQLite；仅放行本阶段接口；将内存测试会话解析为服务端身份；记录真实计算次数。 |
| `ui.tsx` | 测试专用 React 入口，导入未修改的 Provider，显示实际 HTTP 结果；Report 按钮禁用。 |
| `run.mjs` | Windows 控制器：构建测试入口、启动远端隔离实例及隧道、点击浏览器、执行19项检查、保存证据并停止实例。 |

## 运行条件及准备

本次使用 Windows 控制器、179 Linux Python 环境。需要已配置的 SSH 公钥认证，不在文件中填写凭据。控制器项目根目录需有 `frontend/node_modules` 中的 `esbuild`、`react`、`react-dom`、`playwright`，以及已安装的 Chromium。实际版本见本次证据；没有依赖时先按项目锁文件准备环境，不把安装过程混入受测运行。

1. 在179的工程根目录核对本次证据中的70个后端源码 SHA、20个 Agent 冻结 SHA，以及前端123个文件 SHA。版本不同须作为新基线记录。
2. 用 `Path('/tmp') / ('codex-m1b-' + uuid.uuid4().hex)` 生成隔离路径，执行 `mkdir(mode=0o700)` 创建；目录名应匹配 `/tmp/codex-m1b-[a-z0-9]+`。将工程 `src/`、`configs/`、`data/synthetic/` 复制到其 `engine/` 下，排除 `__pycache__`、`.env*`、数据库和非合成数据。创建 `static/`，将本目录的 `server.py` 放到临时目录根部。
3. 在 Windows 工作区创建独立临时目录，例如 `.tmp_m1b`。将受测前端源文件副本放在 `.tmp_m1b/frontend_copy/web/`。本次已保存的前端副本位于179工程 `experiments/snapshots/m1b_01_frontend_pre_20261103/working_frontend.tar.gz`；解压后应存在 `web/src/providers/platformProvider.ts` 及其依赖。不要用旧 RC 源码替代未提交 Provider。
4. 从 Windows 项目根目录运行（替换远端临时目录）：

```powershell
$env:M1B_SSH = '172.30.60.179'
# 可选：$env:M1B_BROWSER = '已安装的chrome.exe绝对路径'
node tests/integration/m1b_01/run.mjs .tmp_m1b /tmp/codex-m1b-实际目录后缀
```

控制器只通过 stdin 传递随机测试会话；平台 Token 在远端内存生成。页面通过 HttpOnly、SameSite=Strict 测试 Cookie 访问同源接口，不接收平台 Token。远端监听 `127.0.0.1` 临时端口，Windows 隧道同样仅监听回环地址。Host、Origin 原样进入平台检查；没有关闭这些检查。

## 结果与复核

`results/results.json` 保存19项检查、实际响应及浏览器请求路径；`backend_audit.json` 是运行中审计；远端 `server_audit.json` 是停机后完整记录；另有浏览器环境与截图。轮询次数受计算耗时影响，不应以固定 HTTP 总数断言通过。

验收重点是评分 Hs=78.5 / Rs=1.86、输出 Snapshot 身份、200户及医院等3个受影响服务、组合方案3700元、缺资源时数据不足、6小时图通路恢复与9小时任务完成分别返回，以及401/403授权反例。

运行结束后复核临时服务器及隧道已经退出，归档必要结果，再删除本次临时目录。保留本目录的长期回归文件。不要把测试 Cookie 网关作为生产认证服务发布。

## 能力边界

- `PlatformProvider.evaluate()` 当前读取已有评分；新评分 Run 使用同一个真实 `FetchTransport` 单独提交及轮询，尚未包装为正式页面 Provider 方法。
- 新评分输出 Snapshot 不含运行网络。测试推演使用原合成网络 Snapshot，不自动把两个快照拼成一个状态。
- 正式网站 `useDataSource.ts` 的 live 分支仍使用旧 `ApiProvider`。此回归通过不代表正式页面已切换。
- 方案 ID 在测试入口明确给定；正式页面的措施目录和选择流程仍需接线。
- 恢复时间表示图通路恢复，不能解释为流量、压力或水质达标。
- Report、Agent、反馈、真实模型、真实数据及公网接口未纳入本回归。Report 接入须等待 RPT01 独立 PASS。
