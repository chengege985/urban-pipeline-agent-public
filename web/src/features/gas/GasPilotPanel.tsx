import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import type { PilotCitation, PilotDataset, PilotDatasetEntry, PilotReplayPoint, PilotReplayResult, PilotSource } from "../../shared/contracts/platform";
import { GasReadError } from "./GasDetails";
import { GasPilotApi, pilotVersionKey, type ReplaySelection } from "./gasPilotApi";
import { gasMonitoringMetricLabel } from "./GasMonitoringPanel";
import { useGasRead } from "./useGasRead";
import { GasPilotGeometry, GasPilotTrend } from "./GasPilotVisuals";
import { GasPilotAccess, GasPilotQuality, GasPilotRejections } from "./GasPilotQuality";

const quality = { valid: "有效", suspect: "可疑", invalid: "无效", missing: "缺失" };
const classification = { normal: "范围内", anomaly: "单点越限", excluded: "质量排除",
  off_schedule: "偏离采样时点", no_policy: "无适用阈值" };
const transitions = { limit_exceeded: "持续越限", data_missing: "数据缺失", recovered: "恢复" };
const accessErrors: Record<number, string> = {
  401: "登录状态已失效，重新登录后可选择数据集。",
  403: "当前访问已超出授权范围，请重新选择可用版本。",
  409: "所选数据集或阈值版本已失效，旧观测和问答已清空。请重新选择。",
};
type AccessLost = (status: number) => void;

function Source({ row }: { row: PilotSource }) {
  return <span className="gas-monitoring-source">
    <code>{row.source_id}</code> · {row.source_version} · 记录 <code>{row.source_record_id}</code> ·
    {row.sheet && <> 工作表 {row.sheet} ·</>} 第 {row.source_row} 行 · SHA-256 <code>{row.source_sha256}</code>
  </span>;
}

