import { useMemo, useState } from "react";
import type { AnalysisContext } from "../domain/types";
import { AboutPage } from "../pages/AboutPage";
import { AssistantPage } from "../pages/AssistantPage";
import { OverviewPage } from "../pages/OverviewPage";
import { PipePage } from "../pages/PipePage";
import { ReportPage } from "../pages/ReportPage";
import { ScenarioPage } from "../pages/ScenarioPage";
import { AppFooter } from "../components/AppFooter";
import { BootScreen, UnavailableScreen } from "../components/BootScreen";
import { PlatformReportPage } from "../pages/PlatformReportPage";
import { TopBar } from "../components/TopBar";
import { useDataSource, platformBaseUrl } from "../hooks/useDataSource";
import { PlatformProvider } from "../providers";
import { FetchTransport } from "../providers/platform/transport";
import { isGasRoute, useHashRoute, type AppRoute } from "../hooks/useHashRoute";
import { GasSurface } from "./GasSurface";

export default function CompetitionApp() {
  const { route, navigate } = useHashRoute();
  return isGasRoute(route) ? <GasSurface route={route} navigate={navigate} />
    : <WaterWorkspace route={route} navigate={navigate} />;
}

function WaterWorkspace({ route, navigate }: { route: AppRoute; navigate(path: string): void }) {
  const data = useDataSource(route.view === "report", isGasRoute(route));
  const reportRoute = route.view === "report" && data.requestedMode === "platform";
  const reportProvider = useMemo(() => new PlatformProvider({
    transport: new FetchTransport(platformBaseUrl()), datasetId: "", reportEnabled: true,
  }), []);
  const [analysisContext, setAnalysisContext] = useState<AnalysisContext | null>(null);

  // Report owns its snapshot metadata and authorization; never bootstrap another dataset first.
  // Use the effective mode, including an explicit offline selection, for report routing.
  if (reportRoute) return (
    <div className="app-shell" data-product-edition="competition">
      <TopBar route={route} mode="live" onNavigate={navigate} />
      <div className="mode-banner mode-live"><strong>平台运行报告</strong><span>按运行编号读取已保存结果；数据来源、版本及状态以报告为准。</span></div>
      <main className="app-main"><PlatformReportPage key={route.sessionId ?? "report-entry"}
        provider={reportProvider} runId={route.sessionId} onNavigate={navigate} /></main>
      <footer className="site-footer"><strong>水脉守护 AI · WaterPulse</strong><span>报告只读；不代表已审批、派单或关阀。</span></footer>
    </div>
  );
  if (data.loading) return <BootScreen />;
  if (data.mode === "unavailable" || !data.dataset) {
    return (
      <UnavailableScreen
        message={data.error ?? "实时数据源不可用。"}
        onRetry={data.retry}
        onUseDemo={data.switchToDemo}
      />
    );
  }

  const dataset = data.dataset;
  const platformMode = dataset.meta.providerKind === "platform";
  const page = (() => {
    switch (route.view) {
      case "pipe":
        return <PipePage dataset={dataset} pipeId={route.pipeId} onNavigate={navigate} />;
      case "scenario":
        return (
          <ScenarioPage
            dataset={dataset}
            provider={data.provider}
            scenarioId={route.scenarioId}
            pipeId={route.pipeId}
            context={analysisContext}
            onContextChange={setAnalysisContext}
            onNavigate={navigate}
          />
        );
      case "assistant":
        return (
          <AssistantPage
            dataset={dataset}
            provider={data.provider}
            pipeId={route.pipeId}
            scenarioId={route.scenarioId}
            context={analysisContext}
            onNavigate={navigate}
          />
        );
      case "report":
        return platformMode ? (
          <PlatformReportPage key={route.sessionId ?? "report-entry"} provider={data.provider}
            runId={route.sessionId} onNavigate={navigate} />
        ) : (
          <ReportPage dataset={dataset} sessionId={route.sessionId} onNavigate={navigate} />
        );
      case "about":
        return <AboutPage dataset={dataset} />;
      case "overview":
      default:
        return <OverviewPage dataset={dataset} onNavigate={navigate} />;
    }
  })();

  return (
    <div className="app-shell" data-product-edition="competition">
      <TopBar route={route} mode={data.mode} meta={dataset.meta} onNavigate={navigate} />
      <div className={`mode-banner mode-${data.mode}`}>
        <span className="mode-dot" />
        <strong>{platformMode ? "统一平台接口已接通" : data.mode === "live" ? "实时接口已接通" : "合成演示模式"}</strong>
        <span>
          {platformMode
            ? "合成数据；分析与报告读取统一平台服务端结果。"
            : data.mode === "live"
              ? "仅展示合成数据；实时能力仍需独立验收。"
              : "不连接真实设施、企业内部 API 或生产数据库；所有数字来自 DemoHealth 演示规则。"}
        </span>
        <em>{dataset.meta.modelVersion}</em>
      </div>
      <main className="app-main">{page}</main>
      <AppFooter meta={dataset.meta} />
    </div>
  );
}
