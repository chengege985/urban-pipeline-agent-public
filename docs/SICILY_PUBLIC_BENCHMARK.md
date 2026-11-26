# 西西里高压输气研究样本

中控右上角选择「西西里」。中央窗口显示 340 个节点和 350 条管线的拓扑示意。点选或搜索管段可查看端点、长度、管径以及所选小时的发布流量、流速。

- 「风险分析」可切换代表日和小时，查看作者发布的节点绝对压力。题头标注分时稳态工况。共有 19 日、456 个小时的输入，436 个小时附成对的发布结果。
- 「应急推演」选择管段后点「执行拓扑推演」。中央地图与回放联动，可以点击或播放「基准连通」「给定管段退出」「恢复基准连接」三个假设阶段。管段 36 和管段 1 分别用于核对零个与一个新增断连节点。输出使用模型节点和标准体积需求。
- 快捷示例使用默认工况。342 号退出后仍有 340 个可达节点，青色虚线标出退出管段两端的一条替代拓扑通路。254 号退出后有 24 个新增断连节点和 23 条断连支网管段。断连范围由模型的连接关系计算。
- 如需复核压力，另点「另算稳态压力」。求解有单独的等待上限，计算期间可以继续查看或播放拓扑结果。
- 样本属于高压输气研究模型，数据来自作者另一套公开算例。论文里的 3.63 公里配气案例需要向作者申请。已核验的小时节点表未提供可用于全网落图的坐标。

## 数据和启动

项目已有本机快照时，从仓库根目录开启两个 PowerShell 窗口：

```powershell
pwsh -NoProfile -File scripts/start-gas-public.ps1 -Mode Check
pwsh -NoProfile -File scripts/start-gas-public.ps1 -Mode Backend -ApiPort 18219 -WebPort 5219
```

```powershell
pwsh -NoProfile -File scripts/start-gas-public.ps1 -Mode Frontend -ApiPort 18219 -WebPort 5219
```

访问 [本机燃气中控](http://127.0.0.1:5219/#/gas)。端口可改，前后端必须用同一组值。启动脚本保留显式配置，缺省时可识别本机已验的 GasLib 文件及现有快照。

重新取数可克隆[作者仓库](https://github.com/Filippo-L-A-Munafo/GasNetSim_4Qgis)，在工作副本检出 `4c3bf375338913c6f88749bac89cc534e4a1c0f0`，然后从 `engineering` 运行：

```powershell
python scripts/import_sicily.py <作者仓库目录> outputs/research/sicily-snapshot
```

运行目录内的清单保存原提交和逐文件 SHA-256。另选目录时设置 `GAS_SICILY_DATA_ROOT`。需要独立求解时，在单独的 Python 3.11 环境安装作者[求解器分支对应提交](https://github.com/Filippo-L-A-Munafo/GasNetSim_4Qgis-venv/tree/6079ec1841f6167b22c774abb6df24a768f22541)，将 `SICILY_SOLVER_PYTHON` 指向该环境的 Python。数据、第三方求解器及本机输出放在忽略目录。

## 验证与限制

本机按官方文件恢复 GasLib 拓扑，得到 582 节点和 609 连接。新版后端也读取了 AER、NGN、Charlottesville、SGN、Schutterwald 和 Merrimack 的概览。线上来源的读权限和可达性由上游服务决定。

Windows 原 Schutterwald 环境缺失包内子模块，已按 `engineering/configs/schutterwald_solver_requirements.txt` 另建短路径求解环境，子进程显式使用 UTF-8。后端定向回归 20 项通过，包括 Schutterwald 基准与退出工况。前端 406 项通过，13 项由原配置跳过，项目构建通过。

| 复算时段 | 作者同小时发布压力的最大差 | 独立节点标准体积流量复核最大残差 |
|---|---:|---:|
| 2025-11-15 h12 | 0.199899 bar | 2.437645 Sm³/s |
| 2026-01-08 h18 | 2.066699 bar | 7.093981 Sm³/s |

输入端 75 bar 表压换算为 76.01325 bar 绝对压力。当前两组基准均未通过节点流量复核，冬季基准也未通过压力复核。阈值 0.25 bar 与 0.03 Sm³/s 是本次复现检查阈值，未用作工程限值。退出情景保留拓扑结果，各项压力和流量对比待复现完成后开放。风险窗口的作者发布结果有独立的来源说明。

收紧求解公差后，两个基准的节点流量残差仍为约 1.843 与 6.994 Sm³/s。保留作者脚本的求解参数和项目的独立结果检查。

Windows Chrome 验收脚本为 `web/scripts/sicily.browser.mjs`。截图与交互记录位于 `engineering/outputs/research/browser/sicily/`。这些验证只覆盖公开研究样本及软件行为。
