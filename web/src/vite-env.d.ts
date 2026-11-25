/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_WPN_EDITION?: "competition" | "project";
  readonly VITE_WPN_GAS_DEMO?: "1";
  readonly VITE_WATERPULSE_MODE?: "demo" | "live" | "platform";
  readonly VITE_WATERPULSE_API_URL?: string;
  readonly VITE_WATERPULSE_PLATFORM_URL?: string;
  readonly VITE_WATERPULSE_PLATFORM_DATASET_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
