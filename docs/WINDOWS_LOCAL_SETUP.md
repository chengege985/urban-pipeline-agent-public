# 本地开发与测试

**当前主流程已调整为179服务器开发测试，Windows接收变更并推送GitHub。** 本页保留本地前端和核心测试的可选用法，不要求为了日常开发先安装WSL或移植fcntl。实际工作顺序见[SERVER_WORKFLOW.md](SERVER_WORKFLOW.md)。

2026-11-04迁移实测：Windows Python3.12.14、Node24.14.1、npm11.11.0；Linux Python3.12.3、Node22.18.0。使用锁文件，不复制Linux虚拟环境到Windows。

## 前端：Windows或Linux

从仓库根执行：

```powershell
cd web
npm ci
npm run dev -- --host 127.0.0.1
# 在浏览器访问终端显示地址并加 /?mode=demo
```

检查：`npm run typecheck`、`npm test`、`npm run build`。测试中的浏览器项目默认可能skip，不能把Vitest通过当作完整浏览器验收。

## Python核心：Windows可用

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r engineering/configs/agent_requirements.lock.txt
& .\.venv\Scripts\python.exe -B scripts/verify_synthetic_restore.py --backend-root engineering --scope kernel --output-dir restore-results/kernel-01
```

输出目录必须不存在，测试不写入源码。若py启动器未登记3.12，使用实际Python3.12可执行文件路径。kernel是明确子集，不替代平台测试。PG专项需独立实例，本配置下跳过。

## 完整FastAPI：目前要求Linux

**原生Windows在`src/platform/runtime.py`导入`fcntl`失败。** 该模块用于worker互斥；不得用假模块或取消锁使其表面运行。本机尚未安装WSL2。可另行准备WSL2/Linux，在其中新建Linux虚拟环境。以下命令从该环境中的仓库根运行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r engineering/configs/agent_requirements.lock.txt
npm --prefix web ci
npm --prefix web run build
.venv/bin/python -B scripts/start_synthetic_local.py \
  --backend-root engineering --web-dist web/dist --port 8000
```

打开启动输出中的一次性`login_url`，网关使用HttpOnly合成会话，页面通过同源访问实际FastAPI。仅监听127.0.0.1，不依赖179服务器，不读取旧业务数据库；每次生成新的合成SQLite及snapshot。登录URL不要提交或分享。Agent关闭、模型为Mock，Report是否开放由所选前端分支及已有后端合同决定。

完整合成检查：

```bash
.venv/bin/python -B scripts/verify_synthetic_restore.py --backend-root engineering --scope unified --output-dir restore-results/unified-01
.venv/bin/python -B scripts/check_local_http.py --backend-root engineering --output-dir restore-results/http-01
```

浏览器检查需安装Playwright Chromium，在同一Linux环境调用：

```bash
cd web
npx playwright install chromium
cd ..
node scripts/check_local_browser.mjs .venv/bin/python engineering web restore-results/browser-01
```

最后一条默认检验main的Report链路。UI候选需要额外参数`<Chromium可执行文件绝对路径> ui-v3`，该分支不请求Report。WSL localhost访问取决于本机配置，不通过改公网监听来绕开网络问题。

## 已知不随首包提供的依赖

- 提供方500条样例及其派生结果、内部RAG全文没有上传。依赖它们的历史测试应报告缺项，不伪造样例或降低断言。
- 可选水力候选保留INP，另需WNTR/EPANET，尚非正式产品能力。
- PostgreSQL/pgvector、真实Qwen、真实数据和部署权限需另行配置及验收。
- 历史浏览器脚本中仍有Linux绝对路径；已保留代码，不声称所有旧入口已跨平台。
- `web/scripts/generate-platform-types.mjs`的默认源码目录是历史路径；新结构下需显式输入：

```powershell
node web/scripts/generate-platform-types.mjs engineering/04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json
```

只有需要更新类型时执行，并审查生成Diff；当前生成TS已提供。不要因为目录迁移擅自改变评分、冻结Agent或接口版本。
