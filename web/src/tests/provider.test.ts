import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiProvider } from "../providers/apiProvider";
import { DemoProvider } from "../providers/demoProvider";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("数据访问适配层", () => {
  it("DemoProvider 默认离线可用", async () => {
    const provider = new DemoProvider();
    expect(provider.mode).toBe("demo");
    const dataset = await provider.loadDataset();
    expect(dataset.pipes).toHaveLength(24);
    expect(dataset.meta.mode).toBe("demo");
    expect(Object.keys(dataset.assessments)).toHaveLength(24);
  });

  it("ApiProvider 探测失败时保持 unavailable，不伪造 live", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const provider = new ApiProvider("https://example.invalid");
    expect(provider.mode).toBe("unavailable");
    await expect(provider.probe()).resolves.toBe(false);
    expect(provider.mode).toBe("unavailable");
  });

  it("ApiProvider 没有地址时直接不可用", async () => {
    const provider = new ApiProvider("");
    await expect(provider.probe()).resolves.toBe(false);
    expect(provider.mode).toBe("unavailable");
  });
});
