import { useState } from "react";
import type { GasAsset, GasCollection } from "./contracts";
import type { GasCaseResponse } from "./types";
import type { GasReadState } from "./useGasRead";
import { GasReadError } from "./GasDetails";
import { GAS_NODE_LABELS, gasRecordIdentity, type GasRecordIndex } from "./assetRecords";
import { GAS_ASSET_PAGE_SIZE, GAS_ASSET_TYPES, type GasEngineeringRoute } from "../../shared/routing/gasRoutes";

export function GasRecordSummary({ catalogue, total, eventsTotal }: { catalogue: GasCaseResponse | null; total: number | null; eventsTotal?: number | null }) {
  const counts = catalogue?.counts;
  const nodeValves = catalogue?.nodes.filter((node) => node.node_type === "valve").length;
  return <section className="gas-record-summary" aria-label="资产与拓扑统计口径">
    <dl>{[["管段资产", counts?.pipes, "gas-pipe-count"], ["拓扑节点", counts?.nodes, "gas-node-count"],
      ["阀门资产", counts?.valves, "gas-valve-count"], ["消费对象记录", counts?.consumers, "gas-consumer-count"],
      ["原始资产总记录", total, "gas-record-total"], ...(eventsTotal !== undefined ? [["合成事件", eventsTotal, "gas-event-count"]] : [])].map(([label, value, testId]) => <div key={label}>
        <dt>{label}</dt><dd data-testid={testId as string}>{value ?? "—"}</dd></div>)}</dl>
    <details className="gas-count-explanation"><summary>统计口径 · 不重复计算阀门</summary><p>GAS-M0 合成候选。统计分别读取案例合同 counts 与资产接口 total，不随分页或筛选改写。
      {nodeValves !== undefined ? ` ${counts?.nodes} 个拓扑节点中含 ${nodeValves} 个阀门节点；它们不是新增的 ${nodeValves} 条阀门资产。` : " 集合映射未取得时，统计和记录类别保留未知。"}</p></details>
  </section>;
}

const TYPE_LABELS: Record<GasAsset["asset_type"], string> = {
  pipe: "pipe · 管段记录", valve: "valve · 阀门资产及阀门节点", node: "node · 节点记录",
  source: "source · 气源节点", consumer: "consumer · 消费对象记录", regulator: "regulator · 调压节点", junction: "junction · 普通节点",
};
export function GasAssetDirectory({ route, resource, index, allTotal, selectedAssetId, onPage, onType, onOpen, onLookup }: {
  route: GasEngineeringRoute;
  resource: GasReadState<GasCollection<GasAsset>> & { retry: () => void };
  index: GasRecordIndex | null;
  allTotal: number | null;
  selectedAssetId?: string;
  onPage: (page: number) => void;
  onType: (type: GasAsset["asset_type"] | undefined) => void;
  onOpen: (id: string, button: HTMLButtonElement) => void;
  onLookup: (id: string) => void;
}) {
  const [lookup, setLookup] = useState("");
  const total = resource.data?.total ?? (!route.assetType ? allTotal : null);
  const pages = total === null ? null : Math.max(1, Math.ceil(total / GAS_ASSET_PAGE_SIZE));
  const offset = (route.assetPage - 1) * GAS_ASSET_PAGE_SIZE;
  const loaded = resource.data?.items.length;
  return <section className="gas-panel gas-asset-directory" aria-labelledby="gas-directory-heading" aria-busy={resource.status === "loading"}>
    <div className="gas-card-heading"><h2 id="gas-directory-heading">资产目录</h2><span className="gas-readonly-label">服务端分页 · 每页 {GAS_ASSET_PAGE_SIZE} 条</span></div>
    <div className="gas-directory-controls"><label>接口原始类型<select aria-label="筛选接口原始类型" value={route.assetType ?? ""}
      onChange={(event) => onType(GAS_ASSET_TYPES.find((type) => type === event.target.value))}>
      <option value="">全部记录</option>{GAS_ASSET_TYPES.map((type) => <option value={type} key={type}>{TYPE_LABELS[type]}</option>)}</select></label>
      <form onSubmit={(event) => { event.preventDefault(); const id = lookup.trim(); if (id) onLookup(id); }}>
        <label>按完整资产编号查询<input aria-label="查询资产编号" required value={lookup} onChange={(event) => setLookup(event.target.value)} placeholder="通过详情 API 跨页查询" /></label>
        <button type="submit">查询详情</button></form></div>
    <p className="gas-directory-progress" role="status" data-testid="gas-asset-page-progress">
      本页已加载 {loaded ?? "—"} / 总记录 {total ?? "未知"}；第 {route.assetPage} / {pages ?? "—"} 页
      {loaded ? `，当前记录 ${offset + 1}–${offset + loaded}` : ""}。
      <span> total 保留当前服务端类型筛选口径，不表示独立设施数量。</span></p>
    {route.assetType === "valve" ? <p className="gas-notice">valve 是接口原始类型，同时包含阀门资产和阀门拓扑节点。请按“记录类别”区分；不能把筛选总记录数当作阀门资产数。</p> : null}
    {resource.status === "loading" ? <p className="gas-inline-empty" role="status">正在读取第 {route.assetPage} 页；不展示上一页记录…</p> : null}
    {resource.error ? <GasReadError error={resource.error} retry={resource.retry} label="重试当前资产页" /> : null}
    {resource.data ? <div className="gas-directory-table-wrap" tabIndex={0} role="region" aria-label="资产分页表，可横向滚动">
      <table><caption className="gas-sr-only">接口记录不做删除或合并；记录集合根据已确认案例 ID 对照。</caption><thead><tr>
        <th scope="col">资产编号</th><th scope="col">记录类别</th><th scope="col">拓扑节点子类型</th><th scope="col">接口原始类型</th></tr></thead>
        <tbody>{resource.data.items.map((item) => { const identity = gasRecordIdentity(index, item.asset_id); return <tr key={item.asset_id} data-asset-id={item.asset_id} data-record-kind={identity.kind}>
          <td><button type="button" aria-label={`查看资产 ${item.asset_id}`} aria-pressed={selectedAssetId === item.asset_id}
            onClick={(event) => onOpen(item.asset_id, event.currentTarget)}>{item.asset_id}</button></td>
          <td>{identity.label}</td><td>{identity.nodeType ? `${GAS_NODE_LABELS[identity.nodeType]} (${identity.nodeType})` : identity.kind === "unknown" ? "unknown" : "不适用"}</td>
          <td>{item.asset_type}</td></tr>; })}</tbody></table>
      {!resource.data.items.length ? <p className="gas-inline-empty">本页未返回记录。{pages && route.assetPage > pages ? ` 当前筛选只有 ${pages} 页，请返回有效页码。` : "该类型可能没有记录。"}</p> : null}</div> : null}
    <nav className="gas-pagination" aria-label="资产分页">
      <button type="button" disabled={route.assetPage <= 1} onClick={() => onPage(1)}>首页</button>
      <button type="button" disabled={route.assetPage <= 1} onClick={() => onPage(route.assetPage - 1)}>上一页</button>
      <span>第 {route.assetPage} 页</span>
      <button type="button" disabled={pages === null || route.assetPage >= pages} onClick={() => onPage(route.assetPage + 1)}>下一页</button>
      <button type="button" disabled={pages === null || route.assetPage === pages} onClick={() => { if (pages) onPage(pages); }}>末页</button>
    </nav>
  </section>;
}
