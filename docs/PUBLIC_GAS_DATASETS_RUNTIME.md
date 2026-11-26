# 公开燃气数据集运行接入

本分支在现有燃气中控右上角增加数据源选择。原GAS-M0合成案例保留为独立选项；切换到公开来源后，旧案例的资产、事件、路径和计算结果不会留在公开来源视图中。

## 来源和当前可用范围

| 数据源 | 当前读取方式 | 可以展示 | 不支持的结论 |
|---|---|---|---|
| AER Alberta | 从本地只读SQLite快照读取；启动时按manifest SHA-256核验 | 管线记录、来源属性、Release Report记录、精确唯一关联记录；可按分区/来源字段检索并分页 | 低压配气、阀门拓扑、用户供气关系、水力推演；Release Report总数不能称为确认泄漏数 |
| SGN Eastbourne | 服务端调用SGN发布的ArcGIS Feature Layer，限量分页 | 官方发布的城市配气管线几何和原始字段；支持分页查看 | 页面当前页不能当作全量资产总数；无逐段拓扑、阀门、用户关系和供气工况 |
| GasLib-582 v2 | 从指定本地目录解析官方`.net` XML网络描述 | 节点、连接、来源坐标及网络组件的拓扑示意 | 该数据是研究稳态优化基准，不表示当前在运城市网络；本分支没有GasLib-582完整稳态求解器，不能输出压力/流量解 |
| Merrimack Valley | 展示NTSB事故公开页面、最终报告和事故卷宗引用 | 可追溯事故事实、报告统计、证据入口和时间精度 | 不含可重算的完整城市网络；报告客户影响统计不是本系统算法结果；不从图像伪造遥测序列 |

