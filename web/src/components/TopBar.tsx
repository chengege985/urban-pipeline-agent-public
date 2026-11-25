import type { ProviderMeta, ProviderMode } from "../domain/types";
import type { AppRoute, AppView } from "../hooks/useHashRoute";
import { AssistantIcon, InfoIcon, LogoIcon, OverviewIcon, PipeIcon, ScenarioIcon } from "./Icons";
import { ModePill } from "./StatusPill";

const NAV: Array<{ view: AppView; label: string; icon: typeof OverviewIcon }> = [
  { view: "overview", label: "城市总览", icon: OverviewIcon },
  { view: "pipe", label: "管段分析", icon: PipeIcon },
  { view: "scenario", label: "情景模拟", icon: ScenarioIcon },
  { view: "assistant", label: "AI 助手", icon: AssistantIcon },
  { view: "gas", label: "燃气中控", icon: PipeIcon },
  { view: "about", label: "关于", icon: InfoIcon },
];

interface TopBarProps {
  route: AppRoute;
  mode: ProviderMode;
  meta?: ProviderMeta;
  onNavigate: (path: string) => void;
}

export function TopBar({ route, mode, meta, onNavigate }: TopBarProps) {
  const activeView: AppView = route.view === "report" ? "scenario" : route.view;
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <button className="brand" onClick={() => onNavigate("/overview")} aria-label="返回城市总览">
          <span className="brand-mark">
            <LogoIcon size={22} />
          </span>
          <span className="brand-copy">
            <strong>水脉守护 <em>AI</em></strong>
            <small>{route.view === "gas" || route.view === "gas-engineering" ? "WaterPulse · 燃气合成候选" : "赛题3 · 供水中控"}</small>
          </span>
        </button>
        <nav className="main-nav" aria-label="主导航">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = activeView === item.view;
            return (
              <button
                className={`nav-item ${active ? "active" : ""}`}
                key={item.view}
                onClick={() => onNavigate(item.view === "overview" ? "/overview" : `/${item.view}`)}
                aria-current={active ? "page" : undefined}
              >
                <Icon size={16} />
                {item.label}
              </button>
            );
          })}
        </nav>
        {meta ? <div className="topbar-meta">
          <ModePill mode={mode} />
          <span className="data-version">{meta.dataVersion}</span>
        </div> : null}
      </div>
    </header>
  );
}
