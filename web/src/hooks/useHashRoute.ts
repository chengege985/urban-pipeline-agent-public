import { useCallback, useEffect, useState } from "react";
import { PRODUCT_EDITION, type ProductEdition } from "../shared/config/productEdition";
import { gasEngineeringPath, parseGasEngineeringRoute, type GasEngineeringRoute } from "../shared/routing/gasRoutes";

export type AppView = "overview" | "pipe" | "scenario" | "assistant" | "report" | "gas" | "gas-engineering" | "about";

export interface AppRoute extends Partial<GasEngineeringRoute> {
  view: AppView;
  pipeId?: string;
  scenarioId?: string;
  sessionId?: string;
}

export function parseRoute(hash: string, defaultView: "gas" | "overview" = "gas"): AppRoute {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart, queryPart] = raw.split("?");
  const segments = pathPart.split("/").filter(Boolean);
  const params = new URLSearchParams(queryPart ?? "");
  const view = (segments[0] ?? defaultView) as AppView;
  if ((view === "overview" || segments[0] === "water") && segments.length === 1) return { view: "overview" };
  if (view === "pipe") return { view, pipeId: segments[1] };
  if (view === "scenario") {
    return { view, scenarioId: segments[1], pipeId: params.get("pipe") ?? undefined };
  }
  if (view === "assistant") {
    return {
      view,
      pipeId: params.get("pipe") ?? undefined,
      scenarioId: params.get("scenario") ?? undefined,
    };
  }
  if (view === "report") return { view, sessionId: segments[1] };
  if (["gas", "gas-engineering", "gas-control-room"].includes(view)) {
    return { view: "gas", ...parseGasEngineeringRoute(segments.slice(1), params) };
  }
  if (view === "about") return { view };
  return defaultView === "overview" ? { view: "overview" } : { view: "gas", ...parseGasEngineeringRoute([], new URLSearchParams()) };
}

export function isGasRoute(route: Pick<AppRoute, "view">): boolean {
  return route.view === "gas" || route.view === "gas-engineering";
}

export function routeForEdition(hash: string, edition: ProductEdition): AppRoute {
  const route = parseRoute(hash, edition === "competition" ? "overview" : "gas");
  // Project builds cannot mount the water workspace, including direct/bookmarked URLs.
  return edition === "project" && !isGasRoute(route) ? parseRoute("#/gas") : route;
}

export function useHashRoute() {
  const [route, setRoute] = useState<AppRoute>(() => routeForEdition(window.location.hash, PRODUCT_EDITION));

  useEffect(() => {
    const onChange = () => {
      const next = routeForEdition(window.location.hash, PRODUCT_EDITION);
      if (next.view === "gas" && !next.gasRouteError) {
        const canonical = "#" + gasEngineeringPath(next);
        if (window.location.hash !== canonical) window.history.replaceState(null, "", window.location.pathname + window.location.search + canonical);
      }
      if (next.view === "overview" && !/^#\/?overview(?:[/?]|$)/.test(window.location.hash)) {
        window.history.replaceState(null, "", window.location.pathname + window.location.search + "#/overview");
      }
      setRoute(next);
    };
    onChange();
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((path: string, replace = false) => {
    const hash = path.startsWith("/") ? `#${path}` : `#/${path}`;
    if (replace) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search + hash);
      setRoute(routeForEdition(hash, PRODUCT_EDITION));
    } else {
      window.location.hash = hash;
    }
    window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, []);

  return { route, navigate };
}
