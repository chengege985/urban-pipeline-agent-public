import { useEffect, useMemo, useRef, type MouseEvent } from "react";
import type { PilotMappedLine, PilotReplayResult } from "../../shared/contracts/platform";

const width = 960;
const height = 254;
const left = 78, right = width - 24, top = 38, bottom = height - 48, qualityRow = height - 34;

function screen(canvas: HTMLCanvasElement | null, background = "#102334") {
  const context = canvas?.getContext("2d");
  if (!context) return null;
  context.fillStyle = background;
  context.fillRect(0, 0, width, height);
  context.font = "13px system-ui";
  context.lineWidth = 1;
  context.textAlign = "left";
  return context;
}

function plot(report: PilotReplayResult) {
  const start = Date.parse(report.window_start ?? "");
  const span = Math.max(1, Date.parse(report.window_end ?? "") - start);
  const x = (at: string) => left + (right - left) * Math.min(1, Math.max(0, (Date.parse(at) - start) / span));
  const thresholds = report.applied_thresholds ?? report.segments.map(({ threshold }) => threshold);
  const values = [
    ...report.observations.filter(({ observation }) => observation.quality !== "invalid").map(({ observation }) => observation.value),
    ...thresholds.flatMap((rule) => [rule.lower, rule.upper]),
  ].filter((value): value is number => value !== null);
  const low = values.length ? Math.min(...values) : null, high = values.length ? Math.max(...values) : null;
  const magnitude = Math.max(Math.abs(low ?? 0), Math.abs(high ?? 0)) || 1;
  const lower = (low ?? 0) / magnitude, range = (high ?? 0) / magnitude - lower;
  const y = (value: number) => range === 0 ? (top + bottom) / 2 : bottom - (value / magnitude - lower) / range * (bottom - top);
  const points = report.observations.map((point) => ({
    point, x: x(point.observation.observed_at),
    y: point.observation.value === null || point.observation.quality === "invalid" ? qualityRow : y(point.observation.value),
  }));
  return { x, y, low, high, points, thresholds };
}

function drawTrend(canvas: HTMLCanvasElement, report: PilotReplayResult, chart: ReturnType<typeof plot>, selected: string | null) {
  const css = window.getComputedStyle(canvas);
  const color = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  const axes = color("--gas-ink-2", "#9eb8cd");
  const blue = color("--gas-brand-strong", "#78c4f8");
  const orange = color("--gas-warn", "#f4b565");
  const green = color("--gas-success", "#46b980");
  const violet = color("--gas-pilot-suspect", "#bb9bf5");
  const context = screen(canvas, color("--gas-surface-2", "#102334"));
  if (!context || !report.window_start || !report.window_end) return;
  const { x, y, thresholds } = chart;
  const line = (x1: number, y1: number, x2: number, y2: number, ink: string) => {
    context.strokeStyle = ink;
    context.beginPath();
    context.moveTo(x1, y1);
    context.lineTo(x2, y2);
    context.stroke();
  };
  for (const segment of report.segments) {
    segment.channel.events.forEach((event, index, events) => {
      const until = x(events[index + 1]?.at ?? segment.end);
      if (event.kind === "limit_exceeded" || event.kind === "data_missing") {
        const from = x(event.missing_slots[0] ?? event.at);
        context.fillStyle = event.kind === "limit_exceeded" ? color("--gas-warn-soft", "#f4b56526")
          : color("--gas-pilot-missing", "#a996e854");
        context.fillRect(from, top, Math.max(3, until - from), bottom - top);
      } else {
        context.setLineDash([3, 3]);
        line(x(event.at), top, x(event.at), bottom, green);
        context.setLineDash([]);
        context.fillStyle = green;
        context.fillRect(x(event.at) - 2, top, 4, 5);
      }
    });
  }
  for (const [index, rule] of thresholds.entries()) {
    const from = x(rule.effective_from), until = x(rule.effective_until);
    context.setLineDash([6, 4]);
    for (const bound of [rule.lower, rule.upper]) {
      if (bound !== null) line(from, y(bound), until, y(bound), orange);
    }
    if (index > 0) line(from, top - 9, from, bottom, axes);
    context.setLineDash([]);
    context.fillStyle = orange;
    const space = until - from - 7;
    if (space > 42) context.fillText(rule.version, from + 4, 22, space);
  }
  line(left, top, left, bottom, axes);
  line(left, bottom, right, bottom, axes);
  context.fillStyle = axes;
  if (chart.high !== null) {
    context.fillText(chart.high.toPrecision(4), 9, top + 4, left - 14);
    context.fillText(chart.low!.toPrecision(4), 9, bottom + 4, left - 14);
  }
  context.fillText(report.sensor.unit, 9, top + 23);
  context.fillText(report.window_start.slice(0, 19).replace("T", " ") + " UTC", left, height - 10);
  context.textAlign = "right";
  context.fillText(report.window_end.slice(0, 19).replace("T", " ") + " UTC", right, height - 10);
  const cadences = new Map(thresholds.map((rule) => [rule.threshold_id, rule.cadence_seconds * 1000]));
  let previous: (typeof chart.points)[number] | undefined;
  for (const item of chart.points) {
    const { observation } = item.point;
    const policy = item.point.threshold_id;
    const before = previous?.point.observation;
    if (observation.quality === "valid" && previous && before && policy
        && previous.point.threshold_id === policy && item.point.classification !== "off_schedule"
        && Date.parse(observation.observed_at) - Date.parse(before.observed_at) === cadences.get(policy)) {
      line(previous.x, previous.y, item.x, item.y, blue);
    }
    context.fillStyle = observation.quality !== "valid" ? violet : item.point.classification === "anomaly" ? orange : blue;
    context.beginPath();
    if (observation.quality === "suspect") {
      context.moveTo(item.x, item.y - 6);
      context.lineTo(item.x + 6, item.y + 5);
      context.lineTo(item.x - 6, item.y + 5);
      context.closePath();
    } else if (observation.quality !== "valid") {
      context.rect(item.x - 3, item.y - 3, 6, 6);
    } else {
      context.arc(item.x, item.y, 3.8, 0, 2 * Math.PI);
    }
    context.fill();
    if (observation.observation_id === selected) {
      context.strokeStyle = color("--gas-ink-1", "#eaf1f7");
      context.beginPath();
      context.arc(item.x, item.y, 8, 0, 2 * Math.PI);
      context.stroke();
    }
    previous = observation.quality === "valid" && item.point.classification !== "off_schedule" ? item : undefined;
  }
}

