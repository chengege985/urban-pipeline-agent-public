import { useCallback, useMemo } from "react";
import type { GasM2Binding, GasTopicSelection } from "../../shared/contracts/platform";
import { CHONGQING_DISTRICTS, findChongqingDistrict } from "./chongqingDistricts";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasReadError } from "./GasDetails";
import { useGasRead } from "./useGasRead";
import { downloadGasFile, formatGasMetric, topicPrintReport } from "./printableGasReport";
import "./gas-topic.css";

const labels = { pipe: "管段", node: "节点", valve: "阀门", consumer: "用户节点" } as const;

export function useGasTopic(binding: GasM2Binding | null) {
  const c = useGasCockpit();
  const geographic = c.route.gasMap === "gis";
  const districtId = geographic ? c.selectedDistrictId : null;
  const district = findChongqingDistrict(districtId);
  const subnet = geographic ? undefined : c.route.gasSubnetId
    ?? (c.route.gasAssetId ? c.caseView.data?.network_id : undefined);
  const asset = subnet ? c.route.gasAssetId : undefined;
  const selection = useMemo<GasTopicSelection>(() => geographic
    ? { scope: "chongqing", district_id: districtId }
    : { scope: "demo", subnet_id: subnet, asset_id: asset }, [geographic, districtId, subnet, asset]);
  const fetchTopic = useCallback((signal: AbortSignal) => {
    if (!binding) throw new Error("当前数据登记不可用。");
    return c.dataM2Api.dataTopic(binding, selection, signal);
  }, [c.dataM2Api, binding, selection]);
  const canRead = binding && (geographic || !c.route.gasAssetId || subnet);
  const resource = useGasRead(canRead ? JSON.stringify([binding, selection]) : null, fetchTopic);
  const topic = resource.data;
  const title = geographic ? district?.name ?? "重庆全市" : topic?.selected_asset?.asset_id ?? (subnet ? "GAS-M0 合成子网" : "GAS-M0 合成目录");
  return { resource, district, districtId, geographic, subnet, asset, title,
    focused: Boolean(geographic ? districtId : c.route.gasAssetId) };
}

export function GasTopicPanel({ workspace, openQuality }: {
  workspace: ReturnType<typeof useGasTopic>; openQuality(): void;
}) {
  const c = useGasCockpit();
  const { resource, district, districtId, geographic, subnet, asset, title } = workspace;
  const topic = resource.data;

  function move(map: "gis" | "topology", subnetId?: string, assetId?: string) {
    c.go({ gasMap: map, gasDistrictId: undefined, gasSubnetId: subnetId, gasAssetId: assetId, gasEventId: undefined,
      gasObservation: undefined, gasCatalogueOpen: false, gasSection: assetId ? "assets" : "events" });
  }

  return <section className="gas-topic" aria-label="燃气数据导航" data-testid="gas-data-topic" data-topic-level={topic?.level}>
    <header><h3>{geographic ? "重庆区县专题" : "演示数据导航"}</h3><span>{geographic ? "GIS 行政参考" : "DEMO / SYNTHETIC"}</span></header>
    <nav className="gas-topic-actions" aria-label="专题位置">
      <button type="button" onClick={() => move("gis")}>重庆全市</button>
      {geographic && district && <span aria-current="page">{district.name}</span>}
      {!geographic && <button type="button" onClick={() => move("topology")}>合成目录</button>}
      {!geographic && subnet && <button type="button" onClick={() => move("topology", subnet)}>子网</button>}
      {!geographic && asset && <span aria-current="page">管段/设备</span>}
    </nav>
    {geographic ? <label className="gas-topic-select">区县
      <select aria-label="专题区县" value={districtId ?? ""} onChange={event => c.selectDistrict(event.target.value || null)}>
        <option value="">重庆全市</option>
        {CHONGQING_DISTRICTS.map(item => <option key={item.district_id} value={item.district_id}>{item.name}</option>)}
      </select>
    </label> : null}
    {resource.status === "loading" && <p role="status">正在读取 M2 登记…</p>}
    {resource.error && <GasReadError error={resource.error} retry={resource.retry} label="重试专题" />}
    {topic && <>
      <h4>{title}</h4>
      <dl className="gas-topic-metrics">
        <div><dt>登记资产</dt><dd>{topic.registered_assets}</dd></div>
        <div><dt>登记事件</dt><dd>{topic.registered_events}</dd></div>
        <div><dt>字段完整率</dt><dd>{formatGasMetric(topic.field_completeness)}</dd></div>
      </dl>
      {geographic ? <>
        <p className="gas-topic-empty" data-testid="gas-topic-authorized-empty">当前登记仅有行政参考。授权燃气管线坐标 unavailable，实际覆盖未知。</p>
        <details><summary>授权子网登记（{topic.subnets.length}）</summary><p>当前范围无获准展示的子网、管段、设备和事件。</p></details>
        {topic.administrative_source_uri && <p><a href={topic.administrative_source_uri} target="_blank" rel="noreferrer">行政区划来源</a></p>}
      </> : <>
        {!subnet && <div className="gas-topic-actions">
          {topic.subnets.map(item => <button key={item.subnet_id} type="button" onClick={() => move("topology", item.subnet_id)}>{item.name}</button>)}
        </div>}
        {subnet && <label className="gas-topic-select">管段/设备
          <select aria-label="子网管段或设备" value={asset ?? ""} onChange={event => move("topology", subnet, event.target.value || undefined)}>
            <option value="">选择登记对象</option>
            {(Object.keys(labels) as Array<keyof typeof labels>).map(kind => <optgroup key={kind} label={labels[kind]}>
              {topic.assets.filter(item => item.asset_type === kind).map(item => <option key={item.asset_id} value={item.asset_id}>
                {item.asset_id.replace(/^syn-gas-(pipe|node|valve|consumer)-/, "")}</option>)}
            </optgroup>)}
          </select>
        </label>}
        {topic.selected_asset && <p data-testid="gas-topic-selected-asset" data-asset-id={topic.selected_asset.asset_id}>
          {labels[topic.selected_asset.asset_type]} · <code>{topic.selected_asset.asset_id}</code>
        </p>}
        <details><summary>合成事件（{topic.events.length}）</summary><ul>{topic.events.map(id => <li key={id}><button type="button"
          onClick={() => c.selectEvent(id)}>{id.replace(/^syn-gas-event-/, "")}</button></li>)}</ul></details>
      </>}
      <div className="gas-topic-actions">
        {geographic && <button type="button" onClick={() => move("topology")}>打开独立合成拓扑</button>}
        <button type="button" onClick={openQuality}>数据质量工作台</button>
        <button type="button" onClick={() => downloadGasFile("gas-topic-" + (districtId ?? topic.level), topicPrintReport(topic, title), "html")}>
          导出专题打印版
        </button>
      </div>
    </>}
  </section>;
}