function GasPilotObservationDetail({ entry, report, point, api, onAccessLost, qwenAllowed, onSelect, onOpenCuvDemo }: {
  entry: PilotDatasetEntry; report: PilotReplayResult; point: PilotReplayPoint; api: GasPilotApi;
  onAccessLost: AccessLost; qwenAllowed: boolean; onSelect: (id: string) => void; onOpenCuvDemo?: () => void;
}) {
  const observation = point.observation;
  const [view, setView] = useState<"pipe" | "map" | "topology" | null>(null);
  const [question, setQuestion] = useState("解释当前观测、阈值和关联事件的证据。");
  const [submission, setSubmission] = useState<{ question: string; number: number } | null>(null);
  const [turns, setTurns] = useState<{ number: number; question: string; answer: string }[]>([]);
  const [citation, setCitation] = useState<PilotCitation | null>(null);
  const trace = useRef<HTMLDivElement>(null);
  const detail = useRef<HTMLElement>(null);
  useEffect(() => { detail.current?.focus(); }, []);
  const eventDetails = useRef<HTMLDetailsElement>(null);
  const getPipe = useCallback((signal: AbortSignal) => api.pipe(entry, report.pipe_id, observation.observed_at, signal),
    [api, entry, report.pipe_id, observation.observed_at]);
  const pipe = useGasRead(view ? report.pipe_id : null, getPipe);
  const getAnswer = useCallback((signal: AbortSignal) => api.query(entry, {
    observation_id: observation.observation_id, question: submission!.question,
    binding: {
      manifest_sha256: entry.manifest_sha256, threshold_version: point.threshold_version,
      window_start: report.window_start, window_end: report.window_end,
    },
  }, report.pipe_id, signal), [api, entry, observation.observation_id, point.threshold_version, report, submission]);
  const answer = useGasRead(submission ? String(submission.number) : null, getAnswer);
  const events = report.related_events.filter((event) => point.event_ids.includes(event.event_id));
  const thresholds = report.applied_thresholds ?? report.segments.map(({ threshold }) => threshold);
  const clause = citation?.kind === "threshold"
    ? thresholds.flatMap((threshold) => threshold.rule_inputs ?? []).find((rule) => rule.rule_id === citation.record_id)
    : undefined;
  useEffect(() => {
    if (answer.data && submission) {
      const turn = { ...submission, answer: answer.data.answer };
      setTurns((current) => [...current.filter((row) => row.number !== turn.number), turn].slice(-6));
    }
  }, [answer.data, submission]);
  const errorStatus = [answer.error?.status, pipe.error?.status].find((status) => status && accessErrors[status]);
  useEffect(() => {
    if (errorStatus && accessErrors[errorStatus]) onAccessLost(errorStatus);
  }, [errorStatus, onAccessLost]);
  useEffect(() => { if (citation) trace.current?.focus(); }, [citation]);

  function ask(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || answer.status === "loading") return;
    setCitation(null);
    setSubmission((current) => ({ question: question.trim(), number: (current?.number ?? 0) + 1 }));
  }
  function jump(item: PilotCitation) {
    if (item.kind === "asset") setView("pipe");
    if (item.kind === "event" && events.some((event) => event.event_id === item.record_id) && eventDetails.current)
      eventDetails.current.open = true;
    setCitation(item);
  }

  return <section className="gas-monitoring-channel" data-testid="gas-pilot-observation" tabIndex={-1} ref={detail}>
    <h4>观测 <code>{observation.observation_id}</code></h4>
    <p><time dateTime={observation.observed_at}>{observation.observed_at}</time> ·
      {observation.value === null ? " 无读数" : " " + observation.value + " " + observation.unit}
      {" · " + quality[observation.quality] + " · " + classification[point.classification]}</p>
    <p>阈值 <code>{point.threshold_id ?? "暂无适用阈值"}</code>{point.threshold_version && " · 版本 " + point.threshold_version}</p>
    {observation.quality !== "valid" && <p data-testid="gas-pilot-quality-reason">
      {observation.quality_reason ? "源记录登记原因 " + observation.quality_reason : "源记录未提供质量标记原因。"}
    </p>}
    <p><Source row={observation.source} /></p>
    <div className="gas-cuv-links">
      <button type="button" aria-pressed={view === "pipe"} onClick={() => setView("pipe")}>查看关联管段 {report.pipe_id}</button>
      <button type="button" aria-pressed={view === "map"} onClick={() => setView("map")}>打开试点地图</button>
      <button type="button" aria-pressed={view === "topology"} onClick={() => setView("topology")}>定位试点拓扑</button>
      {events.length > 0 && <button type="button" onClick={() => {
        if (eventDetails.current) {
          eventDetails.current.open = true;
          eventDetails.current.querySelector("summary")?.focus();
        }
      }}>打开关联事件证据</button>}
    </div>
    {view && pipe.status === "loading" && <p role="status">正在读取该试点的管段记录。</p>}
    {pipe.error && <GasReadError error={pipe.error} retry={pipe.retry} label="重试读取管段" />}
    {pipe.data && view === "pipe" && <div className="gas-cuv-card" data-testid="gas-pilot-pipe">
      <h5>管段 <code>{pipe.data.pipe.pipe_id}</code></h5>
      <p>{pipe.data.pipe.from_node_id} → {pipe.data.pipe.to_node_id}</p>
      <p>材质 {pipe.data.pipe.material} · 管径 {pipe.data.pipe.diameter_mm} mm · 压力等级 {pipe.data.pipe.pressure_class}</p>
      <p><Source row={pipe.data.pipe.source} /></p>
    </div>}
    {pipe.data && view === "map" && <div className="gas-cuv-card" data-testid="gas-pilot-map">
      {pipe.data.geometry_status === "available" ? <GasPilotGeometry lines={pipe.data.geometries_wgs84} />
        : <p role="status">GIS 未就绪 · {pipe.data.geometry_status === "outside_scope"
          ? "当前许可未开放 GIS 坐标。" : "当前观测时点暂无可显示的授权坐标。"}</p>}
      {pipe.data.geometries_wgs84.map((line) => <p key={line.geometry_id}>
        {line.geometry_id} · {line.source_crs} → EPSG:4326 · <Source row={line.source} />
      </p>)}
    </div>}
    {pipe.data && view === "topology" && <figure className="gas-pilot-topology gas-cuv-card" data-testid="gas-pilot-topology">
      <figcaption>试点拓扑 · 选中管段 <code>{pipe.data.pipe.pipe_id}</code></figcaption>
      <div aria-label="管段两端节点"><code>{pipe.data.pipe.from_node_id}</code><span aria-hidden="true" /><code>{pipe.data.pipe.to_node_id}</code></div>
      <p><Source row={pipe.data.pipe.source} /></p>
    </figure>}
    {events.length > 0 && <details ref={eventDetails} data-testid="gas-pilot-linked-events">
      <summary>关联事件（{events.length}）</summary>
      <ul>{events.map((event) => <li key={event.event_id}><code>{event.event_id}</code> · {event.status} ·
        证据 <code>{event.evidence_ref}</code><br /><Source row={event.source} /></li>)}</ul>
    </details>}
    {qwenAllowed ? <form className="gas-agent-input-form" onSubmit={ask}>
      <p className="gas-window-note" data-testid="gas-pilot-agent-scope">
        试点历史回放 · {entry.dataset_id} · {entry.version}{point.threshold_version && " · 阈值 " + point.threshold_version}
      </p>
      <label>试点只读问答<input value={question} maxLength={1200} onChange={(event) => setQuestion(event.target.value)} /></label>
      <button type="submit" disabled={answer.status === "loading" || !question.trim()}>查询试点证据</button>
    </form> : <p>当前授权用途未开放试点智能体查询。</p>}
    {answer.status === "loading" && <p role="status">正在查询当前数据集的证据。</p>}
    {answer.error && <GasReadError error={answer.error} retry={answer.retry} label="重试证据查询" />}
    {turns.length > 1 && <details data-testid="gas-pilot-turns">
      <summary>本观测的最近 {turns.length} 轮问答</summary>
      {turns.map((turn) => <p key={turn.number}><strong>{turn.question}</strong><br />{turn.answer}</p>)}
    </details>}
    {answer.data && <div className="gas-agent-result-card" data-testid="gas-pilot-agent"
      data-evidence-scope={answer.data.evidence_scope.scope} data-evidence-version={answer.data.evidence_scope.dataset_version}>
      <p data-testid="gas-pilot-model-state" data-model-state={answer.data.model_state}><strong>{
        answer.data.status === "unavailable" ? "超出证据查询范围"
          : answer.data.model_state === "online" ? "Qwen 在线 · " + answer.data.model_id
            : answer.data.model_state === "fallback" ? "规则降级" : "模型不可用 · 规则回答"
      }</strong>{answer.data.fallback_reason && " · " + (answer.data.fallback_reason === "timeout" ? "模型超时" : "模型未返回可核验证据")}</p>
      {answer.data.answer.split("\n").map((sentence) => <p key={sentence}>{sentence}</p>)}
      <ul className="gas-pilot-citations" aria-label="问答证据入口">{answer.data.citations?.map((item) => {
        const labels = { observation: "观测", sensor: "测点", asset: "管段",
          threshold: "阈值版本 " + (item.threshold_version ?? answer.data?.threshold_version), event: "事件" };
        return <li key={item.kind + ":" + item.record_id}>
          <button type="button" onClick={() => jump(item)}>{labels[item.kind]} {item.record_id}</button>
          <button type="button" onClick={() => jump(item)}>来源 {item.source.source_id}</button>
        </li>;
      })}</ul>
      {citation && <div className="gas-cuv-card" ref={trace} tabIndex={-1} data-testid="gas-pilot-qwen-trace">
        <h5>证据记录 <code>{citation.record_id}</code></h5>
        <p>数据集版本 {entry.version}{citation.threshold_version && " · 阈值版本 " + citation.threshold_version}</p>
        {clause && <p>{clause.threshold_type === "lower" ? "下限" : "上限"} {clause.threshold_value} {clause.unit} ·
          生效 {clause.effective_from} 至 {clause.effective_to} · 批准引用 <code>{clause.evidence_reference}</code></p>}
        {!clause && citation.detail && <p>{citation.detail}</p>}
        {citation.kind === "observation" && citation.record_id !== observation.observation_id
          && report.observations.some((row) => row.observation.observation_id === citation.record_id)
          && <button type="button" onClick={() => onSelect(citation.record_id)}>打开这条观测</button>}
        <p><Source row={citation.source} /></p>
      </div>}
    </div>}
    {onOpenCuvDemo && <div className="gas-cuv-card" data-testid="gas-pilot-cuv-handoff">
      <p>后续比较使用 GAS-M0 合成案例的观测和拓扑。确认及审计关联该案例的证据。</p>
      <button type="button" onClick={onOpenCuvDemo}>用 GAS-M0 观测进入 CUV 演示</button>
    </div>}
  </section>;
}


