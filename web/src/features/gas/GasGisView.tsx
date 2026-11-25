import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { PRODUCT_EDITION } from "../../shared/config/productEdition";
import { useGasCockpit } from "./GasCockpitProvider";
import {
  CHONGQING_CENTER,
  CHONGQING_DISTRICTS,
  CHONGQING_OUTLINE_COORDS,
  JIALING_RIVER_COORDS,
  YANGTZE_RIVER_COORDS,
  chongqingDistrictSvgViewport,
  findChongqingDistrict,
  projectChongqingCoord,
  summarizeDistrictReadiness,
  type ChongqingZone,
} from "./chongqingDistricts";
import {
  AmapBoundaryError,
  filterAuthorizedAssetFeatures,
  filterAuthorizedEventFeatures,
  loadAmapSdk,
  readAmapBoundary,
  readControlledAmapConfig,
  type AmapMap,
  type AmapSdk,
} from "./amapLoader";
import { zoomGasViewport, DEFAULT_VIEWPORT, type Viewport } from "./cockpitModel";
import { gasLocationReason, hasAuthorizedGasLocation, relatedAssetIds } from "./presentation";
import { useGasTheme } from "./gasTheme";

const ZONES: readonly ChongqingZone[] = ["主城核心区", "主城新区", "渝东北", "渝东南"];
const DISTRICT_COUNT = CHONGQING_DISTRICTS.length;
const CORE_COUNT = CHONGQING_DISTRICTS.filter(district => district.zone === "主城核心区").length;

function coordsToPath(coords: readonly [number, number][], close = false): string {
  return coords
    .map(([lon, lat], idx) => {
      const { x, y } = projectChongqingCoord(lon, lat);
      return `${idx === 0 ? "M" : "L"}${x} ${y}`;
    })
    .join(" ") + (close ? " Z" : "");
}

