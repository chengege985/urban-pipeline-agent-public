import { describe, expect, it } from "vitest";
import { parseProductEdition } from "../shared/config/productEdition";
import { parseRoute, routeForEdition } from "../hooks/useHashRoute";
import { gasEngineeringPath, parseGasHash, PROJECT_GAS_MAP } from "../shared/routing/gasRoutes";

describe("one source, two product route scopes", () => {
  it("defaults to the gas project and rejects unknown build identities", () => {
    expect(parseProductEdition(undefined)).toBe("project");
    expect(parseProductEdition("competition")).toBe("competition");
    expect(() => parseProductEdition("all")).toThrow();
  });
  it("restores the existing explicit water overview and competition landing", () => {
    expect(parseRoute("#/overview")).toEqual({ view: "overview" });
    expect(routeForEdition("", "competition")).toEqual({ view: "overview" });
    expect(routeForEdition("#/water", "competition")).toEqual({ view: "overview" });
  });
  it.each(["#/overview", "#/pipe/WP-016", "#/scenario/SC-HOSP?pipe=WP-016", "#/assistant", "#/report/run-id", "#/about"])(
    "project blocks water route %s before any water provider mounts", hash => {
      expect(routeForEdition(hash, "project")).toMatchObject({ view: "gas", assetPage: 1 });
      expect(routeForEdition(hash, "competition").view).not.toBe("gas");
    });
  it.each(["#/gas", "#/gas-control-room", "#/gas-engineering?tab=assets&page=5"])(
    "both editions preserve gas route %s", hash => {
      expect(routeForEdition(hash, "project")).toEqual(routeForEdition(hash, "competition"));
    });
  it("preserves encoded asset, event, catalogue and paging state", () => {
    const hash = "#/gas/events/e%2F1/assets/a%2F2?page=5&catalogue=open";
    expect(routeForEdition(hash, "competition")).toEqual(parseRoute(hash));
    expect(routeForEdition(hash, "project")).toEqual(parseRoute(hash));
  });
  it("query strings cannot enable project water pages", () => {
    expect(routeForEdition("#/overview?edition=competition&mode=live", "project").view).toBe("gas");
  });
});

describe("the engineering entry has a gas-only route parser", () => {
  it.each(["", "#/overview", "#/dashboard", "#/report/old?edition=competition", "#/pipe/WP-016"])(
    "maps retired application paths to the cockpit %s", hash => {
      expect(gasEngineeringPath(parseGasHash(hash))).toBe("/gas");
    });
  it("preserves and canonicalizes legacy gas deep links", () => {
    const route = parseGasHash("#/gas-engineering/events/e%2F1/assets/a%2F2?page=5&map=gis");
    expect(route).toMatchObject({ gasEventId: "e/1", gasAssetId: "a/2", assetPage: 5, gasMap: "gis" });
    expect(gasEngineeringPath(route)).toBe("/gas/events/e%2F1/assets/a%2F2?page=5&map=gis");
  });
  it("keeps explicit errors for invalid gas details", () => {
    expect(parseGasHash("#/gas/events/%E0%A4%A").gasRouteError).toBeTruthy();
  });
});

it("opens the project hash in GIS and keeps an explicit topology selection on reload", () => {
  expect(parseGasHash("#/gas", PROJECT_GAS_MAP).gasMap).toBe("gis");
  const topology = gasEngineeringPath({ gasMap: "topology" }, PROJECT_GAS_MAP);
  expect(topology).toBe("/gas?map=topology");
  expect(parseGasHash("#" + topology, PROJECT_GAS_MAP).gasMap).toBe("topology");
  expect(parseGasHash("#/gas").gasMap).toBe("topology");
});
