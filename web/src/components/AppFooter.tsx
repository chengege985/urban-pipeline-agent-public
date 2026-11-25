import type { ProviderMeta } from "../domain/types";

export function AppFooter({ meta }: { meta: ProviderMeta }) {
  return (
    <footer className="site-footer">
      <div>
        <strong>水脉守护 AI · WaterPulse</strong>
        <p>{meta.disclaimer}</p>
      </div>
      <div className="footer-meta">
        <span>演示模型 {meta.modelVersion}</span>
        <span>数据 {meta.dataVersion}</span>
        <span>应用 {meta.appVersion}</span>
        <span>不控制真实设施 · 不连接企业内部接口</span>
      </div>
    </footer>
  );
}
