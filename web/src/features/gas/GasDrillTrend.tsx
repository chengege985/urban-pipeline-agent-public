import { useCallback, useState } from "react";
import type { GasM2Binding } from "../../shared/contracts/platform";
import { useGasCockpit } from "./GasCockpitProvider";
import { useGasRead } from "./useGasRead";

type Point = {
  record_id: string; asset_id: string; sensor_id: string; observed_at: string; metric: "pressure" | "flow";
  unit: string; quality: "valid" | "suspect"; value: number; source_sha256: string;
};

function point(row: Record<string, unknown>): row is Point {
  return typeof row.record_id === "string" && typeof row.asset_id === "string" && typeof row.sensor_id === "string"
    && typeof row.observed_at === "string" && Number.isFinite(Date.parse(row.observed_at))
    && ((row.metric === "pressure" && row.unit === "kPa") || (row.metric === "flow" && row.unit === "Nm3/h"))
    && (row.quality === "valid" || row.quality === "suspect") && typeof row.value === "number" && Number.isFinite(row.value)
    && typeof row.source_sha256 === "string" && /^[a-f0-9]{64}$/.test(row.source_sha256);
}

function Trend({ points, start, end }: { points: Point[]; start: number; end: number }) {
  const min = Math.min(...points.map(row => row.value)), max = Math.max(...points.map(row => row.value));
  const dots = points.map(row => ({
    ...row, time: Date.parse(row.observed_at),
    x: 12 + (Date.parse(row.observed_at) - start) / Math.max(1, end - start) * 256,
    y: max === min ? 39 : 68 - (row.value - min) / (max - min) * 56,
  }));
  const first = points[0];
  const label = first.asset_id.replace("syn-gas-pipe-", "") + " " + (first.metric === "pressure" ? "压力" : "流量");
  return <figure className="gas-drill-trend-series" data-channel={first.metric} data-points={points.length}>
    <figcaption>{label} · {points.length} 条 · {min}–{max} {first.unit}</figcaption>
    <svg viewBox="0 0 280 80" role="img" aria-label={label + "合成观测趋势"}>
      <path d="M 12 68 H 268 M 12 12 V 68" className="gas-drill-trend-axis" />
      {dots.slice(1).map((dot, index) => {
        const previous = dots[index];
        if (dot.time - previous.time !== 60_000 || dot.quality !== "valid" || previous.quality !== "valid") return null;
        return <line key={dot.record_id} x1={previous.x} y1={previous.y} x2={dot.x} y2={dot.y} className="gas-drill-trend-line" />;
      })}
      {dots.map(dot => <circle key={dot.record_id} cx={dot.x} cy={dot.y} r="2.8" data-quality={dot.quality}>
        <title>{dot.observed_at} · {dot.value} {dot.unit} · {dot.record_id}</title>
      </circle>)}
    </svg>
  </figure>;
}

export function GasDrillTrend({ binding, count }: { binding: GasM2Binding; count: number }) {
  const [open, setOpen] = useState(false);
  const { dataM2Api } = useGasCockpit();
  const query = useCallback((signal: AbortSignal) => dataM2Api.m2Records(binding, "observations", undefined, signal), [binding, dataM2Api]);
  const read = useGasRead(open ? binding.registry_sha256 + "/" + binding.version : null, query);
  const points = read.data?.items.filter(point).sort((a,b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
  const verified = points?.length === count && points.length > 0 && read.data?.total === count;
  const groups = new Map<string, Point[]>();
  if (verified) for (const row of points) {
    const key = row.asset_id + "/" + row.sensor_id;
    const group = groups.get(key) ?? [];
    group.push(row);
    groups.set(key, group);
  }
  return <details onToggle={event => setOpen(event.currentTarget.open)} className="gas-drill-trend">
    <summary>合成观测趋势</summary>
    {read.status === "loading" && <p role="status">正在读取固定版本的合成观测…</p>}
    {read.error && <p role="alert">{read.error.message} <button type="button" onClick={read.retry}>重试</button></p>}
    {read.data && !verified && <p role="alert">观测数量或字段与当前 TrustShield 回执不一致。</p>}
    {verified && <div data-testid="gas-drill-trend" data-observation-count={count}>
      <p>DEMO · {count} 条原始观测 · {points[0].observed_at.slice(11,16)}–{points.at(-1)?.observed_at.slice(11,16)} UTC。缺测处保留断点。</p>
      {[...groups.entries()].map(([id, rows]) => <Trend key={id} points={rows} start={Date.parse(points[0].observed_at)} end={Date.parse(points.at(-1)!.observed_at)} />)}
      <details><summary>观测记录与来源</summary>
        <ul>{points.map(row => <li key={row.record_id}><code>{row.record_id}</code> · {row.value} {row.unit} · {row.quality}</li>)}</ul>
        <code>SHA-256 {points[0].source_sha256}</code>
      </details>
    </div>}
  </details>;
}