function ReplayValidation({ result }: { result: PilotReplayResult["validation"] }) {
  const scores = result.metrics;
  return <section className="gas-monitoring-channel" data-testid="gas-pilot-validation">
    <h4>{scores?.mode === "synthetic_truth_check" ? "合成真值对照" : scores ? "人工核验历史对照" : "规则一致性检查"}</h4>
    {!scores && <p>{result.reason === "review_use_outside_scope" ? "当前用途仅开放规则回放。" : "当前范围没有完整、可对照的人工真值。"}</p>}
    {scores && <>
      <p>误报 {scores.false_alarms.length} · 漏报 {scores.missed_events} · 恢复检测 {scores.recoveries_detected} / {scores.recoveries_reviewed}</p>
      <details><summary>检测延迟、恢复与真值来源</summary>
        {scores.windows.map((item) => <p key={item.truth_window_id}>{item.start} → {item.end} · <Source row={item.source} /></p>)}
        <ul>{scores.matches.map((item) => <li key={item.truth_event_id}>{item.event_id} ·
          检测延迟 {item.detection_delay_seconds === null ? "未检出" : item.detection_delay_seconds + " s"} ·
          恢复延迟 {item.recovery_delay_seconds === null ? "暂无匹配恢复" : item.recovery_delay_seconds + " s"}
          <br /><Source row={item.truth_source} /></li>)}</ul>
        {scores.false_alarms.map((alarm) => <p key={alarm.at}>未匹配告警 {alarm.at} · 阈值 {alarm.threshold_version}</p>)}
      </details>
    </>}
    <p>连续越限换版去重 {result.duplicate_alarms.length} 条</p>
    {result.duplicate_alarms.map(({ alarm, original }) => <p key={alarm.at}>
      {alarm.at} · 阈值 {alarm.threshold_version} 延续 {original.threshold_version} 已记录的越限
    </p>)}
  </section>;
}

