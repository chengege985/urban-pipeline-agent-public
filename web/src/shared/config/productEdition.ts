/** Build-time product scope, never inferred from query parameters or stored preferences.
 * This selects the UI only; API authentication and dataset ACL remain server-owned.
 */
export type ProductEdition = "competition" | "project";

export function parseProductEdition(value: string | undefined): ProductEdition {
  if (value === undefined || value === "project") return "project";
  if (value === "competition") return "competition";
  throw new Error("Unsupported frontend edition");
}

export const PRODUCT_EDITION: ProductEdition = import.meta.env.VITE_WPN_EDITION === "competition" ? "competition" : "project";
export const PRODUCT = PRODUCT_EDITION === "competition"
  ? { label: "赛题3 · 水脉守护 AI", title: "水脉守护 AI · 赛题3", defaultView: "overview" as const }
  : { label: "燃气项目演示", title: "重庆燃气智能监管 · 项目演示", defaultView: "gas" as const };
