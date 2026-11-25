# 2026-11-07 供水 Fixture 来源复核

- 服务器元数据：`/workdata/WaterPipNet/01_工程项目/experiments/pipe_health/pipe_health_20261028_150251_2c9bbd/metadata.json`
- 元数据 SHA-256：`8f23d0c3dfd772f169a1128441d8b33547a2683583f32744d9b8f6b480010b59`
- 元数据声明的结果 SHA-256：`2131ec61873876d3e1395e2d4819bc0b09ec63336a0eb39daabe2a2d3485385d`
- Windows 现有结果：`D:\重通院管网项目\outputs\pipe_health\pipe_health_20261028_150251_2c9bbd\V3.0健康评价结果.xlsx`
- Windows 结果复算 SHA-256：`2131ec61873876d3e1395e2d4819bc0b09ec63336a0eb39daabe2a2d3485385d`
- 元数据声明的输入 SHA：静态 `4d430b1e2815f767839b3cdc71eb34c8d85a963b5406626f828461d04e5e4f7c`，动态 `dc966f48c322349b9048bd8ddf2da079031a3626fc19ce5a3fb459b7579e4a0f`；Windows 端复算一致。
- 服务器候选分支原本缺少该 Excel 和分支内路径映射；本轮只将结果文件临时放到 `/tmp`，以符号链接映射到测试路径，复跑后已删除临时文件和符号链接。
- 结果文件未加入 Git、未复制到正式工程树、未修改数据库；供水原始资料仍由项目资产目录按来源和 SHA 管理。

复跑结果：5 项原阻断反例 `5 passed`；完整平台/认证/合同/工作台相关回归 `81 passed, 1 warning`。
