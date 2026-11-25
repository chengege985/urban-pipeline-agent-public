import { GasCockpitProvider } from "./GasCockpitProvider";
import { GasCockpit } from "./GasCockpit";
import type { GasEngineeringRoute } from "../../shared/routing/gasRoutes";

// The sole gas container. Compatibility routes are normalized before reaching it.
export function GasPage({ baseUrl, onNavigate, route }: { baseUrl: string; onNavigate(path: string, replace?: boolean): void; route?: GasEngineeringRoute }) {
  return <GasCockpitProvider baseUrl={baseUrl} route={route} onNavigate={onNavigate}><GasCockpit onNavigate={onNavigate} /></GasCockpitProvider>;
}