export function GasPilotTrend({ report, selected, onSelect }: {
  report: PilotReplayResult; selected: string | null; onSelect: (id: string) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const chart = useMemo(() => plot(report), [report]);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const paint = () => drawTrend(element, report, chart, selected);
    paint();
    const theme = element.closest("[data-theme]");
    const observer = new MutationObserver(paint);
    if (theme) observer.observe(theme, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, [report, chart, selected]);

  function selectPoint(event: MouseEvent<HTMLCanvasElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) * width / bounds.width;
    const y = (event.clientY - bounds.top) * height / bounds.height;
    let target, distance = 16 ** 2;
    for (const point of chart.points) {
      const next = (point.x - x) ** 2 + (point.y - y) ** 2;
      if (next <= distance) {
        target = point;
        distance = next;
      }
    }
    if (target) onSelect(target.point.observation.observation_id);
  }

  if (!report.observations.length) return null;
  return <figure className="gas-pilot-figure" data-testid="gas-pilot-trend">
    <figcaption>{report.sensor.metric === "pressure" ? "压力" : "流量"}历史趋势 · 点击观测点打开原记录</figcaption>
    <ul className="gas-pilot-legend" aria-label="趋势图图例">
      <li data-mark="reading">有效读数</li><li data-mark="alarm">越限及阈值</li>
      <li data-mark="quality">可疑（三角）或无可用值（方块）</li><li data-mark="recovery">恢复</li>
    </ul>
    <canvas ref={canvas} width={width} height={height} onClick={selectPoint} role="img"
      aria-label="历史观测和阈值换版。键盘可在观测记录表中选择同一记录。" />
    {chart.thresholds.length > 0 && <ol className="gas-pilot-threshold-strip" aria-label="生效阈值与切换时间">
      {chart.thresholds.map((rule) => <li key={rule.threshold_id}>
        <strong>{rule.version}</strong> · 生效 <time dateTime={rule.effective_from}>{rule.effective_from}</time>
      </li>)}
    </ol>}
  </figure>;
}

const blue = "#78c4f8";
const muted = "#9eb8cd";

export function GasPilotGeometry({ lines }: { lines: PilotMappedLine[] }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const context = screen(canvas.current);
    if (!context) return;
    const coordinates = lines.map(({ geometry }) => geometry.coordinates.filter(
      (point): point is [number, number] => Array.isArray(point) && point.length === 2
        && point.every((value) => typeof value === "number" && Number.isFinite(value)),
    ));
    let left = Infinity, right = -Infinity, bottom = Infinity, top = -Infinity;
    for (const line of coordinates) for (const [x, y] of line) {
      left = Math.min(x, left); right = Math.max(x, right);
      bottom = Math.min(y, bottom); top = Math.max(y, top);
    }
    if (!Number.isFinite(left)) return;
    const midX = (left + right) / 2, midY = (top + bottom) / 2;
    const scale = Math.min((width - 72) / Math.max(right - left, 1e-9), (height - 58) / Math.max(top - bottom, 1e-9));
    context.strokeStyle = blue;
    context.lineWidth = 3;
    for (const line of coordinates) {
      context.beginPath();
      line.forEach(([x, y], i) => {
        const screenX = width / 2 + (x - midX) * scale, screenY = height / 2 - (y - midY) * scale;
        if (i === 0) context.moveTo(screenX, screenY); else context.lineTo(screenX, screenY);
      });
      context.stroke();
    }
    context.fillStyle = muted;
    context.fillText("WGS84 · 经度 / 纬度", 18, height - 12);
  }, [lines]);
  return <figure className="gas-pilot-figure" data-testid="gas-pilot-local-map">
    <figcaption>授权线几何 · 本地视图</figcaption>
    <canvas ref={canvas} width={width} height={height} role="img" aria-label="试点管段的 WGS84 线几何" />
  </figure>;
}
