# NGN The North 公开配气数据

## 已核验原件

| 数据 | 原件发布版本 | 原件 SHA-256 | 导入 |
|---|---|---|---:|
| ODP17 The North 在运配气主管 | 2026-11-04 | `7476b2f9d03000944f7d199620afa8bfcdcb39655988933ba1da9ddf751b9d28` | 113,081 |
| ODP4 Public Reported Emergencies | 2026-05-07 | `f8c004b2bdfacf68776bcde11faa4f8bb9106a90b6a7429c8fc5c3ed1623ed25` | 429,236 |
| ODP5 Unplanned Interruptions | 2026-05-07 | `b0b000d802c711b9e9f266ca57b6c5ecbf2b3fc6fd9710b272fd8924456f1692` | 88,306 |

署名 Northern Gas Networks。三份目录分别声明 CC BY 4.0。导入快照包含源文件名、链接、哈希、权属与空间/时间范围。原件及生成数据库存于上级工作区 `outputs/ngn-the-north/`。 GeoPackage 没有被修改。[ODP17 管线](https://northerngasopendataportal.co.uk/dataset/20) · [ODP4 应急呼叫](https://northerngasopendataportal.co.uk/dataset/5) · [ODP5 非计划停供](https://northerngasopendataportal.co.uk/dataset/6) · [署名指南](https://www.northerngasnetworks.co.uk/wp-content/uploads/2023/10/ODP-Terms-Conditions.pdf)

## 核验与映射

- 管线层为 `NGN_live_distribution_mains_The_North`，源 CRS 为 EPSG:27700，几何为 MultiLineString。113,081 条均具有可显示的几何，`ASSET_ID` 无空缺或重复。它是发布方匿名编号。界面记录键用快照内的 `OBJECTID`。源管径保留 `DIAMETER` 与 `DIAM_UNIT`，仅 MM 和 I 转成毫米；源字典说明管径口径可能不同。
- Windows 的应用程序控制阻止加载本轮下载的 GDAL 驱动 DLL。导入器因此按 OGC 头部合同用 Python SQLite 读取原始 GeoPackage，使用 Shapely 解码 WKB 和 PROJ 转换坐标。转换操作为 `Inverse of British National Grid + OSGB36 to WGS 84 (6) + axis order change (2D)`。输出 WGS84 仅供展示。[OGC GeoPackage 编码标准](https://www.geopackage.org/spec140/index.html)
- 历史表按各自 `Data` 页原表行定位，按日历日期统计，实际记录日期均为 2019-10-01 至 2026-03-31。原件包含邮区字段与本地时刻字段，无可核验的管段共同键或明确的 The North 区域列。两种历史原件均作为 NGN 全网发布范围展示，保留原始时间，未推算工程事件时间或位置。应急呼叫和停供分别统计，前者含响应分钟，后者原件的 Duration 以小时计。
- 资产区域总数、全区筛选命中、视野内边界框候选数量和当前页显示数分别报告。默认进入一处由数据选定的局部近景，直接绘制管线。点击「The North 区域概览」查看按 0.04° 网格统计的记录密度，点击网格可进入相应局部。视野查询使用边界框与 SQLite RTree，线几何保留原始分段形态。统计在后端完成。月度仅显示有源记录的月份。

## 运行和复查

从 NGN 三个目录下载当前核验版本，保留官方文件名。导入器有明确的版本文件名和字段约束。若运营商更新原件，先复查字段和许可，再登记新的版本并生成独立快照目录。

```powershell
python -m pip install -r engineering/configs/ngn_import_requirements.txt
python -X utf8 engineering/scripts/import_ngn.py --source-dir ..\outputs\ngn-the-north\raw --output ..\outputs\ngn-the-north\snapshot-new
$env:GAS_NGN_DATA_ROOT = (Resolve-Path ..\outputs\ngn-the-north\snapshot-new).Path
```

启动继续沿用 [公开数据中控说明](PUBLIC_GAS_DATASETS_RUNTIME.md)。后端按清单核验数据库并以只读模式打开。浏览器右上角选 NGN。接口为 `/api/v1/gas/public-data/ngn_the_north/overview` 和 `/api/v1/gas/public-data/ngn/events`。历史接口接受 `kind`、`start`、`end` 和分页参数。缺失的历史原件独立显示待数据。

第一版提供真实资产与历史资料查询。原包不含已核验的阀门端口、供需边界和用户接入，相关推演只展示输入缺项。

## 本机验收记录

- 快照 `ngn-odp17-20261104-c44feac429bc`。HTTP 查验了资产数、源 CRS、密度网格总数、编号检索、空间与属性组合筛选，以及历史月计数与整表记录数闭合。2026 年 3 月日期窗口与相应月份计数一致。
- Windows Chrome 在 1064×850 与 1366×850 实测视野缩放和平移、重置、筛选、记录详情、历史类别和月份联动、NGN → AER → GAS-M0 → NGN。未出现水平溢出或浏览器页面异常。截图存于上级工作区 `outputs/ngn-the-north/ngn-1064.png` 和 `ngn-1366.png`。
- 公开数据后端测试 9 项通过。前端 405 项通过，13 项浏览器用例在 Vitest 运行中跳过，以上 Chrome 验收独立执行。项目版构建成功。
