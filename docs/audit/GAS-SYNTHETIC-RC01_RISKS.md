# GAS-SYNTHETIC-RC01 风险与发布门禁

## 已关闭

- 联合提交保留基线、442 后端安全修复和 053f088 前端合同收敛的 Git 血缘，无复制整树或重写历史。
- 受控模式无认证配置不会启动；匿名例外仅限显式 loopback/local synthetic 配置。
- X-Water-Client 不提供认证升级；Token 和主体 ACL 由服务端解析。
- Fixture 缺失/损坏返回 503，未知案例 404，越权 403，未认证 401。
- Gas DTO、OpenAPI、生成 TS 与真实响应结构一致；候选评分和物理状态不被提升。
- Agent20、算法22、Report5 冻结 SHA 未变。

## 未关闭/必须在发布前确认

1. 本机 Chromium 无法访问本地端口，live React→FastAPI 浏览器联调未验证。需在允许 loopback 的隔离浏览器环境补跑。
2. 前端 FetchTransport 不在浏览器包内携带服务端 Token；受控部署需同源代理、服务端会话或明确的本地匿名例外，不能把 Token 写入前端。
3. 合成 fixture 不是生产数据，`partial`、`unknown`、`candidate`、`not_business_validated` 必须原样呈现。
4. 真实燃气数据、真实模型、设备控制和公网监听仍禁止。
5. 供水五项测试只有在受控临时挂载原有授权样本时可运行；样本不应提交到 Git。

## 裁决

- 隔离合成开发/测试：PASS。
- 受控内网合成候选：PASS WITH CONDITIONS。
- 真实业务数据、真实设备或公网：BLOCK。
- GitHub main：BLOCK，等待独立 HY4 复验和 live 浏览器证据。