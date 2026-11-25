import { useCallback, useEffect, useState } from "react";
import { gasEngineeringPath, parseGasHash, PROJECT_GAS_MAP } from "../shared/routing/gasRoutes";

export function useGasHashRoute() {
  const [route, setRoute] = useState(() => parseGasHash(window.location.hash, PROJECT_GAS_MAP));

  useEffect(() => {
    const onHashChange = () => {
      const next = parseGasHash(window.location.hash, PROJECT_GAS_MAP);
      if (!next.gasRouteError) {
        const canonical = "#" + gasEngineeringPath(next, PROJECT_GAS_MAP);
        if (window.location.hash !== canonical) window.history.replaceState(null, "", canonical);
      }
      setRoute(next);
    };
    onHashChange();
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = useCallback((path: string, replace = false) => {
    const hash = path.startsWith("/") ? `#${path}` : `#/${path}`;
    const next = parseGasHash(hash, PROJECT_GAS_MAP);
    const canonical = next.gasRouteError ? hash : "#" + gasEngineeringPath(next, PROJECT_GAS_MAP);
    if (replace) {
      window.history.replaceState(null, "", canonical);
      setRoute(next);
    } else {
      window.location.hash = canonical;
    }
    window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, []);

  return { route, navigate };
}
