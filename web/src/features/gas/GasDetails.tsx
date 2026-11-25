import { GasDispositionPanel } from "./GasDispositionPanel";
import type { GasObservationRef } from "../../shared/contracts/platform";
import type { GasAsset, GasEvent } from "./contracts";
import { GAS_NODE_LABELS, UNKNOWN_GAS_RECORD, groupGasRecordIds, type GasRecordIdentity, type GasRecordIndex } from "./assetRecords";
import type { GasApiError } from "./engineeringApi";
import { assetTypes, eventKinds, eventStates, gasLocationReason, hasAuthorizedGasLocation, relatedAssetIds } from "./presentation";

export function GasReadError({ error, retry, label }: { error: GasApiError; retry: () => void; label: string }) {
  return <div className="gas-read-error" role="alert" data-error-code={error.code}>
    <p>{error.message}</p><button type="button" onClick={retry}>{label}</button>
  </div>;
}
export function GasProvenance({ item }: { item: Pick<GasAsset, "data_mode" | "source_status"> }) {
  return <dl className="gas-fields gas-provenance"><div><dt>数据模式</dt><dd>{item.data_mode}</dd></div>
    <div><dt>数据来源</dt><dd>{item.source_status}</dd></div></dl>;
}
type EvidenceSubject = { id: string; label: string; refs?: string[] | null };

export function GasEvidence({ event, asset, showObjects = false }: {
  event?: Pick<GasEvent, "event_id" | "evidence_refs"> | null;
  asset?: Pick<GasAsset, "asset_id" | "evidence_refs"> | null;
  showObjects?: boolean;
}) {
  const subjects: EvidenceSubject[] = [];
  if (event) subjects.push({ id: event.event_id, label: "事件", refs: event.evidence_refs });
  if (asset) subjects.push({ id: asset.asset_id, label: "资产", refs: asset.evidence_refs });

  return <section className="gas-evidence" aria-label="证据引用"><h3>证据引用</h3>
    {subjects.length === 0 && <p className="gas-muted">当前没有已加载的事件或资产。</p>}
    {subjects.map(({ id, label, refs }) => <div key={`${label}:${id}`}>
      {showObjects && <h4>{label} <code>{id}</code></h4>}
      {refs?.length
        ? <ul>{Array.from(new Set(refs), ref => <li key={ref}><code>{ref}</code></li>)}</ul>
        : <p className="gas-muted">接口未提供证据引用。</p>}
    </div>)}
    <p className="gas-muted">引用标识只用于追溯来源，不代表实时监测或业务验证。</p>
  </section>;
}
export function GasLocation({ item, subject }: {
  item: Pick<GasAsset, "data_mode" | "source_status" | "location" | "location_status">;
  subject: "事件" | "资产";
}) {
  const authorized = hasAuthorizedGasLocation(item);
  const reasonId = `gas-${subject === "事件" ? "event" : "asset"}-location-reason`;
  return <section className="gas-location" aria-label={`${subject}定位`}>
    <dl className="gas-fields"><div><dt>位置状态</dt><dd>{item.location_status ?? "unknown"}</dd></div>
      {authorized && item.location ? <><div><dt>坐标系</dt><dd>{item.location.coordinate_system}</dd></div>
        <div><dt>经度 / 纬度</dt><dd>{item.location.longitude} / {item.location.latitude}</dd></div></> : null}</dl>
    <button type="button" disabled aria-describedby={reasonId}>定位{subject}</button>
    <p className="gas-muted" id={reasonId}>{gasLocationReason(item)}</p>
  </section>;
}

type EventAssetGroupsProps = {
  event: GasEvent;
  index?: GasRecordIndex | null;
  selectedAssetId: string | null;
  openAsset: (id: string) => void;
};

export function GasEventAssetGroups({ event, index = null, selectedAssetId, openAsset }: EventAssetGroupsProps) {
  const ids = relatedAssetIds(event);
  const groups = groupGasRecordIds(index, ids);
  return <section className="gas-related" aria-label="事件关联资产" data-testid="gas-event-asset-groups">
    <h3>关联资产 <span>{ids.length} 项</span></h3>
    {groups.length ? groups.map(({ kind, label, ids: groupIds }) => (
      <section className="gas-related-group" data-asset-kind={kind} aria-label={label} key={kind}>
        <h4>{label} <span>{groupIds.length} 项</span></h4>
        <ul className="gas-related-list">
          {groupIds.map((id) => <li key={id}><button type="button"
            aria-label={`打开资产详情 ${id}`} aria-pressed={selectedAssetId === id} onClick={() => openAsset(id)}>
            <span>{id}</span><span className="gas-asset-action">查看详情</span>
          </button></li>)}
        </ul>
      </section>
    )) : <p className="gas-inline-empty">该事件未返回关联资产 ID，不推测资产映射。</p>}
  </section>;
}

