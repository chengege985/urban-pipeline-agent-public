# 燃气首批试点数据交付说明

[字段交付清单](../templates/gas-pilot-02-fields.csv)沿用 PILOT-02 的 99 项字段，增加交付级别、关联键和可选的观测质量原因。清单中的示例均为合成。填写时保留源系统记录编号、原始单位和实际许可范围。

| 级别 | 提供内容 | 能完成的验证 |
| --- | --- | --- |
| A 首批必需 | 版本和文件清单、独立授权登记、节点、管段、测点、历史观测 | 数据准入、只读回放、原记录追溯与管段拓扑 |
| B 推荐 | 签批阈值与历史版次、事件及凭据、GIS 线要素、质量原因、人工核验窗口和事件 | 分别支持正式历史预警、事件证据、地图、质量成因和真值对照 |
| C 后续 CUV | 工程模型的管段参数、边界、需求、阀门历史和校核工况 | 按后续校核任务确认格式和批准范围 |

A 中的 `region_ref` 适用于真实授权包，`sheet` 仅在 Excel 源文件中必填。批准阈值是正式告警回放的前提。缺少批准阈值时，首批数据仍可验收读数、时间和关联关系，正式告警显示无适用阈值。无 GIS 许可或线要素时可查看试点管段的两端节点。缺少原始质量原因时界面保留质量码。

## 准备交付

1. 解包后在 `data/` 内填写 `nodes.csv`、`pipes.csv`、`sensors.csv` 和 `observations.csv`。保留列名，或在 `manifest.json` 的 `columns` 里逐列映射运营方的实际表头。普通 CSV 使用 UTF-8。XLSX 使用明确的工作表，ID 和含时区时间按文本导出。
2. 检查关联键 `nodes.node_id → pipes.from_node_id/to_node_id → sensors.pipe_id → observations.sensor_id`。每表同时提供稳定的 `source_record_id`。每个测点在同一 UTC 时点最多一条记录。时间填写带时区的 ISO 8601，缺测留空并标为 `missing`。压力与流量保留测点、读数和阈值所用的同一单位，并附表压或绝压、流量工况、采样周期和质量码说明。
3. 将数据集 ID、版本、来源系统、来源批次和摘要写入最终清单。真实来件使用经批准的 ID 和 `real_authorized` 模式。授权维护人在受控环境登记 A 类所列审批字段，绑定最终清单字节的 SHA-256、实际交付目录、区域、管段、读者、用途和有效期。文件、清单和审批登记走约定的受控文件通道。
4. 校验人员先预览，再由当前版本的授权导入人执行入库。发现结构错误时按质量回执修正源文件，分配新版本并核对签批摘要。中控只读展示纳入该主体授权范围的质量提示和拒收回执。格式无效的清单和未获许可的文件由受控 CLI 拒绝。

## 操作命令

在 129 主工作树的 `engineering` 下运行。以下命令使用 Git 外的独立合成验收路径。模板可重新生成，选 `full` 会同时生成 B 类表头，C 类待专业模型阶段确认。

```bash
export PYTHONPATH=.
python -B -m src.gas_application.pilot_cli template /nix/pilot03-minimum --dataset syn-gas-pilot-minimum --version intake-v1 --mode synthetic --profile minimum
python -B -m src.gas_application.pilot_cli preview /nix/pilot03-filled --store /nix/pilot03-store
python -B -m src.gas_application.pilot_cli ingest /nix/pilot03-filled --store /nix/pilot03-store --actor qa-ingest
```

`/nix/pilot03-filled` 是已按字段合同填写和重算摘要的合成数据目录，`/nix/pilot03-store` 是提前建立的 0700 目录。每份文件修改后必须重新计算字节摘要。可在清单目录运行下列脚本，真实清单的授权摘要以此脚本完成后的输出为准。

```python
from hashlib import sha256
import json
from pathlib import Path

path = Path("manifest.json")
manifest = json.loads(path.read_text(encoding="utf-8-sig"))
for entry in manifest["files"].values():
    filename = entry["filename"]
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("清单必须使用包内文件名")
    entry["sha256"] = sha256(Path(filename).read_bytes()).hexdigest()
path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(sha256(path.read_bytes()).hexdigest())
```

## 验收口径

历史回放显示当前合成或授权标识、有效期、阈值切换和原始质量标记。Qwen 仅从当前数据集、观测及回放窗口选择已授权证据。阈值换版统计按各自实际生效时段计算。正式工程水力、泄漏风险和复气结论由后续专业模型校核及授权决定。

版本隔离、重新导入和回滚见 [PILOT-02 说明](GAS-PILOT-02.md)。
