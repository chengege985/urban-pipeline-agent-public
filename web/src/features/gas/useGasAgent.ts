import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrowserGasTransport, GasApiError, toGasError } from "./engineeringApi";
import { GasAgentApi, type GasAgentHealthResponse, type GasAgentQueryRequest, type GasAgentQueryResponse } from "./gasAgentApi";
export type GasConversationTurn = { input: string; result: GasAgentQueryResponse | null; error: GasApiError | null; status: "ready" | "error"; };
type Selection = { asset_id?: string; event_id?: string; district_id?: string };
const explicitId = /(?:^|[^a-z0-9_-])(?:syn-gas-[a-z0-9_-]+|[a-z]{1,8}-?\d+)(?![a-z0-9_-])/i;
const reference = /上一|刚才|前面|当前|该|这个|此|选中|它/;
const assetReference = /(?:上一|刚才|前面|当前|该|这个|此|选中).{0,6}(管段|管线|阀门|资产|设施|节点)/;
const eventReference = /(?:上一|刚才|前面|当前|该|这个|此|选中).{0,8}(事件|告警)/;
const affectedPipes = /(?:受影响|影响|涉及|关联).{0,8}(?:管段|管线)/;

function lastReturnedId(history: readonly GasConversationTurn[], kind: "asset" | "event") {
  for (const turn of [...history].reverse()) {
    const ids = new Set<string>();
    for (const tool of turn.result?.tools ?? []) {
      if (tool.status !== "succeeded" && tool.status !== "partial") continue;
      const key = kind === "asset" ? "asset_id" : "event_id";
      const value = (tool.data?.[kind] as Record<string, unknown> | undefined)?.[key];
      if (typeof value === "string" && value) ids.add(value);
      if (kind === "event" && tool.tool_name === "map.locate" && typeof tool.data?.event_id === "string") ids.add(tool.data.event_id);
    }
    if (ids.size > 0) return ids.size === 1 ? [...ids][0] : undefined;
  }
}

export function conversationContext(question: string, history: readonly GasConversationTurn[], selected: Selection): Selection {
  const district = /区县|GIS|地图/i.test(question) && selected.district_id ? { district_id: selected.district_id } : {};
  if (explicitId.test(question)) return district;

  const prior = /上一|刚才|前面/.test(question);
  const selectionOnly = /选中/.test(question);
  const objectId = (kind: "asset" | "event") => {
    const current = selected[kind === "asset" ? "asset_id" : "event_id"];
    if (selectionOnly) return current;
    const previous = () => lastReturnedId(history, kind);
    if (prior) return previous() ?? current;
    return selected.asset_id || selected.event_id ? current : previous();
  };

  if (assetReference.test(question)) {
    const asset_id = objectId("asset");
    return asset_id ? { ...district, asset_id } : district;
  }
  if (affectedPipes.test(question) || eventReference.test(question)) {
    const event_id = objectId("event");
    return event_id ? { ...district, event_id } : district;
  }
  if (reference.test(question)) {
    const asset_id = objectId("asset"), event_id = objectId("event");
    if (asset_id && !event_id) return { ...district, asset_id };
    if (event_id && !asset_id) return { ...district, event_id };
  }
  return district;
}

export function opensPipeDetails(question: string): boolean {
  return /^(?:请(?:帮我)?)?\s*打开\s*(?:(?:管段\s*)?(?:syn-gas-pipe-)?K-\d+\s*(?:管段)?|(?:这个|该|选中的)管段)(?:的?(?:详情|信息))?\s*[。！？?!]?\s*$/i.test(question);
}

