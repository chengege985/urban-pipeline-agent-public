import { lazy, Suspense } from "react";

// Vite replaces the literal condition before Rollup builds the module graph.
// Project assets never include the competition/water application.
const ProductApp = import.meta.env.VITE_WPN_EDITION === "competition"
  ? lazy(() => import("./app/CompetitionApp"))
  : lazy(() => import("./app/GasProjectApp"));

export default function App() {
  return <Suspense fallback={<p role="status">正在加载…</p>}><ProductApp /></Suspense>;
}
