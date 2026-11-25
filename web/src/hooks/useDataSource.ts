import { useCallback, useEffect, useMemo, useState } from "react";
import type { ProviderMode, WaterPulseDataset } from "../domain/types";
import { ApiProvider, DemoProvider, PlatformProvider, type WaterPulseProvider } from "../providers";
import { FetchTransport } from "../providers/platform/transport";

export type RequestedMode = "demo" | "live" | "platform";

export interface DataSourceState {
  mode: ProviderMode;
  requestedMode: RequestedMode;
  provider: WaterPulseProvider;
  dataset: WaterPulseDataset | null;
  loading: boolean;
  error: string | null;
  switchToDemo: () => void;
  retry: () => void;
}

export function requestedMode(): RequestedMode {
  const params = new URLSearchParams(window.location.search);
  const value = params.get("mode") ?? import.meta.env.VITE_WATERPULSE_MODE ?? "demo";
  if (value === "platform") return "platform";
  return value === "live" ? "live" : "demo";
}

function platformDatasetId(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get("datasetId") ?? import.meta.env.VITE_WATERPULSE_PLATFORM_DATASET_ID ?? "";
}

export function platformBaseUrl(): string {
  return import.meta.env.VITE_WATERPULSE_PLATFORM_URL ?? "";
}

export function useDataSource(isReportRoute = false, skipBootstrap = false): DataSourceState {
  const demoProvider = useMemo(() => new DemoProvider(), []);
  const [provider, setProvider] = useState<WaterPulseProvider>(demoProvider);
  const [dataset, setDataset] = useState<WaterPulseDataset | null>(null);
  const [mode, setMode] = useState<ProviderMode>("demo");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [requested, setRequested] = useState<RequestedMode>(requestedMode);
  // Route changes suspend only platform initialization, never the user's explicit mode choice.
  const skipInitialLoad = skipBootstrap || (isReportRoute && requested === "platform");

  useEffect(() => {
    // An existing Report contains its own frozen dataset metadata.
    if (skipInitialLoad) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        if (requested === "platform") {
          const datasetId = platformDatasetId();
          if (!datasetId) {
            throw new Error("未配置平台 datasetId（datasetId 或 VITE_WATERPULSE_PLATFORM_DATASET_ID）。");
          }
          const platform = new PlatformProvider({
            transport: new FetchTransport(platformBaseUrl()),
            datasetId,
            reportEnabled: true,
            onUnavailable: (message) => {
              if (!cancelled) {
                setMode("unavailable");
                setError(message);
              }
            },
          });
          const available = await platform.probe();
          if (!available) {
            throw new Error("统一平台探测失败，未显示任何平台或演示结果。");
          }
          const platformDataset = await platform.loadDataset();
          if (cancelled) return;
          setProvider(platform);
          setDataset(platformDataset);
          setMode("live");
        } else if (requested === "live") {
          const apiUrl = import.meta.env.VITE_WATERPULSE_API_URL ?? "";
          if (!apiUrl) {
            throw new Error("未配置 VITE_WATERPULSE_API_URL，不能启用 live 模式。");
          }
          const api = new ApiProvider(apiUrl, (message) => {
            if (!cancelled) {
              setMode("unavailable");
              setError(message);
            }
          });
          const available = await api.probe();
          if (!available) {
            throw new Error("实时接口探测失败，未显示任何 live 数据。");
          }
          const liveDataset = await api.loadDataset();
          if (cancelled) return;
          setProvider(api);
          setDataset(liveDataset);
          setMode("live");
        } else {
          const demoDataset = await demoProvider.loadDataset();
          if (cancelled) return;
          setProvider(demoProvider);
          setDataset(demoDataset);
          setMode("demo");
        }
      } catch (caught) {
        if (cancelled) return;
        setMode("unavailable");
        setError(caught instanceof Error ? caught.message : "数据源不可用。");
        // Platform/live failures never fall back to the offline demo silently.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [demoProvider, reloadToken, requested, skipInitialLoad]);

  const switchToDemo = useCallback(() => {
    setLoading(true);
    setError(null);
    setRequested("demo");
    // The same loader handles mode changes and explicit retries, including a failed demo load.
    setReloadToken((value) => value + 1);
  }, []);

  const retry = useCallback(() => setReloadToken((value) => value + 1), []);

  return { mode, requestedMode: requested, provider, dataset, loading, error, switchToDemo, retry };
}