export function GasGisView() {
  const c = useGasCockpit();
  const { theme } = useGasTheme();
  const [zoneFilter, setZoneFilter] = useState<"all" | ChongqingZone>("all");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [viewport, setViewport] = useState<Viewport>(DEFAULT_VIEWPORT);
  const svgRef = useRef<SVGSVGElement>(null);
  const panRef = useRef<{ x: number; y: number; startX: number; startY: number } | null>(null);
  const amapContainerRef = useRef<HTMLDivElement>(null);

  const amapConfig = useMemo(() => readControlledAmapConfig(), []);
  const [sdkError, setSdkError] = useState<string>();
  const [sdkAttempt, setSdkAttempt] = useState(0);
  const [amap, setAmap] = useState<{ sdk: AmapSdk; map: AmapMap; alive: boolean }>();

  const selectedDistrict = useMemo(
    () => findChongqingDistrict(c.selectedDistrictId),
    [c.selectedDistrictId],
  );

  const readiness = useMemo(
    () => summarizeDistrictReadiness({
      selectedDistrictId: c.selectedDistrictId,
      districts: c.districts.data,
      districtsStatus: c.districts.status,
      events: c.events.data,
      assetTotal: c.summary.data?.total ?? null,
      caseView: c.caseView.data,
    }),
    [c.selectedDistrictId, c.districts.data, c.districts.status, c.events.data, c.summary.data, c.caseView.data],
  );

  const authorizedEvents = useMemo(
    () => filterAuthorizedEventFeatures(c.events.data?.items ?? []).filter(item => !selectedDistrict || item.event.district_id === selectedDistrict.district_id),
    [c.events.data, selectedDistrict],
  );
  const authorizedAssets = useMemo(() => {
    const pool = [...(c.assets.data?.items ?? [])];
    if (c.asset.data && !pool.some((item) => item.asset_id === c.asset.data!.asset_id)) {
      pool.push(c.asset.data);
    }
    return filterAuthorizedAssetFeatures(pool).filter(item => !selectedDistrict || item.asset.district_id === selectedDistrict.district_id);
  }, [c.assets.data, c.asset.data, selectedDistrict]);

  useEffect(() => {
    setViewport(chongqingDistrictSvgViewport(selectedDistrict));
    if (selectedDistrict) setDrawerOpen(true);
    else setDrawerOpen(false);
  }, [selectedDistrict, c.gisViewportRevision]);

  useEffect(() => {
    if (!amapConfig.configured) return;
    let cancelled = false;
    let activeMap: { sdk: AmapSdk; map: AmapMap; alive: boolean } | undefined;
    setSdkError(undefined);
    loadAmapSdk(amapConfig)
      .then((sdk) => {
        if (cancelled || !amapContainerRef.current) return;
        const map = new sdk.Map(amapContainerRef.current, {
          viewMode: "2D", zoom: 8, center: [CHONGQING_CENTER.lon, CHONGQING_CENTER.lat],
          mapStyle: "amap://styles/darkblue",
        });
        activeMap = { sdk, map, alive: true };
        setAmap(activeMap);
      })
      .catch(() => {
        if (cancelled) return;
        setSdkError("高德 JS API 暂不可用，显示离线行政参考。");
      });
    return () => {
      cancelled = true;
      if (activeMap) { activeMap.alive = false; activeMap.map.destroy(); }
    };
  }, [amapConfig, sdkAttempt]);

  useEffect(() => {
    if (amap) amap.map.setMapStyle(theme === "light" ? "amap://styles/normal" : "amap://styles/darkblue");
  }, [amap, theme]);

  const adcode = selectedDistrict?.district_id ?? "500000";
  const [retryBoundary, setRetryBoundary] = useState(0);
  const [boundary, setBoundary] = useState<{
    adcode: string; attempt: number;
    result: { rings: unknown[][] } | { error: AmapBoundaryError };
  }>();
  const scope = boundary?.adcode === adcode && boundary.attempt === retryBoundary ? boundary.result : undefined;
  const currentRings = scope && "rings" in scope ? scope.rings : undefined;
  const boundaryIssue = scope && "error" in scope ? scope.error : undefined;
  const amapStatus = currentRings ? "ready" : !amapConfig.configured || sdkError || boundaryIssue ? "fallback" : "loading";
  const amapError = boundaryIssue?.message ?? sdkError;

  useEffect(() => {
    if (!amap) return;
    const controller = new AbortController();
    setBoundary(undefined);
    const lookup = () => readAmapBoundary(amap.sdk, adcode, controller.signal)
      .then((rings) => {
        if (controller.signal.aborted) return;
        setBoundary({ adcode, attempt: retryBoundary, result: { rings } });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const issue = error instanceof AmapBoundaryError
          ? error : new AmapBoundaryError("高德行政区划暂不可用。", "service");
        setBoundary({ adcode, attempt: retryBoundary, result: { error: issue } });
      });
    const start = window.setTimeout(lookup, 120);
    return () => {
      window.clearTimeout(start);
      controller.abort();
    };
  }, [amap, adcode, retryBoundary]);

  useEffect(() => {
    const Polygon = amap?.sdk.Polygon;
    if (!amap || !Polygon || !currentRings) return;
    if (!c.gisLayers.adminRegions) {
      const center = selectedDistrict ? [selectedDistrict.refLon, selectedDistrict.refLat] as [number, number]
        : [CHONGQING_CENTER.lon, CHONGQING_CENTER.lat] as [number, number];
      amap.map.setZoomAndCenter(selectedDistrict ? 10 : 8, center);
      return;
    }
    const overlays = currentRings.map(path => new Polygon({ path, strokeColor: "#76e0e3", strokeWeight: 2,
      fillColor: "#217e9c", fillOpacity: 0.17 }));
    amap.map.add(overlays);
    const side = drawerOpen && (amapContainerRef.current?.clientWidth ?? 0) > 640 ? 270 : 32;
    amap.map.setFitView(overlays, true, [32, 32, 32, side], selectedDistrict ? 13 : 9);
    return () => { if (amap.alive) amap.map.remove(overlays); };
  }, [amap, selectedDistrict, currentRings, c.gisViewportRevision, c.gisLayers.adminRegions, drawerOpen]);

  useEffect(() => {
    if (!amap?.sdk.Marker || amapStatus !== "ready") return;
    const Marker = amap.sdk.Marker;
    const positions = [
      ...(c.gisLayers.authorizedAssets ? authorizedAssets : []),
      ...(c.gisLayers.authorizedEvents ? authorizedEvents : []),
    ];
    const features = positions.map(({ gcj02 }) => new Marker({ position: [gcj02.lon, gcj02.lat] }));
    if (!features.length) return;
    amap.map.add(features);
    return () => { if (amap.alive) amap.map.remove(features); };
  }, [amap, amapStatus, authorizedAssets, authorizedEvents, c.gisLayers.authorizedAssets, c.gisLayers.authorizedEvents]);

  useEffect(() => {
    const element = svgRef.current;
    if (!element || amapStatus === "ready") return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const matrix = element.getScreenCTM();
      const point = matrix ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()) : { x: 500, y: 320 };
      setViewport((current) => zoomGasViewport(current, event.deltaY > 0 ? 0.9 : 1.12, point));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [amapStatus]);

  const toSvgPoint = (event: ReactPointerEvent<SVGSVGElement>) => {
    const matrix = event.currentTarget.getScreenCTM();
    return matrix ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()) : { x: event.clientX, y: event.clientY };
  };

  const outlinePath = useMemo(() => coordsToPath(CHONGQING_OUTLINE_COORDS, true), []);
  const yangtzePath = useMemo(() => coordsToPath(YANGTZE_RIVER_COORDS), []);
  const jialingPath = useMemo(() => coordsToPath(JIALING_RIVER_COORDS), []);
  const visibleDistricts = useMemo(
    () => zoneFilter === "all" ? CHONGQING_DISTRICTS : CHONGQING_DISTRICTS.filter((d) => d.zone === zoneFilter),
    [zoneFilter],
  );

  function selectDistrictFromToolbar(districtId: string | null) {
    if (!districtId) setZoneFilter("all");
    c.selectDistrict(districtId, true);
  }

  const selectedAsset = c.asset.data;
  const selectedEvent = c.route.gasAssetId ? null : c.event.data;
  const selected = selectedAsset ?? selectedEvent;
  const selectedEventRelatedIds = selectedEvent ? relatedAssetIds(selectedEvent) : [];
  const canUseTopology = c.mapping.edgeIds.length + c.mapping.nodeIds.length > 0;

  return (
    <div className="gas-topology-shell gas-gis-shell" data-testid="gas-gis-view" data-gis-mode={amapStatus}
      data-gis-adcode={adcode} data-gis-error={boundaryIssue?.reason} data-gis-provider-code={boundaryIssue?.providerCode}>
      <div className="gas-map-toolbar gas-gis-toolbar">
        <span className="gas-map-toolbar-title">
          <strong>重庆全域行政 GIS 视图</strong>
          <span className="gas-mode-pill is-emergency">{amapStatus === "ready" ? "高德区划边界" : amapStatus === "loading" ? "高德区划加载中" : "离线区县位置参考"}</span>
        </span>
        <div className="gas-gis-controls">
          <label className="gas-gis-district-select">
            <span className="gas-sr-only">选择重庆行政区县</span>
            <select
              aria-label="选择重庆行政区县"
              value={selectedDistrict?.district_id ?? ""}
              onChange={(event) => selectDistrictFromToolbar(event.target.value || null)}
            >
              <option value="">全市域（{DISTRICT_COUNT}个区县）</option>
              {CHONGQING_DISTRICTS.map((d) => (
                <option key={d.district_id} value={d.district_id}>
                  {d.name} · {d.zone}
                </option>
              ))}
            </select>
          </label>
          <span className="gas-map-toolbar-actions">
            <button type="button" aria-label="放大GIS地图" onClick={() => amapStatus === "ready" ? amap?.map.zoomIn() : setViewport((cur) => zoomGasViewport(cur, 1.2))}>＋</button>
            <button type="button" aria-label="缩小GIS地图" onClick={() => amapStatus === "ready" ? amap?.map.zoomOut() : setViewport((cur) => zoomGasViewport(cur, 0.84))}>－</button>
            <button type="button" onClick={() => selectDistrictFromToolbar("500103")}>聚焦主城</button>
            <button type="button" onClick={() => selectDistrictFromToolbar(null)}>返回重庆全市</button>
            <button type="button" onClick={() => setDrawerOpen((v) => !v)}>{drawerOpen ? "收起区县栏" : "展开区县栏"}</button>
            <button type="button" onClick={() => c.go({ gasMap: "topology" })}>返回GAS-M0合成拓扑</button>
          </span>
        </div>
      </div>

      <div className="gas-map-canvas gas-gis-canvas" data-district-open={drawerOpen} data-testid="gas-central-view">
        {(amapStatus !== "ready" || authorizedAssets.length === 0) && (
          <section className="gas-gis-unavailable-bar" data-testid="gas-gis-unavailable" role="status">
            <div>
              {authorizedAssets.length === 0 && <strong>{selectedDistrict?.name ?? "重庆全市"}暂无已授权燃气资产坐标</strong>}
              {amapStatus !== "ready" && <span className="gas-gis-state-copy">{amapError ?? (amapConfig.configured ? "正在查验高德区划边界。" : "高德运行配置待提供，当前为离线行政参考。")}</span>}
              {amapStatus === "fallback" && amapConfig.configured && <button type="button"
                onClick={() => amap ? setRetryBoundary(value => value + 1) : setSdkAttempt(value => value + 1)}>重试在线边界</button>}
            </div>
          </section>
        )}
        {amapConfig.configured && <div ref={amapContainerRef} className="gas-amap-host" data-testid="gas-amap-host"
          aria-hidden={amapStatus !== "ready"}
          style={amapStatus === "ready" ? undefined : { opacity: 0, visibility: "hidden", pointerEvents: "none" }} />}
        {amapStatus !== "ready" && (
          <svg
            ref={svgRef}
            viewBox="0 0 1000 640"
            role="img"
            aria-label={`重庆市${DISTRICT_COUNT}区县行政参考底图`}
            data-testid="gas-gis-canvas"
            onPointerDown={(event) => {
              const target = event.target as Element;
              if (target.closest("[data-district-id]")) return;
              const start = toSvgPoint(event);
              panRef.current = { x: viewport.x, y: viewport.y, startX: start.x, startY: start.y };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              if (!panRef.current) return;
              const next = toSvgPoint(event);
              const start = panRef.current;
              setViewport((cur) => ({ ...cur, x: start.x + next.x - start.startX, y: start.y + next.y - start.startY }));
            }}
            onPointerUp={() => { panRef.current = null; }}
            onPointerCancel={() => { panRef.current = null; }}
          >
            <rect className="gas-map-background" width="1000" height="640" fill="var(--gas-surface-0)" />
            <g data-testid="gas-gis-viewport-transform" data-zoom={viewport.zoom} transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.zoom})`}>
              {c.gisLayers.adminRegions && PRODUCT_EDITION === "competition" ? (
                <>
                  <path d={outlinePath} className="gas-gis-chongqing-boundary" />
                  <path d={yangtzePath} className="gas-gis-river" />
                  <path d={jialingPath} className="gas-gis-river is-tributary" />
                </>
              ) : null}

              {c.gisLayers.adminRegions && visibleDistricts.map((district) => {
                const pos = projectChongqingCoord(district.refLon, district.refLat);
                const selected = c.selectedDistrictId === district.district_id;
                const isCoreCluster = district.zone === "主城核心区" && viewport.zoom < 1.8 && !selected;
                if (isCoreCluster && district.district_id !== "500103") return null;
                return (
                  <g
                    key={district.district_id}
                    data-district-id={district.district_id}
                    className={`gas-gis-district-node${selected ? " is-selected" : ""}${isCoreCluster ? " is-cluster" : ""}`}
                    role="button"
                    tabIndex={0}
                    aria-label={isCoreCluster ? `聚焦主城核心区${CORE_COUNT}区` : `定位重庆区县 ${district.name}`}
                    onClick={() => {
                      if (isCoreCluster) {
                        setZoneFilter("主城核心区");
                        c.selectDistrict("500103", true);
                      } else {
                        c.selectDistrict(district.district_id, true);
                      }
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        c.selectDistrict(district.district_id, true);
                      }
                    }}
                  >
                    <circle cx={pos.x} cy={pos.y} r={isCoreCluster ? 11 : selected ? 8 : 5} />
                    <text
                      x={district.district_id === "500120" && viewport.zoom < 1.8 && !selected ? pos.x - 9 : pos.x}
                      y={district.district_id === "500120" && viewport.zoom < 1.8 && !selected ? pos.y + 4 : pos.y - (isCoreCluster ? 14 : 10)}
                      textAnchor={district.district_id === "500120" && viewport.zoom < 1.8 && !selected ? "end" : "middle"}
                    >
                      {isCoreCluster ? `主城${CORE_COUNT}区 (点击展开)` : district.shortName}
                    </text>
                  </g>
                );
              })}

              {c.gisLayers.authorizedEvents && authorizedEvents.map((feat) => {
                const pos = projectChongqingCoord(feat.gcj02.lon, feat.gcj02.lat);
                return (
                  <g key={feat.event.event_id} data-authorized-event={feat.event.event_id} className="gas-gis-auth-marker">
                    <circle cx={pos.x} cy={pos.y} r="7" />
                    <text x={pos.x + 9} y={pos.y + 4}>{feat.event.event_id}</text>
                  </g>
                );
              })}

              {c.gisLayers.authorizedAssets && authorizedAssets.map((feat) => {
                const pos = projectChongqingCoord(feat.gcj02.lon, feat.gcj02.lat);
                return (
                  <g key={feat.asset.asset_id} data-authorized-asset={feat.asset.asset_id} className="gas-gis-auth-marker is-asset">
                    <rect x={pos.x - 4} y={pos.y - 4} width="8" height="8" />
                    <text x={pos.x + 8} y={pos.y + 3}>{feat.asset.asset_id}</text>
                  </g>
                );
              })}
            </g>
          </svg>
        )}

        {(selected || c.lastLocateNotice) ? (
          <div className="gas-gis-locate-banner" data-testid="gas-gis-locate-banner">
            <strong>{selectedAsset ? `资产定位校验：${selectedAsset.asset_id}` : selectedEvent ? `事件定位校验：${selectedEvent.event_id}` : "地图定位提示"}</strong>
            {selected ? <p>
              {selectedEvent ? `已关联 ${selectedEventRelatedIds.length} 项资产。` : null}
              {gasLocationReason(selected)} location_status={selected.location_status ?? "unknown"}，district_id={selected.district_id ?? "未映射"}。
            </p> : null}
            {c.lastLocateNotice ? <p>{c.lastLocateNotice}</p> : null}
            {selected && !hasAuthorizedGasLocation(selected) ? (
              <div className="gas-gis-locate-actions">
                {canUseTopology ? <button type="button" onClick={c.focusTopology}>
                  {selectedAsset ? "切换到合成拓扑定位资产" : `切换到合成拓扑高亮 ${selectedEventRelatedIds.length} 项`}
                </button> : <p>当前对象缺少可用的合成拓扑映射。</p>}
              </div>
            ) : null}
          </div>
        ) : null}

        {drawerOpen ? (
          <aside className="gas-district-panel" data-testid="gas-district-panel" aria-label="重庆区县信息面板">
            <div className="gas-district-panel-head">
              <strong>{selectedDistrict ? `${selectedDistrict.name} (${selectedDistrict.district_id})` : `重庆全域 · ${DISTRICT_COUNT}区县概览`}</strong>
              <span>{selectedDistrict ? selectedDistrict.zone : `区县接口: ${readiness.backendStatus}`}</span>
            </div>
            <div className="gas-zone-pills" role="group" aria-label="片区筛选">
              <button type="button" aria-pressed={zoneFilter === "all"} onClick={() => setZoneFilter("all")}>全部({DISTRICT_COUNT})</button>
              {ZONES.map((zone) => (
                <button key={zone} type="button" aria-pressed={zoneFilter === zone} onClick={() => setZoneFilter(zone)}>{zone}</button>
              ))}
            </div>
            <dl className="gas-fields">
              <div><dt>燃气区县数据接口</dt><dd>{readiness.backendStatus} / {readiness.backendDataMode}</dd></div>
              <div><dt>燃气数据区县条数</dt><dd>{readiness.backendTotal} 条</dd></div>
              <div><dt>行政边界</dt><dd>{amapStatus === "ready" ? "高德 DistrictSearch · GCJ-02" : "离线示意参考"}</dd></div>
              <div><dt>当前范围授权设施</dt><dd>{authorizedAssets.length ? authorizedAssets.length + " 项" : "暂无已授权资产"}</dd></div>
              <div><dt>区县事件映射</dt><dd>{selectedDistrict ? (readiness.selectedDistrictEventCount ?? "unavailable（未映射）") : `${readiness.mappedEventCount} / ${(c.events.data?.items ?? []).length}`}</dd></div>
              <div><dt>区县资产映射</dt><dd>{readiness.mappedAssetCount} / {readiness.totalAssetsKnown ?? "—"}（其余无区县字段）</dd></div>
              <div><dt>区划登记版本</dt><dd><a href="https://mzj.cq.gov.cn/zwgk_218/zfxxgkml/tzgg/202601/t20260119_15332641.html"
                target="_blank" rel="noreferrer">重庆市民政局 · 2025-12</a></dd></div>
            </dl>
            <p className="gas-window-note">{amapStatus === "ready"
              ? "高德提供行政边界参考。燃气设施和事件按已授权坐标展示。"
              : readiness.limitations[0]}</p>
          </aside>
        ) : null}

        <div className="gas-map-gesture-hint">拖动平移 · 滚轮缩放 · 点击区县居中 · 选择区县查看详情 · 返回重庆全市</div>
      </div>

      <div className="gas-map-bottombar" data-testid="gas-gis-layers">
        <div className="gas-layer-toggles" aria-label="GIS地图图层管理">
          <label><input type="checkbox" checked={c.gisLayers.adminRegions} onChange={() => c.toggleGisLayer("adminRegions")} />重庆行政区划(参考)</label>
          <label><input type="checkbox" checked={c.gisLayers.authorizedAssets} onChange={() => c.toggleGisLayer("authorizedAssets")} />授权设施点位({authorizedAssets.length})</label>
          <label><input type="checkbox" checked={c.gisLayers.authorizedEvents} onChange={() => c.toggleGisLayer("authorizedEvents")} />授权事件点位({authorizedEvents.length})</label>
        </div>
        <p className="gas-map-selection" role="status">
          {amapStatus === "ready" ? "高德区划" : `当前缩放 ${viewport.zoom.toFixed(2)}×`} · 授权设施 {authorizedAssets.length} / 事件 {authorizedEvents.length} · 仅受控坐标可落图
        </p>
      </div>
    </div>
  );
}
