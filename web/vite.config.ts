import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const edition = mode === "competition" ? "competition" : "project";
  const gasDemo = edition === "project" && (mode === "gas-demo" ||
    loadEnv(mode, ".", "VITE_WPN_GAS_DEMO").VITE_WPN_GAS_DEMO === "1");
  const title = edition === "competition" ? "水脉守护 AI · 赛题3" : "重庆燃气智能监管 · 项目演示";
  return {
    plugins: [react(), {
      name: "product-edition",
      generateBundle(_options, bundle) {
        const modules = Object.values(bundle).flatMap(item => item.type === "chunk"
          ? Object.entries(item.modules).filter(([, info]) => info.renderedLength > 0).map(([id]) => id.replace(/\\/g, "/")) : []);
        const waterPaths = ["pages/", "features/assistant/", "features/cuv/", "features/network/", "features/reports/",
          "data/", "demo.", "engine/", "storage/", "adapters/", "hooks/useDataSource", "app/CompetitionApp",
          "providers/demoProvider", "providers/apiProvider", "providers/platformProvider", "providers/index"];
        const waterModules = modules.filter(id => waterPaths.some(path => id.includes("/src/" + path)));
        if (edition === "project" && waterModules.length) {
          this.error("Water runtime modules entered the gas-only build: " + waterModules.join(", "));
        }
        this.emitFile({ type: "asset", fileName: "frontend-product.json", source: JSON.stringify({
          edition, defaultRoute: edition === "competition" ? "#/overview" : "#/gas",
          domains: edition === "competition" ? ["water", "gas"] : ["gas"],
          publisher: edition === "competition" ? "Qoder" : "Codex",
          waterRuntimeModuleCount: waterModules.length,
          dataPolicy: "Explicit provider identity; API authentication and ACL are server-enforced.",
        }, null, 2) });
      },
      transformIndexHtml(html) {
        const titled = html.replace(/<title>.*?<\/title>/, "<title>" + title + "</title>");
        if (edition === "competition") return titled;
        const flame = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#07131e"/><path d="M35 6C40 20 53 26 53 40a21 21 0 0 1-42 0c0-9 6-17 11-23 0 10 3 12 6 14 5-8 8-16 7-25Z" fill="#4d94ff"/><path d="M33 34c3 7 9 10 9 16a10 10 0 0 1-20 0c0-5 5-10 11-16Z" fill="#eaf1f7"/></svg>';
        return titled.replace(/<link rel="icon"[^>]*>/, '<link rel="icon" href="data:image/svg+xml,' + encodeURIComponent(flame) + '" />');
      },
    }],
    define: {
      "import.meta.env.VITE_WPN_EDITION": JSON.stringify(edition),
      "import.meta.env.VITE_WPN_GAS_DEMO": gasDemo ? JSON.stringify("1") : "undefined",
    },
    base: "./",
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            const normalized = id.replace(/\\/g, "/");
            if (normalized.includes("/node_modules/react/") || normalized.includes("/node_modules/react-dom/") || normalized.includes("/node_modules/scheduler/")) return "react-vendor";
            if (normalized.includes("/src/features/gas/")) return "gas-cockpit";
            if (normalized.includes("/src/pages/") || normalized.includes("/src/components/") || normalized.includes("/src/providers/")) {
              return edition === "project" ? "shared-transport" : "platform-legacy";
            }
            return undefined;
          },
        },
      },
    },
  };
});
