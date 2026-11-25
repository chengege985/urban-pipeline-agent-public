# M1-B-03 正式报告页真实浏览器回归

在 179 的已有受控环境执行；这是开发测试入口，不是部署脚本。需要已有 Node、Playwright Chromium、Python 虚拟环境及完成的前端 dist。

```sh
cd /workdata/WaterPipNet/02_比赛作品/web
export PATH=/opt/node-v22.18.0-linux-x64/bin:$PATH
node tests/integration/m1b03/run.mjs /tmp/m1b03-browser-UNIQUE /path/to/reviewed/dist /workdata/WaterPipNet/01_工程项目
```

第一个目录必须全新且位于授权临时目录。脚本不安装依赖，不下载浏览器，不修改被测源码或交付数据。使用系统现有 `/workdata/WaterPipNet/.venv-agent/bin/python` 和 `/root/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome`；环境不同需显式更新测试运行条件。

## 隔离与模型限制

- 复制当前后端 src 和一个统一合成 fixture 至独立临时目录，记录完整 source_manifest.json；不读取企业数据库或其他数据文件。
- 每次全新的 SQLite、输出、日志和合成快照；Python审计钩子拒绝测试目录以外文件写入及非隔离SQLite连接。后端socket.connect拒绝外发，浏览器仅允许本次127.0.0.1同源请求。
- 原有 FastAPI Host、Origin、身份与数据授权仍启用。随机 HttpOnly 测试会话只在同源网关内转换为服务端 Token，浏览器不持有Token。此网关不是生产身份方案。
- fixture通过真实 decision-runs 接口和既有 Runtime 计算，准备阶段写入单独计数；额外构造 pending/身份冲突记录只作用于测试库。
- Report阶段按请求上下文分别记录计算函数、Runtime.execute、SQL写、文件写、全部Snapshot读取与外来Snapshot读取；比较数据库行和输出/数据文件哈希。其他线程的情景计算不会被误计为Report计算。
- 退出关闭浏览器和仅本次创建的服务；不重启或修改现有服务。

## 覆盖及证据

真实Chromium加载完整构建产物，逐项覆盖合法null、unknown、partial、缺少已选时间指标、未知费用、未完成Run与身份冲突409、无认证401、跨主体403、404、刷新、路由变化旧报告清除、情景异步Run到报告入口，以及报告阶段仅GET。

`results/checks.json` 为断言结果；`responses.json` / `requests.json`为浏览器实际HTTP；PNG为真实页面截图；`fixtures.json`为后端真实合成输入结果；`server_audit.json`为最终副作用计数；`source_manifest.json`为隔离被测版本；`environment.json`记录实际运行版本。返回码0仅代表本次脚本全部断言通过，不能代替独立业务验收或生产准入。

直达报告只需 `?mode=platform#/report/{run_id}`，测试要求API仅请求Report，不预加载capabilities或dataset view。因此合法partial报告的读取不依赖总览/情景对缺字段快照的准入。对未知户数合成快照另外生成真实simulation Run，验证顶层partial。原Dataset总览准入不属于本测试修改范围。

失败启动/调试轮必须与最终成功轮分开记录；测试不得以降级权限门禁使就绪检查通过。匿名能力端点401仅表示服务已监听，不代表匿名获权。截图同时保留fullPage原图与桌面、390像素手机viewport原图，不裁剪图片。
