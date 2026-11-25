import { useCallback, useEffect, useState } from "react";
import { toGasError, type GasApiError } from "./engineeringApi";

export type GasReadState<T> = {
  key: string | null;
  status: "idle" | "loading" | "succeeded" | "failed";
  data: T | null;
  error: GasApiError | null;
};
export function pendingGasRead<T>(key: string | null): GasReadState<T> {
  return { key, status: key === null ? "idle" : "loading", data: null, error: null };
}
// Hide the previous object even during the render before effect cleanup.
export function currentGasRead<T>(state: GasReadState<T>, key: string | null): GasReadState<T> {
  return state.key === key ? state : pendingGasRead<T>(key);
}
export function useGasRead<T>(key: string | null, read: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<GasReadState<T>>(() => pendingGasRead<T>(key));
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState(pendingGasRead<T>(key));
    if (key !== null) {
      Promise.resolve().then(() => read(controller.signal)).then((data) => {
        if (!controller.signal.aborted) setState({ key, status: "succeeded", data, error: null });
      }).catch((caught: unknown) => {
        if (!controller.signal.aborted) setState({ key, status: "failed", data: null, error: toGasError(caught) });
      });
    }
    return () => controller.abort();
  }, [key, read, attempt]);
  const retry = useCallback(() => { setState(pendingGasRead<T>(key)); setAttempt((value) => value + 1); }, [key]);
  return { ...currentGasRead(state, key), retry };
}
