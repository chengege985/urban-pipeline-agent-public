# 燃气数据标准化候选合同

登记格式为 `gas-data-m2/1`。数据集登记保留版本、原件指纹、来源、数据身份、地理适用范围、坐标口径、质量和许可状态。多文件原件的 `sha256` 为按路径排序的 `sources` JSON UTF-8 序列化指纹，序列化采用键排序、两个空格缩进和一个末尾 LF。单文件使用原件指纹。外部原件未取得时，版本或 SHA 可以为 `null`。登记和生成产物的哈希随文件一起核对。

## 复核和派生结构

`engineering/scripts/gas_data_m2_prepare.py` 从 2026-11-20 隔离目录和当时的登记附件生成另一套离线数据包。脚本需 Python 3.10+、lxml，以及第一阶段缓存并经 SHA-256 校验的 xlrd wheel。输入目录、目录附件和新输出路径经命令行传入。

| 路径 | 用途 |
| --- | --- |
| `raw/` | SHA 复核后逐字节保存的原件，写成只读副本 |
| `normalized/` | 源字段映射、研究或统计投影及 `registry.json` |
| `quarantine/` | 异常记录对应的原文件指纹、CSV 行、GeoJSON 索引与原因 |
| `fixtures/` | 合成测试输入，保留原始记录 ID、时点、质量码 |
| `receipts/` | 来源对照、校验结果和全量 SHA-256 清单 |

所有目录均置于 Git 工作树外。129 私有候选服务只读取 `normalized/registry.json` 的受审查摘要，原始及隔离数据保留在审计目录。供需工况检查按原单位和方向配平。压力缺项、表压与源网络压力口径保留为独立字段。缺项为 `null`。GIS 猜测坐标、健康评价、运行风险和物理预测保持 `unavailable`。

## API 和只读解释

FastAPI 前缀为 `/api/v1/gas/engineering/data-m2`。受控本机环境设置 `GAS_DATA_M2_REGISTRY_FILE` 与 `GAS_DATA_M2_REGISTRY_SHA256` 加载候选登记。API 复用认证和 GAS-M0 ACL，并要求内部身份。

- `GET /datasets` 列出适用范围和许可状态。
- `GET /datasets/{dataset_id}` 返回每条登记的质量计数、异常、来源、转换和字段映射。
- `GET /datasets/{dataset_id}/records/{kind}` 查询 K-001 合成资产、事件、拓扑或观测。请求带登记 SHA 与版本，资产可按管段、节点、阀门或用气组筛选。
- `POST /agent/query` 提交数据集、版本、登记 SHA、已选择的证据与问题。后端生成确定性事实和证据 ID，现有 Qwen 只按问题选择证据句。无模型时响应标明后端证据。未授权、失效版本和外部数据申请访问工程记录分别拒绝。

工程中控通过现有数据窗口访问候选登记。GasLib 与 SciGRID 使用独立研究投影。重庆年鉴保留省表和城市表层级信息，公开界面展示表级出处和字段口径。高德行政视图使用受控配置与 `DistrictSearch`。区县边界按精确 adcode 查询，构建物保留离线行政示意。两江新区 `500157` 取自民政局登记，选中区县后只展示已有授权的真实坐标。K-001 拓扑使用独立 DEMO 场景。[高德区划接口](https://lbs.amap.com/api/javascript-api-v2/guide/services/district-search) · [重庆区划代码变更](https://mzj.cq.gov.cn/zwgk_218/zfxxgkml/tzgg/202512/t20251205_15215632.html)