export function useGasAgent(baseUrl: string, identity: string, scope = identity) {
  const api = useMemo(() => new GasAgentApi(new BrowserGasTransport(baseUrl)), [baseUrl]);
  const [health, setHealth] = useState<GasAgentHealthResponse | null>(null);
  const [healthError, setHealthError] = useState<GasApiError | null>(null);
  const [probing, setProbing] = useState(false);
  const [state, setState] = useState<{ status: "idle" | "loading" | "ready" | "error" | "cancelled"; input: string; result: GasAgentQueryResponse | null; error: GasApiError | null }>({ status: "idle", input: "", result: null, error: null });
  const [history, setHistory] = useState<GasConversationTurn[]>([]);
  const queryRef = useRef<AbortController | null>(null);
  const healthRef = useRef<AbortController | null>(null);
  const lastRequest = useRef<GasAgentQueryRequest | null>(null);
  const probe = useCallback(async (background = false) => {
    if (background && healthRef.current) return;
    healthRef.current?.abort();
    const controller = new AbortController(); healthRef.current = controller;
    const timer = setTimeout(() => controller.abort(new DOMException("timed out", "TimeoutError")), 5000);
    if (!background) {
      setProbing(true);
      setHealthError(null);
      setHealth(null);
    }
    try {
      const value = await api.health(controller.signal);
      if (!controller.signal.aborted && healthRef.current === controller) {
        setHealth(value);
        setHealthError(null);
      }
    } catch (err) {
      if (healthRef.current === controller && (!controller.signal.aborted || controller.signal.reason?.name === "TimeoutError")) {
        setHealth(null);
        setHealthError(controller.signal.aborted ? new GasApiError("network", "健康检查超时，请重试。") : toGasError(err));
      }
    } finally {
      clearTimeout(timer);
      if (healthRef.current === controller) { healthRef.current = null; setProbing(false); }
    }
  }, [api]);
  const submit = useCallback(async (request: GasAgentQueryRequest) => {
    queryRef.current?.abort();
    const controller = new AbortController(); queryRef.current = controller;
    lastRequest.current = request;
    const timer = setTimeout(() => controller.abort(new DOMException("timed out", "TimeoutError")), 15000);
    setState({ status: "loading", input: request.question, result: null, error: null });
    try {
      // Health readiness never upgrades ACL or prevents a real query solely
      // because the model is unavailable. Query has its own server-side checks.
      const value = await api.query(request, controller.signal);
      if (queryRef.current === controller && !controller.signal.aborted) {
        setState({ status: "ready", input: request.question, result: value, error: null });
        setHistory((items) => [...items, {input:request.question,result:value,error:null,status:"ready" as const}].slice(-20));
        return value;
      }
    } catch (err) {
      if (queryRef.current === controller && (!controller.signal.aborted || controller.signal.reason?.name === "TimeoutError")) {
        const error = controller.signal.aborted ? new GasApiError("network", "查询超时，请重试；未生成本地回答。") : toGasError(err);
        setState({ status:"error",input:request.question,result:null,error });
        const expired = error.status === 401 || error.status === 403 || error.status === 409;
        if (expired) lastRequest.current = null;
        setHistory((items) => expired ? [] :
          [...items,{input:request.question,result:null,error,status:"error" as const}].slice(-20));
      }
    } finally {
      clearTimeout(timer);
      if (queryRef.current === controller) queryRef.current = null;
    }
    return null;
  }, [api]);
  const cancel = useCallback(() => {
    queryRef.current?.abort(); queryRef.current = null;
    setState((value) => ({ ...value, status: "cancelled", result: null, error: null }));
  }, []);
  useEffect(() => {
    setHistory([]);
    setState({ status: "idle", input: "", result: null, error: null }); lastRequest.current = null;
    return () => {
      queryRef.current?.abort(); queryRef.current = null;
    };
  }, [api, identity, scope]);
  useEffect(() => {
    setHealth(null); setHealthError(null); setProbing(false);
    return () => {
      healthRef.current?.abort(); healthRef.current = null;
    };
  }, [api, identity]);
  useEffect(() => {
    if (import.meta.env.VITE_WPN_GAS_DEMO !== "1" || !["127.0.0.1", "localhost"].includes(window.location.hostname)) return;
    if (baseUrl && new URL(baseUrl, window.location.href).origin !== window.location.origin) return;
    const refresh = () => {
      if (document.visibilityState === "visible") void probe(true);
    };
    refresh();
    const timer = window.setInterval(refresh, 4500);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [baseUrl, identity, probe]);
  const clearHistory = useCallback(() => {
    queryRef.current?.abort(); queryRef.current=null; lastRequest.current=null;
    setHistory([]);setState({status:"idle",input:"",result:null,error:null});
  }, []);
  return { ...state, history, clearHistory, health, healthError, probing, probe, submit, cancel,
    retry: () => lastRequest.current ? submit(lastRequest.current) : Promise.resolve() };
}
