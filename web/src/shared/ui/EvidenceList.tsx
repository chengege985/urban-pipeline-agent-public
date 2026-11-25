const KIND_LABEL: Record<string, string> = {
  rule: "演示规则",
  data: "合成数据",
  scenario: "情景",
  plan: "方案",
  service: "服务",
  pipe: "管段",
};

export function EvidenceList({ ids }: { ids: string[] }) {
  if (ids.length === 0) return <p className="muted small">暂无可引用证据。</p>;
  return (
    <div className="evidence-list">
      {ids.map((id) => {
        const [kind] = id.split(":");
        return (
          <span className="evidence-chip" key={id} title={id}>
            <b>{KIND_LABEL[kind] ?? "证据"}</b>
            {id}
          </span>
        );
      })}
    </div>
  );
}
