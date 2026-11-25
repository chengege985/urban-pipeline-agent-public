import type { AgentToolCall } from "../../../shared/contracts/domain";

export function ToolCallCard({ call }: { call: AgentToolCall }) {
  const statusLabel =
    call.status === "succeeded"
      ? "已执行"
      : call.status === "partial"
        ? "部分结果"
        : call.status === "refused"
          ? "已拒绝"
          : "信息不足";
  return (
    <div className={`tool-card tool-${call.status}`}>
      <div className="tool-card-head">
        <span className="tool-name">{call.tool}</span>
        <span className="tool-status">{statusLabel}</span>
      </div>
      <strong>{call.label}</strong>
      <p>{call.summary}</p>
      {call.detail ? <small>{call.detail}</small> : null}
    </div>
  );
}
