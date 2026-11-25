import { GasPage } from "../features/gas/GasPage";
import type { AppRoute } from "../hooks/useHashRoute";
import { PRODUCT_EDITION } from "../shared/config/productEdition";

/** Shared gas surface; no water data provider is imported by this entry. */
export function GasSurface({ route, navigate }: { route: AppRoute; navigate(path: string, replace?: boolean): void }) {
  return <div className="app-shell gas-app-shell" data-product-edition={PRODUCT_EDITION} data-gas-demo={import.meta.env.VITE_WPN_GAS_DEMO === "1"}>
    <GasPage baseUrl={import.meta.env.VITE_WATERPULSE_PLATFORM_URL ?? ""} onNavigate={navigate} route={{
      gasEventId: route.gasEventId, gasAssetId: route.gasAssetId, gasObservation: route.gasObservation, assetPage: route.assetPage ?? 1,
      assetType: route.assetType, gasSection: route.gasSection ?? "events", gasRouteError: route.gasRouteError,
      gasEventKind: route.gasEventKind, gasEventState: route.gasEventState, gasMode: route.gasMode, gasLayout: route.gasLayout,
      gasMap: route.gasMap, gasCatalogueOpen: route.gasCatalogueOpen, gasDistrictId: route.gasDistrictId, gasSubnetId: route.gasSubnetId,
    }} />
  </div>;
}