公开资料支持这些范围：AER Enhanced Pipeline不覆盖低压配气；SGN公开其低、中、次中压网络地图；GasLib网络、压缩机、scenario nomination和控制决策是不同文件；NTSB调查页及应急响应事实报告给出Merrimack Valley事件事实和客户影响口径。参见[ AER目录 ](https://www1.aer.ca/ProductCatalogue/557.html)、[ SGN门户 ](https://www.sgn.co.uk/open-data-sharing-portal)、[ GasLib数据页 ](https://gaslib.zib.de/data.html)、[ GasLib格式说明 ](https://gaslib.zib.de/documentation.html)、[ NTSB调查页 ](https://www.ntsb.gov/investigations/pages/pld18mr003.aspx)、[ NTSB应急响应事实报告 ](https://data.ntsb.gov/Docket/Document/docBLOB?FileExtension=.PDF&FileName=Emergency+Response+Group+Factual+Report-Master.PDF&ID=40476254)。

## 本地运行配置

只读公开接口在本地匿名演示时需要同时满足项目原有loopback演示配置，并显式设置：

```powershell
$env:GAS_PUBLIC_DATA_READONLY = "1"
$env:GAS_AER_DATA_ROOT = "D:\path\to\aer\data\processed\aer"
$env:GASLIB_582_DATA_ROOT = "D:\path\to\GasLib-582-v2"
# 仅当目录包含多个582-v2网络文件时才需要指定
$env:GASLIB_582_NETWORK_FILE = "GasLib-582-v2-20211129.net"
$env:WATER_RUNTIME_MODE = "local"
$env:WATER_BIND_HOST = "127.0.0.1"
$env:ALLOW_LOCAL_SYNTHETIC_ANONYMOUS = "1"
```

`GAS_AER_DATA_ROOT`目录中必须有`manifest.json`和`aer_catalog.sqlite3`。服务端会核对manifest里的数据库名、数据集ID和SHA-256；任何不匹配都会拒绝读取。不要将AER源文件、SQLite快照或访问凭据提交到Git。

`GASLIB_582_DATA_ROOT`目录应包含官方GasLib-582 v2 `.net` XML文件。若目录有多个匹配版本，必须通过`GASLIB_582_NETWORK_FILE`显式指定文件名，避免依赖文件系统枚举顺序。解析器会核对公开网络基线582个节点、609条连接，并检查每条连接两端均存在。当前它只提供拓扑和坐标示意，不计算压力、流量、压缩机控制或可行运行点。

SGN需要运行服务能够访问官方ArcGIS Feature Layer；SGN门户的访问流程可能需要先注册取得门户访问链接。门户说明管线位置仅供参考，不能用于施工开挖定位。网络不可达时页面显示当前来源不可用，不回退到合成数据。Merrimack页面引用NTSB公开材料，数字按报告口径注明。

在受控运行中，调用主体还需由服务端ACL显式授权`public`模式及对应数据集ID；前端不能自行提升数据权限。

### 本地启动

在两个PowerShell窗口中从项目目录分别启动：

```powershell
# 窗口一：后端（进入 engineering 目录）
cd engineering
$env:WATER_RUNTIME_MODE = "local"
$env:WATER_BIND_HOST = "127.0.0.1"
$env:ALLOW_LOCAL_SYNTHETIC_ANONYMOUS = "1"
$env:GAS_PUBLIC_DATA_READONLY = "1"
$env:GAS_AER_DATA_ROOT = "D:\path\to\aer\data\processed\aer"
$env:WATER_CORS_ORIGINS = "http://127.0.0.1:5173"
python -m src.gas_application

# 窗口二：前端（进入 web 目录；先按项目锁文件安装依赖）
cd web
$env:VITE_WATERPULSE_PLATFORM_URL = "http://127.0.0.1:18096"
npm ci
npm run dev:project
```

Vite通常会在`http://127.0.0.1:5173`启动；如端口被占用，应同步调整`WATER_CORS_ORIGINS`。`GAS_AER_DATA_ROOT`必须替换为已核验快照目录。公开接口保持只读；该本地匿名例外仅绑定loopback，不得改为`0.0.0.0`或用于外网部署。受控部署应移除本地匿名开关并配置服务端主体、`public`模式和数据集ACL。

## API和合同

- AER 事件栏默认查询已唯一关联到燃气管段的记录。定位时使用精确来源管段编号，详情保留事件和资产两侧证据。
- SGN 可在地图选中资产后请求其周边矩形范围内的官方要素。此范围只用于地图浏览。
- `GET /api/v1/gas/public-data/gaslib_582/connectivity?edge_id=` 比较研究基准中单条管道边退出前后的多源图可达性。它使用无向连接，单位是基准出口和节点。
- Merrimack 复盘支持逐步查看和时间线跳转，各阶段附 NTSB 证据入口。

- `GET /api/v1/gas/public-data/catalog`：列出来源、模式及本地可用状态。
- `GET /api/v1/gas/public-data/{dataset_id}/overview?limit=&offset=`：返回版本化来源概览与有限展示页；AER支持`field_centre`和`search`过滤。
- `GET /api/v1/gas/public-data/aer/assets`、`/aer/events`：AER分页查询，事件支持来源关联状态和管段查询。
- `GET /api/v1/gas/public-data/sgn/assets`：SGN官方图层限量分页。

公开数据返回独立的`data_mode`和来源字段，不转换成原有synthetic `GasAsset`/`GasEvent`合同。AER分页的`status=partial`代表只展示当前页；SGN远端要素数量未核实时显示“未核实”。地图上的线条只按来源几何绘制；GasLib仅对网络端点连线作拓扑示意，不代表实测管线走向。Merrimack没有完整GIS数据时使用证据视图，不画虚构地图。

## 未完成的工程项

1. GasLib完整稳态计算：需要明确并审查全网数学模型、GasLib `.net/.cs/.scn/.cdf`数据组合、求解器及许可证，再建立独立参考解和物料/能量平衡验证。本分支没有安装或引入求解器，因此计算功能明确不可用。
2. SGN几何展示可在线查询，但当前仅展示有限页；生产使用前应由使用方确认SGN门户条款、缓存策略和图层变更处理。
3. AER公开资料有版权与重用条件，部署和对外演示前需核对当前许可条款。
4. Merrimack事故报告可以支持证据复盘，不足以单独重建事故网络、阀门动作和逐户服务影响。

## 本轮检查记录

- Python语法编译：通过（数据适配器、两个API挂载入口及测试文件）。
- AER只读适配器对本机已核验快照的冒烟检查：通过；manifest版本为`aer_public_20261119_6c5fbf3ae3d7`，Calgary分区记录数为22,565，全域资产记录数为209,171，Release Report记录数为86,450。样例事件保持`no_segment_id`来源关联状态，没有把报告总数称为泄漏数。
- 公开数据后端测试 9 项通过，包括两项 HTTP 测试。Windows 测试使用允许回环连接的环境和独立 `--basetemp` 目录。工程后端全量收集在现有环境遇到 `langgraph` 缺失，完整平台仍受原生 Windows `fcntl` 限制。
- 前端 Vitest 404 项通过、13 项跳过。`npm run build:project` 通过。Windows Chrome 已实测 AER 地图、选择和分页，Merrimack 证据与跨来源状态清理，以及 SGN 实时几何和第二页。
- 已读取 GasLib 官方 `GasLib-582-v2-20211129.net`，解析得到 582 个节点和 609 条连接。文件 SHA-256 为 `f76ba873f7e1ef9ece34aed6b70ca619eb7e7b5f39ea9c29236dfd99544f5bf3`。稳态求解仍未接入。
