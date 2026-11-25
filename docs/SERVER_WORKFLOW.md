# 129 开发与主线同步

GitHub 私有仓库 `catiseyeqaq/urban-pipeline-agent` 的 `main` 是代码基线。燃气在 129 的 `/nix/wpn-mainline-unify-20261118` 开发及验证。Windows 主工作树位于工作区的 `02_赛题3_水脉守护/工作树/urban-pipeline-agent`，用于审阅、GitHub 提交及版本核对。

1. 开始前读取远端 `main`。129 无法直接访问 GitHub 时，由 Windows 接收远端提交，以同历史完整或增量 bundle 导入 129，核对提交和 Tree 后快进。遇到分叉先核实来源和改动。
2. 从该版本建短期 `feature/*`。按 [模块所有权](architecture/MODULE_OWNERSHIP.md) 落实改动，用实际账户签署提交。供水代码及冻结清单保留。
3. 在 129 按功能运行有针对性的 Python、OpenAPI、TypeScript、Vitest 和 Chromium 回归。跨合同变更另跑模块边界检查。
4. 审阅候选后提交 PR。合入后快进同步两端，并核对 `git rev-parse HEAD HEAD^{tree}`。离线 bundle、业务原件、数据库、凭据、模型、依赖和运行日志分别保管。
5. 一键演示从验收后的固定提交生成 Git bundle 和预建产物，保留上一套已校验发布作为回滚点。启动和构建见 [一键演示](GAS_DEMO_QUICKSTART.md)。比赛构建与发布见 [前端入口](FRONTEND_EDITIONS.md)。

Windows 可以执行前端回归。完整后端及模型测试使用 129 Linux 和代码仓的锁定依赖。179 旧目录、历史实验与离线归档供追溯。
