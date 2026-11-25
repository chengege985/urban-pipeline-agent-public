# Gas Control Room：唯一燃气主界面

基线为Codex统一提交 `0940740db1743c49f640f6cdfc1ea5516559edb7`。只修改前端业务及前端回归，不修改后端、ACL、Agent、Report、冻结算法、Fixture或数据库。

## 唯一容器与案例

`App → GasPage → GasCockpitProvider → GasCockpit`。GasEngineeringPage独立控制器已退出并删除；兼容地址由路由层规范化，不启动另一套应用。旧供水与Report显式入口由LegacyApp保留，燃气根不实例化供水bootstrap。

Provider是唯一案例请求位置，通过既有GasEngineeringApi.catalogue复用fetchGasCase和共享FetchTransport。主图、204记录目录、事件/资产详情、证据、图层与智能体上下文全部消费同一案例对象、case_id/network_id及内容版本身份。详情仍通过真实独立GET读取，不拿缓存或Case静态属性冒充HTTP详情。案例身份进入请求键，切换上下文立即遮蔽旧响应并取消过期请求。

## 路由

- 默认 `#/gas`；空hash以replace规范化。
- `#/gas/events/{event_id}`、`#/gas/events/{event_id}/assets/{asset_id}`。
- `#/gas/assets` 为同一中控的分页目录弹窗；`#/gas/assets/{asset_id}` 为右侧独立资产详情。`catalogue=1`保留目录打开状态。
- `#/gas/analysis`、`#/gas/assistant` 打开同一主界面的分析/上下文内容，不是独立应用。可用event/asset查询参数保留上下文。
- `mode`、`layout`、`map`、`page`、`type`、`eventKind`、`eventState`保留模式、布局、地图能力、资产分页/类型及事件筛选，返回与刷新保持一致。
- `#/gas-control-room`、`#/gas-engineering`和既有详情/分页地址兼容为上述规范路由；TopBar只有一个燃气主入口。

## 原中控11窗口

保留topology、scenario、timeline、impact、selected、services、layers、data、evidence、boundary、tasks。默认展示事件/情景、中央图、右侧详情、底部时间线和简洁任务入口；其余窗口按需展开。逐个隐藏、恢复与全部恢复均由同一注册表管理，全部恢复不等于首屏同时铺11块。

三模式分别为综合态势、风险分析、应急推演；内容优先级实际不同。保留command/analysis/compact三布局。深海蓝、中央SVG拓扑、滚轮缩放、空白平移、窗口拖动/调整和选中交互沿用原中控；图层窗口和中央图层开关共用状态。

GasTopologyView从原SVG抽出；保持54节点、73管段的schematic坐标。管段按pipe_id/edge关系、阀门按valve_id/node_id关系真实反向高亮/适配，不按字符串前缀猜映射。无映射明确不可定位；合成拓扑定位与禁用的GIS定位是不同动作。

## 数据与合法边界

- 每页50条服务端资产分页，保留limit/offset/type及total。原始总记录204，阀门资产31；54拓扑节点中的31阀门节点不重复计入资产KPI。
- 原始asset_type=valve筛选仍有62条混合记录，列表分别显示记录集合、节点子类型与原始类型，不改写合同或去重Fixture。
- 事件保留7管段＋2阀门共9个真实关联ID，全部可HTTP下钻；事件选择同步高亮中央对应关系。
- 保留synthetic、partial、not_business_validated、unknown/unavailable及401/403/404/503、网络/合同失败与重试。缺失时间仍显示接口未提供，不从Fixture拼字段。
- 当前只有GAS-M0合成拓扑。高德重庆GIS未就绪，不加载虚构点位或把schematic/px换成经纬度。
- 正式风险排行/高危结果为unavailable；合成关注只按已有事件状态，不输出全重庆榜单或风险分数。
- 智能体为模型未接入、context_only和pending_validation，明确未调用模型；不把readonly上下文伪装成AI执行。真实连接状态必须由后续确认的服务合同提供，不接受URL参数伪装就绪。
- 时间线只读取已有相对时刻与图结果，不补造事件。纯reducer和单一定时器处理播放，自动回放不污染浏览器历史；reduced-motion禁用自动播放并保留手动选点。

## 前端组件与回归

- GasCockpitProvider.tsx：唯一数据与中控状态；GasPage.tsx仅为容器。
- GasCockpit.tsx：原中控及11窗口编排；GasTopologyView.tsx：原SVG的受控视图。
- GasPanels.tsx：事件、时间线、影响、服务、图层、数据、证据；GasInspector.tsx：统一详情、目录与能力空态。
- cockpitModel.ts：纯窗口、时间线与schematic视图映射，不计算风险或物理供气。
- FIX3的GasAssetDirectory、GasDetails、assetRecords和受控API层继续复用。
- gasCockpitUnify.test.ts及既有回归覆盖模型/路由/缺项；gas-cockpit-unify.browser.mjs使用真实TCP，不拦截业务API。
- serve-gas-unify-acceptance.py仅用于127.0.0.1验收。它从固定基线导出并逐Git blob校验137个后端文件，在临时本地盘运行未修改FastAPI以绕开对象存储导入超时。测试主体只在进程内；上游不可达返回502，不能算作真实Fixture 503。不是生产BFF。

最终整合报告与网络、截图及播放证据位于web/docs；交付只走独立分支和工作树，由Codex集成，Gemini与HY4独立审核。

## Sprint 01 工程版工作台

工程版 `#/gas` 默认打开重庆 GIS。继续使用 `GasCockpitProvider` 的路线和窗口状态，`?map=topology` 保留独立合成拓扑视图。行政边界由高德 JS API 2.0 的 `DistrictSearch` 返回，核对区划代码后在当前页面会话内复用。离线时仅提供区县参考位置与授权数据状态。

`GasM2Workbench` 是首页读取入口，通过 `GasEngineeringApi`、共享传输层与 `useGasRead` 获取登记、只读记录和证据。目录、详情、拓扑关联与问答使用同一数据集 ID、版本和登记 SHA。换记录时取消旧请求，过期登记由页面重新读取。正式评分、风险与物理预测由服务能力边界决定。

后续情景、趋势、TrustShield 和报告可各自作为燃气面板接入同一窗口注册表。面板读取版本化数据与证据，认证、ACL 和模型工具权限由服务端校验。燃气专业指标由燃气数据和算法合同定义。