const ROWS_PER_PAGE = 40;
function GasPilotReplayView({ entry, report, api, onAccessLost, dataset, onOpenCuvDemo }: {
  entry: PilotDatasetEntry; report: PilotReplayResult; api: GasPilotApi; onAccessLost: AccessLost; dataset: PilotDataset;
  onOpenCuvDemo?: () => void;
}) {
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const point = report.observations.find((item) => item.observation.observation_id === selected);
  const pageSize = Math.ceil(report.observations.length / ROWS_PER_PAGE);
  const rows = report.observations.slice(page * ROWS_PER_PAGE, (page + 1) * ROWS_PER_PAGE);
  return <section className="gas-pilot-replay" data-testid="gas-pilot-replay">
    <p>回放窗口 {report.window_start ?? "暂无"} 至 {report.window_end ?? "暂无"} · {report.rule_version}</p>
    <GasPilotTrend report={report} selected={selected} onSelect={setSelected} />
    <ReplayValidation result={report.validation} />
    {report.segments.length ? report.segments.map((segment) => <article className="gas-monitoring-channel" key={segment.threshold.threshold_id}>
      <h4>阈值 {segment.threshold.version} · {segment.threshold.threshold_id}</h4>
      <p>下限 {segment.threshold.lower ?? "未设置"} · 上限 {segment.threshold.upper ?? "未设置"} {segment.threshold.unit} ·
        连续 {segment.threshold.sustained_samples} 次越限或 {segment.threshold.missing_samples} 次缺失</p>
      <p>{segment.start} → {segment.end} · 批准引用 <code>{segment.threshold.approval_ref}</code></p>
      <details><summary>阈值来源</summary><p><Source row={segment.threshold.source} /></p></details>
      <ul>{segment.channel.events.map((event) => {
        const trigger = event.evidence.at(-1);
        const triggerPoint = trigger && report.observations.find(({ observation }) =>
          observation.source.source_row === trigger.source_row && observation.source.source_sha256 === trigger.source_sha256
          && observation.observed_at === event.at);
        return <li key={event.kind + ":" + event.at} data-pilot-transition={event.kind}>
          <strong>{transitions[event.kind]}</strong> · <time dateTime={event.at}>{event.at}</time>
          {event.missing_slots.length > 0 && <span> · 缺 {event.missing_slots.length} 个采样时点</span>}
          {triggerPoint && <button type="button" onClick={() => setSelected(triggerPoint.observation.observation_id)}
            aria-label={"打开触发观测 " + triggerPoint.observation.observation_id}>打开触发观测</button>}
        </li>;
      })}</ul>
      {segment.channel.events.length === 0 && <p>该阈值版本下暂无状态变化。</p>}
    </article>) : <p role="status">当前窗口暂无可回放的阈值时段。</p>}
    {point && <GasPilotObservationDetail key={point.observation.observation_id} entry={entry} report={report} point={point} api={api}
      onAccessLost={onAccessLost} qwenAllowed={dataset.access.local_qwen} onSelect={setSelected}
      onOpenCuvDemo={onOpenCuvDemo} />}
    {rows.length > 0 ? <table className="gas-pilot-table"><thead><tr><th>观测</th><th>时点</th><th>原始值</th><th>质量</th><th>判定</th></tr></thead>
      <tbody>{rows.map(({ observation, classification: state }) => <tr key={observation.observation_id}>
        <td><button type="button" aria-pressed={selected === observation.observation_id} onClick={() => setSelected(observation.observation_id)}>
          {observation.observation_id}
        </button></td><td><time dateTime={observation.observed_at}>{observation.observed_at}</time></td>
        <td>{observation.value === null ? "缺失" : observation.value + " " + observation.unit}</td>
        <td>{quality[observation.quality]}</td><td>{classification[state]}</td></tr>)}
      </tbody></table> : <p>当前窗口暂无历史观测。</p>}
    {pageSize > 1 && <div className="gas-cuv-links">
      <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button>
      <span>{page + 1} / {pageSize}</span>
      <button type="button" disabled={page + 1 >= pageSize} onClick={() => setPage(page + 1)}>下一页</button>
    </div>}
  </section>;
}

