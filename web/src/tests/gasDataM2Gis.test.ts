import { describe, expect, it, vi } from "vitest";
import { readAmapBoundary, type AmapSdk } from "../features/gas/amapLoader";

const boundaries = [[[106.1, 29.2], [106.5, 29.3], [106.8, 29.6]]];

function sdkWithDistrict(delay: number, returned: string): AmapSdk {
  return {
    Polygon: class {},
    DistrictSearch: class {
      search(_code: string, callback: (status: string, result: unknown) => void) {
        setTimeout(() => callback("complete", { districtList: [{ adcode: returned, boundaries }] }), delay);
      }
    },
  } as unknown as AmapSdk;
}

describe("gas GIS district provider contract", () => {
  it("accepts exact official adcode from the provider", async () => {
    const controller = new AbortController();
    await expect(readAmapBoundary(sdkWithDistrict(0, "500157"), "500157", controller.signal)).resolves.toEqual(boundaries);
  });
  it("rejects a historical region when the current adcode is selected", async () => {
    const controller = new AbortController();
    await expect(readAmapBoundary(sdkWithDistrict(0, "500112"), "500157", controller.signal)).rejects.toThrow("当前区划代码");
  });
  it("abandons pending focus after a newer selection", async () => {
    const controller = new AbortController();
    const pending = readAmapBoundary(sdkWithDistrict(10, "500157"), "500157", controller.signal);
    controller.abort();
    await expect(pending).rejects.toHaveProperty("name", "AbortError");
  });
});

describe("gas GIS page session", () => {
  function trackedSdk(outcomes: Array<"ready" | "limited"> = []) {
    const calls: string[] = [];
    const sdk = {
      Polygon: class {},
      DistrictSearch: class {
        search(adcode: string, callback: (status: string, result: unknown) => void) {
          calls.push(adcode);
          if (outcomes.shift() === "limited") callback("error", { infocode: "10021" });
          else setTimeout(() => callback("complete", { districtList: [{ adcode, boundaries }] }), 2);
        }
      },
    } as unknown as AmapSdk;
    return { sdk, calls };
  }

  it("shares pending requests and reuses the validated city boundary on return", async () => {
    const { sdk, calls } = trackedSdk();
    const signal = new AbortController().signal;
    const a = readAmapBoundary(sdk, "500000", signal);
    const b = readAmapBoundary(sdk, "500000", signal);
    await expect(Promise.all([a, b])).resolves.toEqual([boundaries, boundaries]);
    await expect(readAmapBoundary(sdk, "500157", signal)).resolves.toEqual(boundaries);
    await expect(readAmapBoundary(sdk, "500000", signal)).resolves.toEqual(boundaries);
    expect(calls).toEqual(["500000", "500157"]);
  });

  it("keeps a shared lookup available to callers after another caller changes selection", async () => {
    const { sdk, calls } = trackedSdk();
    const aborted = new AbortController();
    const pending = readAmapBoundary(sdk, "500157", aborted.signal);
    const stable = readAmapBoundary(sdk, "500157", new AbortController().signal);
    aborted.abort();
    await expect(pending).rejects.toHaveProperty("name", "AbortError");
    await expect(stable).resolves.toEqual(boundaries);
    expect(calls).toEqual(["500157"]);
  });

  it("honors cooldown while cached city boundaries remain available", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      const { sdk, calls } = trackedSdk(["ready", "limited", "ready"]);
      const signal = new AbortController().signal;
      await expect(readAmapBoundary(sdk, "500000", signal)).resolves.toEqual(boundaries);
      await expect(readAmapBoundary(sdk, "500157", signal)).rejects.toMatchObject({
        reason: "rate_limit", sdkStatus: "error", providerCode: "10021",
      });
      await expect(readAmapBoundary(sdk, "500157", signal)).rejects.toHaveProperty("reason", "rate_limit");
      await expect(readAmapBoundary(sdk, "500103", signal)).rejects.toHaveProperty("reason", "rate_limit");
      await expect(readAmapBoundary(sdk, "500000", signal)).resolves.toEqual(boundaries);
      expect(calls).toEqual(["500000", "500157"]);
      vi.setSystemTime(Date.now() + 5000);
      await expect(readAmapBoundary(sdk, "500157", signal)).resolves.toEqual(boundaries);
      expect(calls).toEqual(["500000", "500157", "500157"]);
    } finally {
      vi.useRealTimers();
    }
  });
});
