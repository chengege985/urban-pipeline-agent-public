import { describe, expect, it } from "vitest";
import { PUBLIC_DATASET_OPTIONS, PublicGasDatasetApi } from "../features/gas/publicDatasets";
import type { GasTransport } from "../features/gas/engineeringApi";

class FakeTransport implements GasTransport {
  paths: string[] = [];
  constructor(private readonly body: unknown, private readonly status = 200) {}
  async send(request: { method: "GET" | "POST"; path: string }) {
    this.paths.push(request.path);
    return { status: this.status, body: this.body };
  }
}

describe("explicit public gas dataset contracts", () => {
  it("passes NGN view filters and rejects history from another source", async () => {
    const assets = new FakeTransport({ dataset_id: "ngn_the_north", title: "NGN", data_mode: "official_public", status: "ready" });
    await new PublicGasDatasetApi(assets).overview("ngn_the_north", {
      bbox: "-3,54,-2,55", search: "12", offset: 120, ngnFilters: { material: "PE", pressure: "MP", diameter_mm: "63" },
    });
    const url = new URL(assets.paths[0], "http://localhost");
    expect(Object.fromEntries(url.searchParams)).toMatchObject({ bbox: "-3,54,-2,55", search: "12", offset: "120", material: "PE", pressure: "MP", diameter_mm: "63" });
    const history = new FakeTransport({ dataset_id: "aer_alberta", data_version: "v", kind: "emergency", status: "ready", total: 0, items: [], monthly: [] });
    await expect(new PublicGasDatasetApi(history).ngnEvents({ kind: "emergency", start: "2020-01-01" })).rejects.toThrow("不符合数据集合同");
    expect(history.paths[0]).toContain("start=2020-01-01");
  });

  it("exposes public and research sources and keeps the synthetic source separate", () => {
    expect(PUBLIC_DATASET_OPTIONS.map((item) => item.id)).toEqual([
      "charlottesville", "schutterwald", "sicily_transmission", "aer_alberta", "sgn_eastbourne", "ngn_distribution_example", "ngn_the_north", "gaslib_582", "merrimack_2018", "gas_m0_synthetic",
    ]);
  });

  it("uses POST for Sicily execution and rejects a response from another hour", async () => {
    const send = async (request: Parameters<GasTransport["send"]>[0]) => {
      expect(request.method).toBe("POST");
      const url = new URL(request.path, "http://localhost");
      expect(url.searchParams.get("case_id")).toBe("20251115-12");
      return { status: 200, body: { dataset_id: "sicily_transmission", case_id: "20251115-13", edge_id: "36",
        data_version: "v", execution_id: "e", hydraulics: { status: "ok" }, unreachable_nodes: [] } };
    };
    await expect(new PublicGasDatasetApi({ send }).sicilyRun("20251115-12", "36", "v")).rejects.toThrow("不符合数据集合同");
  });

  it("scopes NGN sample runs to the requested pipe and period", async () => {
    const send = async (request: Parameters<GasTransport["send"]>[0]) => {
      expect(request.method).toBe("POST");
      expect(request.path).toContain("/ngn_distribution_example/run?");
      expect(request.path).toContain("edge_id=LINE_10077");
      return { status: 200, body: { dataset_id: "ngn_distribution_example", data_version: "v",
        task: { period: "2", edge_id: "LINE_10077", trend: false }, execution_id: "e",
        input_sha256: {}, stable_result_sha256: "hash", status: "not_estimable", reason: "no result" } };
    };
    await expect(new PublicGasDatasetApi({ send }).ngnSampleRun("v", "1", "LINE_10077", false))
      .rejects.toThrow("不符合数据集合同");
  });

  it("loads a source-specific overview only under its own dataset id", async () => {
    const transport = new FakeTransport({ dataset_id: "merrimack_2018", title: "NTSB case", data_mode: "official_case_evidence", status: "ready" });
    const api = new PublicGasDatasetApi(transport);
    const result = await api.overview("merrimack_2018");
    expect(result.dataset_id).toBe("merrimack_2018");
    expect(transport.paths).toEqual(["/api/v1/gas/public-data/merrimack_2018/overview?limit=120&offset=0"]);
  });

  it("rejects a response belonging to a different source instead of mixing it", async () => {
    const transport = new FakeTransport({ dataset_id: "gas_m0_synthetic", title: "wrong source", data_mode: "synthetic", status: "ready" });
    const api = new PublicGasDatasetApi(transport);
    await expect(api.overview("aer_alberta")).rejects.toThrow("不符合数据集合同");
  });

  it("does not silently use another dataset when the source endpoint is unavailable", async () => {
    const transport = new FakeTransport({ detail: "source offline" }, 503);
    const api = new PublicGasDatasetApi(transport);
    await expect(api.overview("sgn_eastbourne")).rejects.toThrow();
    expect(transport.paths[0]).toContain("sgn_eastbourne");
  });

  it("sends AER scope, search, and page as source query parameters", async () => {
    const transport = new FakeTransport({ dataset_id: "aer_alberta", title: "AER", data_mode: "official_public", status: "ready" });
    const api = new PublicGasDatasetApi(transport);
    await api.overview("aer_alberta", { fieldCentre: "Calgary", search: "pipe-7", offset: 120 });
    expect(transport.paths[0]).toContain("field_centre=Calgary");
    expect(transport.paths[0]).toContain("search=pipe-7");
    expect(transport.paths[0]).toContain("offset=120");
  });

  it("queries AER event pages without changing dataset identity", async () => {
    const transport = new FakeTransport({ status: "ready", total: 1, offset: 30, limit: 30, items: [] });
    const api = new PublicGasDatasetApi(transport);
    const result = await api.aerEvents({ pipeId: "pipe-7", linkStatus: "linked_unique_gas_segment", offset: 30 });
    expect(result.offset).toBe(30);
    expect(transport.paths[0]).toContain("pipe_id=pipe-7");
    expect(transport.paths[0]).toContain("link_status=linked_unique_gas_segment");
  });
});