function GasPilotReplayControls({ entry, dataset, api, onAccessLost, onOpenCuvDemo }: {
  entry: PilotDatasetEntry; dataset: PilotDataset; api: GasPilotApi; onAccessLost: AccessLost;
  onOpenCuvDemo?: () => void;
}) {
  const [sensor, setSensor] = useState(dataset.sensor_records[0]?.sensor_id ?? "");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [selection, setSelection] = useState<ReplaySelection | null>(sensor ? { sensor_id: sensor } : null);
  const query = useCallback((signal: AbortSignal) => api.replay(entry, selection!, signal), [api, entry, selection]);
  const view = useGasRead(selection ? JSON.stringify(selection) : null, query);
  useEffect(() => {
    if (view.error?.status && accessErrors[view.error.status]) onAccessLost(view.error.status);
  }, [view.error, onAccessLost]);
  const incomplete = Boolean(start.trim()) !== Boolean(end.trim());
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!sensor || incomplete) return;
    setSelection({ sensor_id: sensor, ...(start.trim() && end.trim() ? { start: start.trim(), end: end.trim() } : {}) });
  }
  return <>
    <form className="gas-agent-input-form" onSubmit={submit}>
      <label>试点测点<select value={sensor} onChange={(event) => {
        setSensor(event.target.value);
        setSelection(null);
      }}>{dataset.sensor_records.map((record) => <option key={record.sensor_id} value={record.sensor_id}>
        {record.sensor_id} · {gasMonitoringMetricLabel[record.metric]} · {record.unit}
      </option>)}</select></label>
      <div className="gas-pilot-time">
        <label>回放起点 (ISO 8601)<input value={start} onChange={(event) => setStart(event.target.value)} placeholder="2026-11-18T01:00:00Z" /></label>
        <label>回放终点 (ISO 8601)<input value={end} onChange={(event) => setEnd(event.target.value)} placeholder="2026-11-18T02:00:00Z" /></label>
      </div>
      {incomplete && <p role="status">请同时填写起点和终点，或留空使用最后一小时。</p>}
      <button type="submit" disabled={!sensor || incomplete || view.status === "loading"}>读取历史回放</button>
    </form>
    {view.status === "loading" && <p role="status">正在读取该测点的历史回放。</p>}
    {view.error && <GasReadError error={view.error} retry={view.retry} label="重试历史回放" />}
    {view.data && <GasPilotReplayView key={view.key} entry={entry} report={view.data} api={api} dataset={dataset}
      onAccessLost={onAccessLost} onOpenCuvDemo={onOpenCuvDemo} />}
  </>;
}