export function GasEventDetails({ event, index, selectedAssetId, openAsset, monitoringRef, onOpenObservation }: EventAssetGroupsProps & {
  monitoringRef?: GasObservationRef; onOpenObservation?(ref: GasObservationRef): void;
}) {
  const closedValves = event.closed_valve_ids ?? [];
  return <div className="gas-detail-body" data-testid="gas-event-detail" data-event-id={event.event_id}>
    <div className="gas-object-title"><h3>{event.title}</h3><code>{event.event_id}</code></div>
    <GasProvenance item={event} />
    <dl className="gas-fields gas-event-facts"><div><dt>事件类型</dt><dd>{eventKinds[event.event_kind]} <span className="gas-code">{event.event_kind}</span></dd></div>
      <div><dt>事件时间</dt><dd data-testid="gas-event-time">接口未提供时间字段</dd></div>
      <div><dt>事件来源状态</dt><dd>{event.state ? eventStates[event.state] : "未知"} <span className="gas-code">{event.state ?? "unknown"}</span></dd></div>
      <div><dt>严重程度</dt><dd>{event.severity ?? "unknown"}</dd></div>
      <div><dt>风险计算状态</dt><dd>{event.risk_status ?? "unknown"}</dd></div>
      <div><dt>关闭阀门记录</dt><dd>{closedValves.length ? `${closedValves.length} 项 (${closedValves.join("、")})` : "0 项（非给定关阀记录）"}</dd></div>
      </dl>
    <p className="gas-notice">{event.limitation}</p>
    <GasEventAssetGroups event={event} index={index} selectedAssetId={selectedAssetId} openAsset={openAsset} />
    <GasEvidence event={event} />
    <GasDispositionPanel key={event.event_id} eventId={event.event_id}
      monitoringRef={monitoringRef} onOpenObservation={onOpenObservation} />
    <details className="gas-extra-fields"><summary>其他返回状态</summary><dl className="gas-fields">
      <div><dt>业务验证</dt><dd>{event.validation_status ?? "unknown"}</dd></div>
      <div><dt>所属区县</dt><dd>{event.district_id ?? "未提供"}</dd></div>
      <div><dt>物理供气状态</dt><dd>{event.physical_supply_status ?? "unknown"}</dd></div></dl></details>
    <GasLocation item={event} subject="事件" />
  </div>;
}
export function GasAssetDetails({ asset, identity = UNKNOWN_GAS_RECORD }: { asset: GasAsset; identity?: GasRecordIdentity }) {
  return <div className="gas-detail-body" data-testid="gas-asset-detail" data-asset-id={asset.asset_id}>
    <div className="gas-object-title"><h3>{asset.name}</h3>{asset.name !== asset.asset_id ? <code>{asset.asset_id}</code> : null}</div>
    <dl className="gas-fields"><div><dt>资产编号</dt><dd>{asset.asset_id}</dd></div>
      <div><dt>记录类别</dt><dd data-testid="gas-asset-record-kind">{identity.label}</dd></div>
      <div><dt>拓扑节点子类型</dt><dd>{identity.nodeType ? `${GAS_NODE_LABELS[identity.nodeType]} (${identity.nodeType})` : identity.kind === "unknown" ? "unknown" : "不适用"}</dd></div>
      <div><dt>接口原始类型</dt><dd>{assetTypes[asset.asset_type]} <span className="gas-code">{asset.asset_type}</span></dd></div>
      <div><dt>资产名称</dt><dd>{asset.name}</dd></div>
      <div><dt>资产状态</dt><dd>{asset.status ?? "unknown"}</dd></div>
      <div><dt>所属区县</dt><dd>{asset.district_id ?? "未提供"}</dd></div></dl>
    <GasProvenance item={asset} />
    <p className="gas-notice">仅展示当前 DTO 已返回的属性；管径、材质、压力、流量等专业属性未接入，不补零或推导。</p>
    <GasEvidence asset={asset} />
    <GasLocation item={asset} subject="资产" />
  </div>;
}
