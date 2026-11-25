import type {
  AssetResponse,
  DistrictResponse,
  EventResponse,
  GasAsset,
  GasDistrict,
  GasEvent,
} from "../../shared/contracts/platform";

export type { GasAsset, GasDistrict, GasEvent };
export type GasCollection<T> = {
  status: DistrictResponse["status"];
  data_mode: DistrictResponse["data_mode"];
  source_status: DistrictResponse["source_status"];
  validation_status: DistrictResponse["validation_status"];
  items: T[];
  total: number;
  limitations: string[];
};
export type GasDistrictResponse = DistrictResponse;
export type GasAssetResponse = AssetResponse;
export type GasEventResponse = EventResponse;
