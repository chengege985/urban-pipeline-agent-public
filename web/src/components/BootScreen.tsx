import { LogoIcon } from "./Icons";

export function BootScreen() {
  return (
    <main className="boot-screen">
      <div className="boot-mark">
        <LogoIcon size={30} />
      </div>
      <p className="eyebrow">WaterPulse AI</p>
      <h1>正在准备合成演示数据</h1>
      <p>加载虚拟管网、DemoHealth 规则与情景数据，不访问任何真实系统。</p>
      <span className="loading-bar" aria-label="加载中" />
    </main>
  );
}

export function UnavailableScreen({
  message,
  onRetry,
  onUseDemo,
}: {
  message: string;
  onRetry: () => void;
  onUseDemo: () => void;
}) {
  return (
    <main className="unavailable-screen">
      <div className="unavailable-card">
        <p className="eyebrow">Live mode unavailable</p>
        <h1>实时接口暂不可用</h1>
        <p>{message}</p>
        <p className="muted">
          当前不会显示任何伪造的实时数据。你可以重试实时接口，或切换到完全离线的合成演示模式。
        </p>
        <div className="button-row">
          <button className="button-primary" onClick={onRetry}>重试实时接口</button>
          <button className="button-ghost" onClick={onUseDemo}>使用演示数据</button>
        </div>
        <small>live 模式只有在接口探测成功并通过能力检查后才会启用。</small>
      </div>
    </main>
  );
}
