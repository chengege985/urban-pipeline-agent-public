import { GasSurface } from "./GasSurface";
import { useGasHashRoute } from "../hooks/useGasHashRoute";

export default function GasProjectApp() {
  const { route, navigate } = useGasHashRoute();
  return <GasSurface route={{ ...route, view: "gas" }} navigate={navigate} />;
}
