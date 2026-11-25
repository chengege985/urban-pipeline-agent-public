# 协作仓库导入基线

本仓库从2026-11-04已核验迁移快照导入；原Git bundle、发布产物、审计记录和未提交变更备份继续保留在离线交付中。新仓库没有改写原仓库历史，没有合并原先未合并的前端分支。

| 分支 | 来源 | 状态 |
|---|---|---|
| main | 原site-v0.3-g01：4981aab33dc60559eb8232b1f34bf8e926a72cdb及已保全工作区变更 | 含统一PlatformProvider、M1-B-03 Report、P2离线修复；P2-FIX待独立复审 |
| ui-enterprise-01 | fb9a8c4e3990fdd5ec5ccc234a625e3a4ee40fd9 | 动态管网及V3-FIX1；待独立复审；Report门禁保持原状态 |
| 后端 | 原工程无独立Git仓库，按逐文件SHA导入两分支 | 不改变原0.1.1、Agent冻结文件、拓扑/决策/时间线及Report语义 |

必要兼容server保留；没有继续拆成两个产品。UI与main集成需未来单独审查，不能按目录覆盖。

迁移实测：Linux核心515 passed/6 skipped，真实HTTP10/10；Linux主前端真实浏览器10/10、UI Platform8/8。Windows kernel179 passed/6 skipped；main171 passed/12 skipped，UI190 passed/14 skipped，双方typecheck/build通过；UI浏览器97/97与67/67。Windows完整后端因fcntl产生6个collection errors，不宣称Windows平台PASS。这些是迁移开发验证，不能替代候选独立验收。

`SOURCE_MANIFEST.json`列出当前分支导入源文件的SHA。GitHub的新提交身份用于协作，不等于原验收SHA。源文件的算法与冻结身份应按清单核对；新增README、协作说明和Git配置不属于业务公式修改。
