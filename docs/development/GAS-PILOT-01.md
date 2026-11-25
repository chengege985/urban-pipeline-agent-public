# 燃气试点数据字典与历史回放

当前以独立合成包验收。合成包、试点库和 GAS-M0 各用自己的数据集标识。中控通过 `#/gas` 的「合成监测预警 → 试点历史回放」切换数据范围，分别显示合成和获授权来源。

真实数据的现行准入、操作审计及阈值合同见 [PILOT-02](GAS-PILOT-02.md)。

## 文件与字段

使用 `manifest.json`。清单包含 `schema_version=gas-pilot/1`、`dataset_id`、`version`、`data_mode` 和 `files`。每个 `files` 条目登记 `filename`、`source_id`、来源 `version`、原文件字节 `sha256` 和 `columns`。按「标准字段 → 原生表头或要素属性」逐项填映射。XLSX 另填 `sheet`。所有表有 `source_record_id`，表内唯一。业务 ID 使用 ASCII 字母、数字、下划线、点或连字符，长度至多 128。合成数据集和记录主键使用 `syn-gas-pilot-` 前缀。

| 表 | 必须映射的业务列 | 键与校验 |
| --- | --- | --- |
| `nodes` | `node_id` | 节点主键。节点表为管段拓扑关系提供独立引用。 |
| `pipes` | `pipe_id, from_node_id, to_node_id, material, diameter_mm, pressure_class` | 管段主键，起终点关联不同节点。材质 `steel/pe/ductile_iron/other/unknown`。管径为正数，单位 mm。压力等级使用运营方字典代码。 |
| `gis` | `geometry_id, pipe_id, crs, geometry, scope_ref, valid_from, valid_until` | 几何主键，关联管段。日期按 UTC 日且两端含。只接受有限且在声明坐标域内的二维 LineString，至少两个顶点，相邻顶点须不同。 |
| `sensors` | `sensor_id, pipe_id, metric, unit, device_status` | 测点主键，关联管段。指标 `pressure/flow`，设备状态 `active/inactive/maintenance/unknown`。压力 `Pa/kPa/MPa`，流量 `m3/h/Nm3/h`。 |
| `observations` | `observation_id, sensor_id, observed_at, value, unit, quality` | 观测主键，关联测点。同一测点与 UTC 时点唯一，单位与测点相同。质量 `valid/suspect/invalid/missing`。只有 `missing` 的数值列为空。 |
| `events` | `event_id, recorded_at, observation_ids, pipe_ids, status, evidence_ref` | 事件主键。两组关联使用 JSON ID 数组。每条观测须属于所列管段，且观测时点不晚于事件记录。状态 `open/acknowledged/resolved/unknown`，保留来源凭据引用。 |
| `thresholds` | `threshold_id, sensor_id, version, unit, lower, upper, cadence_seconds, sustained_samples, missing_samples, operator_id, approval_ref, approved_at, effective_from, effective_until` | 阈值主键，关联测点，同测点版本及生效区间不可重复或重叠。上下限至少一个可用，至少连续 2 次有效越限才形成持续越限。单位与测点相同。批准时点不晚于生效时点。 |

所有时间戳使用带时区的 ISO 8601 精确到秒，例如 `2026-11-18T01:00:00Z`。CSV 为 UTF-8。XLSX 的 ID 和带时区时间用文本单元格，数据范围内的公式或单元格错误会拒收。GeoJSON 使用 WGS84 FeatureCollection，业务列在 `properties`，`geometry` 映射要素几何。表格中的几何列使用 JSON；接收 EPSG:4326 经度/纬度或 EPSG:3857 东向/北向米，授权详情统一输出 EPSG:4326。其他坐标系须先由运营方完成约定转换。观测和阈值保留原单位，工况流量与标准状态流量各自验收。

## 模板、预览与入库

在 Linux 主工作树的 `engineering` 中执行。试点库在获准的 Git 外目录，目录权限 0700，库和授权登记归运行用户所有且权限 0600。

```bash
export PYTHONPATH=.
python -B -m src.gas_application.pilot_cli template /nix/pilot-template --dataset syn-gas-pilot-template --version v1 --mode synthetic
python -B -m src.gas_application.pilot_cli preview tests/fixtures/gas_pilot_synthetic
install -d -m 700 /nix/wpn-pilot-verification
python -B -m src.gas_application.pilot_cli ingest tests/fixtures/gas_pilot_synthetic --store /nix/wpn-pilot-verification
```

模板生成当前合同的标准表头和清单。填表或修改源文件后重算文件摘要再预览。预览回执列出原生列、映射、可用关系覆盖、结构错误和质量缺项。原记录号、表或工作表、行号、来源版本、文件 SHA 均随规范化记录保留。结构错误拒收整版并可在隔离库登记拒收回执。存在可疑/缺失读数或选填表缺项的有效包可标记为 `partial`。同版本同摘要可重复提交，内容变化须采用新版本。浏览接口只读，导入是离线操作。

真实包的受控授权登记、专用阈值表和版本维护流程见 [PILOT-02 准入与历史阈值验证](GAS-PILOT-02.md)。

## 回放、问答与样例验收

API 从 `GAS_PILOT_STORE` 读取试点库。接口检查服务端主体的内部身份、数据模式及显式数据集 ACL。真实数据另核对有效的包级授权。私有合成预览通过 `GAS_PILOT_PREVIEW_DATASET` 指定单一合成试点，沿用现有 Basic 登录和回环 FastAPI。正式主体的 Web 转发应使用获准的同源身份服务。

默认回放所选测点最后一小时，也可同时指定起止时点。每条规则按 `effective_from` 起算采样时点，生效区间左闭右开。每段从窗口初态开始判定，观测跨度之外保留空结果。正常、单点越限、持续越限、可用观测缺失及恢复均引用当时规则版本和原始行。可疑、无效、缺失及偏离采样时点的值保留其质量身份。无适用阈值时可查看读数，判定显示无策略。规则版本切换分段计算。

问答从选中的观测开始，返回该数据集版本内的原始观测、测点、管段、适用阈值和关联事件证据。Qwen 仅选择服务端提供的证据句。模型不可用时使用标记明确的规则结果。正式工程水力、风险和复气决策保持不可用。

合成压力样例在 01:03 UTC 持续越限、01:04 恢复、01:06 判断数据缺失、01:07 数据恢复。01:08 开始使用 v2 样例阈值，可从 01:03 的 7.2 kPa 观测下钻管段及源记录。流量样例使用独立测点和样例阈值。

## 待运营方提供或确认

- 管段与节点的稳定 ID、上下游或起终点关系、材质代码、管径定义、压力等级字典，以及历史版本变更。
- GIS 原始线要素及资产键、CRS 与轴序、坐标转换依据、图层版本、适用区域和有效日期。
- 测点与资产绑定及生效时间、设备状态字典、表压或绝压口径、流量工况、量测单位、采样周期、时区和时钟偏移、质量码与缺测约定。
- 历史观测原始行及设备、时间字段，事件主键、状态与时点、实际关联观测和资产、来源证据。请注明覆盖区间与已知缺口。
- 运营方正式批准阈值、适用资产或测点、上下限与单位、持续和缺失策略、版本、生效与失效时间、批准人和批准记录。
- 权属方的包级授权、使用主体、允许用途与区域、保留期限及撤销方式。GIS 展示、历史回放和本机 Qwen 查询各核对用途。当前尚无可用的真实授权包和上述业务确认。
