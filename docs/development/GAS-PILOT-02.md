# 燃气试点准入与历史阈值验证

[运营方字段级资料清单](../templates/gas-pilot-02-fields.csv)涵盖离线数据包、独立授权登记、阈值与人工真值。每个字段列出建议提供岗位、来源系统、格式、质量和缺失影响。样例数字和 ID 均为合成。文件格式与回放入口沿用 [PILOT-01 数据字典](GAS-PILOT-01.md)。

## 真实数据准入与版本操作

- 权属方审批单绑定许可范围、区域、管段、历史时段、读主体、操作主体、用途、授权期限、数据集版本、源目录和最终清单 SHA-256。授权维护人独立登记，每次读取均核对内部身份、数据模式、数据集 ACL 和包级授权。
- 名单、期限与用途变化在下一次请求生效。区域、资产与历史范围变更后须重新校验准入。GAS-M0、合成试点与获授权数据分别隔离，真实试点库与原件放在获准环境的 Git 外目录。
- 来件验证原件摘要、字段映射、主外键、GIS 坐标域和范围、时区、可用质量码及单位。观测归一为 UTC，拒收同测点重复时点，标记源行逆序并按时点回放。结构错误拒收整版。可继续使用的缺项列入质量回执。
- 每次成功导入保存数据集版本、源清单摘要、导入人、执行账户 UID 和 UTC 时间。隔离后该版从列表移除，读请求收到 409。回滚切换推荐版本，已准入版本与审计记录保留。纠正内容后使用新版本，同一摘要的重新导入再次核验当前许可。

在 129 主工作树的 `engineering` 中运行。下列示例使用独立合成验收目录。

```bash
export PYTHONPATH=.
python -B -m src.gas_application.pilot_cli template /nix/pilot02-template --dataset syn-gas-pilot-template --version v2 --mode synthetic
python -B tests/pilot02_fixture.py /nix/pilot02-synthetic
install -d -m 700 /nix/pilot02-check-store
python -B -m src.gas_application.pilot_cli preview /nix/pilot02-synthetic
python -B -m src.gas_application.pilot_cli ingest tests/fixtures/gas_pilot_synthetic --store /nix/pilot02-check-store --actor qa-ingest
python -B -m src.gas_application.pilot_cli ingest /nix/pilot02-synthetic --store /nix/pilot02-check-store --actor qa-ingest
python -B -m src.gas_application.pilot_cli quarantine --store /nix/pilot02-check-store --dataset syn-gas-pilot-history-01 --version demo-v2 --actor qa-custodian --reason synthetic-check
python -B -m src.gas_application.pilot_cli rollback --store /nix/pilot02-check-store --dataset syn-gas-pilot-history-01 --version demo-v1 --actor qa-custodian --reason synthetic-check
python -B -m src.gas_application.pilot_cli reimport /nix/pilot02-synthetic --store /nix/pilot02-check-store --actor qa-ingest
```

真实环境由授权管理员维护 `approvals.json`。审批引用、批准人标识与阈值来自运营方签批台账。导入、回滚和隔离从受控系统账户执行，隔离允许处理已撤销授权的版本。生产接入前须确认实名账号与系统账户映射、审计留存及撤销方式。若真实阈值或审批资料未到齐，先登记缺项并只读查看可用历史。

## 阈值、真值和中控

`threshold_rules` 每行对应一个上下限条款，绑定测点、指标、管段及原单位。仅 `approved` 且处于左闭右开生效区间的条款参与回放。同测点同版本的上下限共用时段及采样规则，不同批准版本的时段不得重叠。旧合成包可继续使用样例 `thresholds`。

可疑、无效及缺失的观测保留其质量身份。有效读数按阈值版本及批准采样周期计数，形成持续越限、缺失及恢复状态。换版前后连续、同向、完整采样的重复告警保留换版来源，按一次告警计数。

人工真值按测点和区间来自 `truth_windows`、`truth_events`。完整覆盖已复核窗口、区间内批准阈值连续适用且许可真值评价时，计算未匹配告警、未检测事件、检测延迟及恢复延迟。匹配核验事件开始至确认恢复区间内的首条同向告警，并在后续事件或回放结束前查找匹配的读数回归。数据恢复与数值回归分别记录。窗口截断、部分核验或无真值时仅展示规则一致性检查。

在 `#/gas` 打开「合成监测预警 → 试点历史回放」，选择数据集和版本。从异常状态或观测表打开观测，再查看管段、事件和试点地图。地图绘制授权线几何。当前时点无可展示的授权几何时提示 GIS 未就绪。试点问答绑定数据集版本、清单摘要、观测与当时阈值版本，从观测、阈值版次和来源按钮打开原始证据，切换观测或版本时清除上下文。当前试点入口提供只读历史证据与阈值回放，专业分析继续按既有验证范围提供。

## 接入前请运营方确认

- 交付清单对应的实际源字段和映射，附材质与压力等级字典、设备质量码映射、测点时钟及采样口径、已知缺口。注明表压或绝压、工况或标准状态流量及其基准。
- 列明历次测点换绑、资产与图层变更及坐标转换依据。按历史适用版本交付，附审批区域、资产集合及 GIS 展示许可。
- 提供阈值签批与发布历史、人工核验区间、事件及恢复记录。确认批准角色、允许用途、本机 Qwen 访问、存放环境、保存期限和撤销流程。
- 当前使用合成数据验收。真实准入和业务验收需要运营方提供并核验上述材料。
