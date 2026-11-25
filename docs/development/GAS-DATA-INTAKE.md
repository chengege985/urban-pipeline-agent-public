# 燃气数据接收与核对

授权试点的管段拓扑、历史观测、阈值和隔离库见[燃气试点数据字典与回放](GAS-PILOT-01.md)。

当前入口用于离线核对数据包的版本、来源、字段和关联。命令输出限于错误码、计数与摘要。业务处置、GIS 地图发布、公开分享和模型检索分别受专项准入约束。

```bash
cd engineering
PYTHONPATH=. python -m src.gas_application.data_package tests/fixtures/gas_data_intake
```

默认示例是独立合成包，与 GAS-M0 使用不同数据集和资产 ID。三个资产分别覆盖资料齐全、缺监测、缺坐标三种情况。点值只用于格式和边界验证，表示合成测试值。

## 标准化文件

包使用 `manifest.json` 和固定名称的 UTF-8 CSV。台账是关联基准。清单允许缺表并报告覆盖缺口，已登记的文件必须存在且字节摘要一致。清单登记 `schema_version`、`dataset_id`、`version`、`data_mode`，以及每份来源的 `source_id`、`version` 和原文件字节 `sha256`。校验以文件实际字节为准。

| 文件 | 字段与语义 |
| --- | --- |
| `assets.csv` | `source_record_id,asset_id,asset_type,name,district_id`。资产 ID 用于包内关联。 |
| `coordinates.csv` | `source_record_id,asset_id,crs,longitude,latitude`。只接收 `wgs84` 或 `gcj02` 坐标点，列顺序为经度、纬度。 |
| `monitoring.csv` | `source_record_id,sensor_id,asset_id,observed_at,metric,value,unit,quality`。观测时点采用带时区的 ISO 8601，归一为 UTC 后判重。指标名为 `pressure` 或 `flow`。压力允许 `Pa/kPa/MPa`，流量允许 `m3/h/Nm3/h`。质量值为 `valid/suspect/invalid`。 |

坐标和监测通过显式资产 ID 关联。缺坐标或缺监测时保留资产及缺项计数。无效记录、重复组、悬空资产引用和同时指向多个资产的传感器进入诊断。疑似读数保留原质量标记。每条可用记录保留原记录号、CSV 记录序号、来源版本和文件 SHA-256。单位和 CRS 原样保留，支线连通和标准工况换算待验收后接入。

报告只包含来源标识和摘要、已接收和可用条数、覆盖计数及结构化问题。`ready` 表示本合同下的完整覆盖，`partial` 表示有可用台账且仍有缺项或质量问题，`unavailable` 表示没有可用台账。CLI 的退出码依次为 0、1、1，文件结构、授权或散列拒绝为 2。本页样例有缺项和一条疑似观测，预期返回 `partial`。这个格式承载标准化点和观测时点，原生表、线几何、设备连接和处置记录需要另行确认映射。

## 授权与待核查事项

- 检查范围内已有目录和字段需求。台账、GIS、监测原始交付和实际逐数据集授权仍待核实。燃气权属或运营单位的数据授权负责人需确认来源、可用主体、用途、时空和保留范围，平台侧据此登记数据集权限。当前资料只给出持有人及协调职责，实际签批主体与凭证待核对。 [既有字段与持有人对照](../../engineering/experiments/gas_m0/docs/gas_data_requirements.md)
- CLI 使用明确标记的合成包。未来授权调用方可使用 `read_package(path, approval=...)`，授权来自包外可信登记，绑定 Git 工作树外的根目录、数据集、清单 SHA 和授权记录。调用方需先核对主体、用途和具体数据集权限。真实源文件只在批准之后读取。当前服务端路由和燃气演示沿用合成源。 [平台授权](../../engineering/src/platform/authz.py) · [燃气演示模式](../../engineering/src/gas_application/standalone.py)
- 台账待确认原生列名、稳定原 ID、版本、管段端点、阀门及调压作用、用户接入依据。GIS 待确认要素到资产的明确映射、几何类型、CRS 与轴序、图层版本。监测待确认设备绑定、原指标和单位、观测时点及其含义、采样频率和质量字典。现有目录里的设备条目仅登记设备，监测读数及权属方收包凭证待单独核对。 [目录字段陷阱和专业条件](../../engineering/experiments/gas_m0/docs/gas_data_requirements.md)