export function GasPilotPanel({ api, onOpenCuvDemo }: { api: GasPilotApi; onOpenCuvDemo?: () => void }) {
  const catalogue = useGasRead("gas-pilot-catalogue", useCallback((signal) => api.catalogue(signal), [api]));
  const [selected, setSelected] = useState("");
  const [accessNotice, setAccessNotice] = useState("");
  const refresh = catalogue.retry;
  const onAccessLost = useCallback<AccessLost>((status) => {
    setSelected("");
    setAccessNotice(accessErrors[status]);
    refresh();
  }, [refresh]);
  const entry = catalogue.data?.items.find((item) => pilotVersionKey(item) === selected);
  const getDataset = useCallback((signal: AbortSignal) => api.dataset(entry!, signal), [api, entry]);
  const dataset = useGasRead(entry ? selected : null, getDataset);
  useEffect(() => {
    if (dataset.error?.status && accessErrors[dataset.error.status]) onAccessLost(dataset.error.status);
  }, [dataset.error, onAccessLost]);
  return <section className="gas-monitoring-panel" data-testid="gas-pilot-panel">
    <h3>试点历史回放</h3>
    {accessNotice && <p role="alert" data-testid="gas-pilot-scope-notice">{accessNotice}</p>}
    {catalogue.status === "loading" && <p role="status">正在核对可见数据集。</p>}
    {catalogue.error && <GasReadError error={catalogue.error} retry={catalogue.retry} label="重试试点目录" />}
    {catalogue.data && !catalogue.data.configured && <p>当前服务未配置试点库。</p>}
    {catalogue.data?.configured && catalogue.data.items.length === 0 && <p>当前主体没有可用试点版本。</p>}
    {!!catalogue.data?.items.length && <label className="gas-agent-input-form">选择试点数据集
      <select value={selected} onChange={(event) => {
        setAccessNotice("");
        setSelected(event.target.value);
      }}>
        <option value="">选择数据集与版本</option>
        {catalogue.data.items.map((item) => <option key={pilotVersionKey(item)} value={pilotVersionKey(item)}>
          {item.dataset_id} · {item.version} · {item.data_mode}{item.preferred ? " · 当前推荐版本" : ""}
        </option>)}
      </select>
    </label>}
    <GasPilotRejections items={catalogue.data?.rejections ?? []} />
    {dataset.error && <GasReadError error={dataset.error} retry={dataset.retry} label="重试试点版本" />}
    {entry && dataset.data && <>
      <p data-testid="gas-pilot-data-mode"><strong>{entry.data_mode === "synthetic" ? "合成测试数据" : "授权历史数据"}</strong>
        {" · " + entry.data_mode + " · " + (entry.quality_status === "partial" ? "有质量缺项" : "结构校验通过")}</p>
      <p><code>{entry.dataset_id}</code> · 版本 {entry.version} · {entry.pipes} 条管段 · {entry.sensors} 个测点 · {entry.observations} 条观测</p>
      <GasPilotAccess access={dataset.data.access} />
      <GasPilotQuality report={dataset.data.quality} />
      <p className="gas-muted">工程水力计算、泄漏风险和复气决策不可用。</p>
      <GasPilotReplayControls key={selected} entry={entry} dataset={dataset.data} api={api} onAccessLost={onAccessLost}
        onOpenCuvDemo={entry.data_mode === "synthetic" ? onOpenCuvDemo : undefined} />
    </>}
  </section>;
}
