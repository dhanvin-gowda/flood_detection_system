'use client';
import React, { useEffect, useState, useRef } from 'react';
import './globals.css';
import { FloodMap, OSM_LAYER_NAMES } from './map';
import type {
    FloodMapHandle,
    FloodVitLayerName,
    OsmFeatureCollection,
    OsmLayerName,
    RasterOverlay,
} from './map';
import Search, { BackendUnavailableError } from './search';
import type { AreaOfInterest } from './search';

type IconProps = { size?: number };

const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 15 * 60 * 1000;

type AnalysisPhase =
    | 'queued'
    | 'selecting'
    | 'submitting_jobs'
    | 'downloading'
    | 'completed'
    | 'error';

type AnalysisState = {
    phase: AnalysisPhase;
    error?: string | null;
    before?: { beginPosition?: string; relativeOrbitNumber?: string | number } | null;
    after?: { beginPosition?: string; relativeOrbitNumber?: string | number } | null;
    results?: Record<string, unknown> | null;
};

const PHASE_LABELS: Record<AnalysisPhase, string> = {
    queued: 'Queued',
    selecting: 'Finding acquisitions',
    submitting_jobs: 'Downloading before scene',
    downloading: 'Downloading after scene',
    completed: 'Complete',
    error: 'Failed',
};

function formatAcquisition(value?: string | null): string {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toISOString().slice(0, 10).replace(/-/g, ' ');
}

const IconWave = ({ size = 15 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M2 12c2-3 4-3 6 0s4 3 6 0 4-3 6 0" strokeLinecap="round" />
        <path d="M2 18c2-3 4-3 6 0s4 3 6 0 4-3 6 0" strokeLinecap="round" opacity="0.5" />
    </svg>
);

const IconPin = ({ size = 13 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 21s7-7.1 7-12a7 7 0 10-14 0c0 4.9 7 12 7 12z" />
        <circle cx="12" cy="9" r="2.4" />
    </svg>
);

const IconChevronDown = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
        <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const IconArrowRight = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.3">
        <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const IconCalendar = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="5" width="18" height="16" rx="2" />
        <path d="M3 10h18M8 3v4M16 3v4" strokeLinecap="round" />
    </svg>
);

const IconCheck = ({ size = 10 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
        <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const IconAlertTriangle = ({ size = 12 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
        <path d="M12 3L2 21h20L12 3z" strokeLinejoin="round" />
        <path d="M12 10v4M12 17.5v.1" strokeLinecap="round" />
    </svg>
);

const IconX = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
        <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
    </svg>
);

const IconPlus = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.3">
        <path d="M12 5v14M5 12h14" strokeLinecap="round" />
    </svg>
);

const IconMinus = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.3">
        <path d="M5 12h14" strokeLinecap="round" />
    </svg>
);

const IconCrosshair = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="7" />
        <path d="M12 2v3M12 19v3M2 12h3M19 12h3" strokeLinecap="round" />
    </svg>
);

const IconExpand = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M9 3H3v6M15 3h6v6M3 15v6h6M21 15v6h-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const IconLayers = ({ size = 13 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 2l9 5-9 5-9-5 9-5z" strokeLinejoin="round" />
        <path d="M3 12l9 5 9-5M3 17l9 5 9-5" strokeLinejoin="round" />
    </svg>
);

const IconRefresh = ({ size = 12 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
        <path d="M20.5 12a8.5 8.5 0 11-2.49-6.01" strokeLinecap="round" />
        <path d="M20.5 3.5v5h-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const IconDoc = ({ size = 15 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M7 2h8l5 5v15H7V2z" strokeLinejoin="round" />
        <path d="M15 2v5h5M9 13h6M9 17h6" strokeLinecap="round" />
    </svg>
);

const IconInfo = ({ size = 13 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v5.5M12 8v.1" strokeLinecap="round" />
    </svg>
);

const IconFileCheck = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M7 2h8l5 5v15H7V2z" strokeLinejoin="round" />
        <path d="M15 2v5h5M9.5 14l1.8 1.8L15 12" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const IconDownload = ({ size = 14 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 3v12M7 10l5 5 5-5M4 20h16" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const IconMap = ({ size = 28 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d="M9 4l-6 2v14l6-2 6 2 6-2V4l-6 2-6-2z" strokeLinejoin="round" />
        <path d="M9 4v14M15 6v14" />
    </svg>
);

export const TopNavbar: React.FC = () => {
    return (
        <header className="fs-navbar">
            <div className="fs-navbar__left">
                <div className="fs-brand">
                    <span className="fs-brand__mark">
                        <IconWave />
                    </span>
                    <span className="fs-brand__name">FloodScope</span>
                </div>
                <div className="fs-navbar__divider" />
                <span className="fs-navbar__label">FLOOD INTELLIGENCE</span>
                <span className="fs-badge fs-badge--mint">
                    <span className="fs-dot" />
                    SYSTEM READY
                </span>
            </div>

            <nav className="fs-navbar__right">
                <button className="fs-navlink">Case Study</button>
                <button className="fs-navlink">Data Sources</button>
                <button className="fs-navlink">About</button>
                <button className="fs-btn--live">
                    <span className="fs-dot" />
                    LIVE ANALYSIS
                    <IconChevronDown size={12} />
                </button>
            </nav>
        </header>
    );
};

const PIPELINE_STEPS = [
    'Satellite data found',
    'Before / after imagery',
    'Flood segmentation',
    'Infrastructure analysis',
    'Connectivity analysis',
];

export type AnalysisSidebarProps = {
    aoi: AreaOfInterest | null;
    date: string;
    onDateChange: (value: string) => void;
    analyzing: boolean;
    phase: AnalysisPhase | null;
    state: AnalysisState | null;
    error: string | null;
    onAnalyze: () => void;
};

export const AnalysisSidebar: React.FC<AnalysisSidebarProps> = ({
    aoi,
    date,
    onDateChange,
    analyzing,
    phase,
    state,
    error,
    onAnalyze,
}) => {
    const floodArea = state?.results?.floodAreaKm2;
    const aoiArea = state?.results?.aoiAreaKm2;

    return (
        <aside className="fs-panel fs-panel--left">
            <div className="fs-panel__inner">
                <div className="fs-step-tracker">
                    <span className="fs-eyebrow">MISSION CONTROL</span>
                    <span className="fs-step-tracker__count">01 / 04</span>
                </div>

                <h1 className="fs-heading-lg">
                    Analyze
                    <br />
                    Disaster Area.
                </h1>
                <p className="fs-desc">
                    Configure your area and satellite inputs to assess potential flood impact.
                </p>

                <div className="fs-field">
                    <span className="fs-field__label">01 &nbsp;AREA OF INTEREST</span>
                    <div className="fs-selector">
                        <IconMap size={16} />
                        <span className="fs-selector__body">
                            <span
                                className="fs-selector__title"
                                style={aoi ? undefined : { color: 'var(--text-tertiary)' }}
                            >
                                {aoi ? aoi.name : 'Search a place…'}
                            </span>
                        </span>
                    </div>
                    {aoi && (
                        <div className="fs-field__sublabel">
                            {aoi.bbox.map((v) => v.toFixed(3)).join(', ')}
                            {aoiArea !== undefined ? ` · ${Number(aoiArea).toFixed(1)} km²` : ''}
                        </div>
                    )}
                </div>

                <div className="fs-field">
                    <span className="fs-field__label">02 &nbsp;EVENT</span>
                    <div className="fs-field__sublabel" style={{ marginTop: 0, marginBottom: 8 }}>
                        Event Date
                    </div>
                    <div
                        className="fs-date-input"
                        onClick={(e) => {
                            const input = e.currentTarget.querySelector('input');
                            if (!input || input.disabled) return;
                            try {
                                input.showPicker();
                            } catch {
                                input.focus();
                            }
                        }}
                    >
                        <input
                            type="date"
                            value={date}
                            onChange={(e) => onDateChange(e.target.value)}
                            disabled={analyzing}
                        />
                        <span className="fs-date-input__icon">
                            <IconCalendar />
                        </span>
                    </div>
                </div>

                <div className="fs-field">
                    <span className="fs-field__label">04 &nbsp;IMAGERY</span>
                    <div className="fs-imagery-grid">
                        <div className="fs-imagery-card">
                            <div className="fs-imagery-card__label">BEFORE</div>
                            <div className="fs-imagery-card__date">
                                {formatAcquisition(state?.before?.beginPosition)}
                            </div>
                            <div className="fs-imagery-card__status">
                                <span
                                    className={`fs-selector__status-dot ${
                                        state?.before ? '' : 'fs-selector__status-dot--pending'
                                    }`}
                                />
                                {state?.before
                                    ? `Orbit ${state.before.relativeOrbitNumber}`
                                    : 'Pending analysis'}
                            </div>
                        </div>
                        <div className="fs-imagery-card">
                            <div className="fs-imagery-card__label">AFTER</div>
                            <div className="fs-imagery-card__date">
                                {formatAcquisition(state?.after?.beginPosition)}
                            </div>
                            <div className="fs-imagery-card__status">
                                <span
                                    className={`fs-selector__status-dot ${
                                        state?.after ? '' : 'fs-selector__status-dot--pending'
                                    }`}
                                />
                                {state?.after
                                    ? `Orbit ${state.after.relativeOrbitNumber}`
                                    : 'Pending analysis'}
                            </div>
                        </div>
                    </div>
                </div>

                <button type="button" className="fs-btn-analyze" disabled={analyzing || !aoi} onClick={onAnalyze}>
                    {analyzing ? (
                        <>
                            <span className="fs-btn-analyze__spinner" />
                            {phase ? PHASE_LABELS[phase].toUpperCase() : 'ANALYZING'}
                        </>
                    ) : (
                        <>
                            ANALYZE AREA
                            <IconArrowRight />
                        </>
                    )}
                </button>

                {error && (
                    <div className="fs-analyze-error" role="alert">
                        <IconAlertTriangle />
                        <span>{error}</span>
                    </div>
                )}

                {floodArea !== undefined && (
                    <div className="fs-analyze-result">
                        <div className="fs-analyze-result__head">
                            DETECTED FLOOD EXTENT
                            <span className="fs-badge fs-badge--mint">
                                {Number(floodArea).toFixed(2)} KM²
                            </span>
                        </div>
                        <div className="fs-analyze-result__row">
                            <span>Flood pixels</span>
                            <span>
                                {Number(state?.results?.floodPixels ?? 0).toLocaleString()}
                            </span>
                        </div>
                        <div className="fs-analyze-result__row">
                            <span>Valid coverage</span>
                            <span>{Number(state?.results?.coveragePct ?? 0).toFixed(1)}%</span>
                        </div>
                        <div className="fs-analyze-result__row">
                            <span>Mean Δ backscatter</span>
                            <span>
                                {Number(
                                    (state?.results?.diffDb as { mean?: number } | undefined)?.mean ?? 0
                                ).toFixed(2)}{' '}
                                dB
                            </span>
                        </div>
                    </div>
                )}

                <div className="fs-step-tracker" style={{ marginBottom: 0 }}>
                    <span className="fs-eyebrow">ANALYSIS PIPELINE</span>
                    <span className="fs-step-tracker__count">
                        {phase ? PHASE_LABELS[phase] : 'IDLE'}
                    </span>
                </div>
                <div className="fs-pipeline">
                    {PIPELINE_STEPS.map((step, i) => {
                        const done = phase === 'completed';
                        const active =
                            (phase === 'selecting' && i === 0) ||
                            (phase === 'submitting_jobs' && i <= 1) ||
                            (phase === 'downloading' && i <= 1);
                        return (
                            <div
                                className={`fs-pipeline__item ${
                                    active ? 'fs-pipeline__item--active' : ''
                                } ${done ? 'fs-pipeline__item--done' : ''}`}
                                key={step}
                            >
                                <span className="fs-pipeline__check">{done ? <IconCheck /> : i + 1}</span>
                                {step}
                            </div>
                        );
                    })}
                </div>

                <hr className="fs-divider" />
                <span className="fs-eyebrow" style={{ marginBottom: 8, display: 'block' }}>
                    DATA SOURCES
                </span>
                <p className="fs-source-line">
                    Copernicus Sentinel-1 · OpenStreetMap contributors
                </p>
            </div>
        </aside>
    );
};

export type MapPlaceholderProps = {
    aoi: AreaOfInterest | null;
    onAoiSelect: (aoi: AreaOfInterest) => void;
    floodAreaKm2: number | null;
    analysisId: string | null;
    phase: AnalysisPhase | null;
};

type OsmLayerStatus = 'idle' | 'loading' | 'ok' | 'error';

const OSM_LAYER_META: Array<{
    id: OsmLayerName;
    label: string;
    color: string;
}> = [
    { id: 'roads', label: 'Roads', color: 'var(--mint)' },
    { id: 'buildings', label: 'Buildings', color: '#f0b429' },
    { id: 'settlements', label: 'Settlements', color: '#ffffff' },
];

const FLOODVIT_ENDPOINTS: Record<FloodVitLayerName, string> = {
    polygons: 'polygons',
    affectedRoads: 'affected_roads',
    affectedBridges: 'affected_bridges',
    disconnectedRoutes: 'disconnected_routes',
    disconnectedSettlements: 'disconnected_settlements',
};

const FLOODVIT_LAYER_META: Array<{
    id: FloodVitLayerName;
    label: string;
    color: string;
}> = [
    { id: 'polygons', label: 'FloodViT flood area', color: '#22d3ee' },
    { id: 'affectedRoads', label: 'Affected roads', color: '#fb923c' },
    { id: 'affectedBridges', label: 'Potentially affected bridges', color: '#f472b6' },
    { id: 'disconnectedRoutes', label: 'Disconnected routes', color: '#ef4444' },
    { id: 'disconnectedSettlements', label: 'Disconnected settlements', color: '#ef4444' },
];

const FLOODVIT_LAYER_IDS = Object.keys(FLOODVIT_ENDPOINTS) as FloodVitLayerName[];

const floodVitStatusMap = (value: OsmLayerStatus): Record<FloodVitLayerName, OsmLayerStatus> => ({
    polygons: value,
    affectedRoads: value,
    affectedBridges: value,
    disconnectedRoutes: value,
    disconnectedSettlements: value,
});

const RASTER_FILE: Record<'before' | 'flood' | 'after', string> = {
    before: 'before.png',
    flood: 'flood_mask.png',
    after: 'after.png',
};

export const MapPlaceholder: React.FC<MapPlaceholderProps> = ({
    aoi,
    onAoiSelect,
    floodAreaKm2,
    analysisId,
    phase,
}) => {
    const [activeView, setActiveView] = useState<'before' | 'flood' | 'after'>('flood');
    const [opacity, setOpacity] = useState(68);
    const [osmBundle, setOsmBundle] = useState<{
        key: string;
        data: Partial<Record<OsmLayerName, OsmFeatureCollection>>;
        status: Record<OsmLayerName, OsmLayerStatus>;
    } | null>(null);
    const [layerVisible, setLayerVisible] = useState<Record<OsmLayerName, boolean>>({
        roads: true,
        buildings: true,
        settlements: true,
    });
    const [floodVitBundle, setFloodVitBundle] = useState<{
        key: string;
        data: Partial<Record<FloodVitLayerName, OsmFeatureCollection | null>>;
        status: Record<FloodVitLayerName, OsmLayerStatus>;
    } | null>(null);
    const [floodVitVisible, setFloodVitVisible] = useState<Record<FloodVitLayerName, boolean>>({
        polygons: true,
        affectedRoads: true,
        affectedBridges: true,
        disconnectedRoutes: true,
        disconnectedSettlements: true,
    });
    // The FloodViT files are written by offline scripts *after* the analysis
    // completes, so the one-shot fetch on completion can easily 404; bumping
    // refreshTick re-runs it (LAYERS panel refresh button) without clearing
    // whatever is already on the map. `refreshing` flips in the click handler
    // and clears when the batch lands, so rows read "loading…" meanwhile.
    const [refreshTick, setRefreshTick] = useState(0);
    const [refreshing, setRefreshing] = useState(false);
    const mapRef = useRef<FloodMapHandle>(null);

    // Per-analysis vector data (OSM layers written by the pipeline, FloodViT
    // polygons written offline by scripts/floodvit/mask_to_polygons.py) only
    // exists once the phase settles on "completed". Bundles are keyed by
    // analysis id so stale data for a previous run is ignored rather than
    // cleared, and a failed fetch just marks its own row unavailable.
    const dataKey = analysisId && phase === 'completed' ? analysisId : null;
    useEffect(() => {
        if (!dataKey) return;
        let cancelled = false;
        (async () => {
            const data: Partial<Record<OsmLayerName, OsmFeatureCollection>> = {};
            const status = {
                roads: 'error' as OsmLayerStatus,
                buildings: 'error' as OsmLayerStatus,
                settlements: 'error' as OsmLayerStatus,
            };
            await Promise.all(
                OSM_LAYER_NAMES.map(async (layer) => {
                    try {
                        const res = await fetch(`/backend/osm/${dataKey}/${layer}`);
                        if (!res.ok) throw new Error(`HTTP ${res.status}`);
                        data[layer] = (await res.json()) as OsmFeatureCollection;
                        status[layer] = 'ok';
                    } catch (e) {
                        console.warn(`[flood] OSM layer ${layer} unavailable`, e);
                    }
                })
            );
            if (!cancelled) setOsmBundle({ key: dataKey, data, status });
        })();
        return () => {
            cancelled = true;
        };
    }, [dataKey]);

    useEffect(() => {
        if (!dataKey) return;
        let cancelled = false;
        (async () => {
            const data: Partial<Record<FloodVitLayerName, OsmFeatureCollection | null>> = {};
            const status = floodVitStatusMap('error');
            await Promise.all(
                FLOODVIT_LAYER_IDS.map(async (layer) => {
                    try {
                        const res = await fetch(
                            `/backend/floodvit/${dataKey}/${FLOODVIT_ENDPOINTS[layer]}`
                        );
                        if (!res.ok) throw new Error(`HTTP ${res.status}`);
                        data[layer] = (await res.json()) as OsmFeatureCollection;
                        status[layer] = 'ok';
                    } catch (e) {
                        console.warn(`[flood] FloodViT layer ${layer} unavailable`, e);
                    }
                })
            );
            if (cancelled) return;
            setFloodVitBundle({ key: dataKey, data, status });
            setRefreshing(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [dataKey, refreshTick]);

    const bundleFresh = !!osmBundle && osmBundle.key === dataKey;
    const osmData = bundleFresh ? osmBundle.data : {};
    const osmStatus: Record<OsmLayerName, OsmLayerStatus> = bundleFresh
        ? osmBundle.status
        : dataKey
            ? { roads: 'loading', buildings: 'loading', settlements: 'loading' }
            : { roads: 'idle', buildings: 'idle', settlements: 'idle' };

    const floodVitFresh = !!floodVitBundle && floodVitBundle.key === dataKey;
    const floodVitData: Partial<Record<FloodVitLayerName, OsmFeatureCollection | null>> =
        floodVitFresh ? floodVitBundle.data : {};
    const floodVitStatus: Record<FloodVitLayerName, OsmLayerStatus> =
        floodVitFresh && !refreshing
            ? floodVitBundle.status
            : floodVitStatusMap(dataKey ? 'loading' : 'idle');

    const handleRefreshLayers = () => {
        if (!dataKey || refreshing) return;
        setRefreshing(true);
        setRefreshTick((tick) => tick + 1);
    };

    const raster: RasterOverlay | null =
        analysisId && phase === 'completed' && aoi
            ? {
                  url: `/backend/rasters/${analysisId}/${RASTER_FILE[activeView]}`,
                  bounds: [aoi.bbox[0], aoi.bbox[1], aoi.bbox[2], aoi.bbox[3]],
                  opacity: opacity / 100,
              }
            : null;

    return (
        <section className="fs-map-wrap">
            <FloodMap
                ref={mapRef}
                raster={raster}
                osm={osmData}
                visible={layerVisible}
                floodVit={floodVitData}
                floodVitVisible={floodVitVisible}
            />

            <Search
                onResultSelect={(next) => {
                    onAoiSelect(next);
                    mapRef.current?.flyTo({ center: next.center, zoom: 11 });
                }}
            />

            <div className="fs-map-topbar">
                <div className="fs-location-chip">
                    <div className="fs-location-chip__title">
                        <IconPin />
                        {aoi ? aoi.name.toUpperCase() : 'NO AREA SELECTED'}
                    </div>
                    {aoi && (
                        <div className="fs-location-chip__coords">
                            {aoi.center[1].toFixed(3)}° N&nbsp;&nbsp;{aoi.center[0].toFixed(3)}° E ·
                            EPSG:4326
                        </div>
                    )}
                </div>

                {floodAreaKm2 !== null && (
                    <div className="fs-flood-alert">
                        <span className="fs-dot" />
                        FLOOD EXTENT DETECTED &nbsp;{floodAreaKm2.toFixed(1)} KM²
                    </div>
                )}
            </div>

                        <div className="fs-map-controls">
                <button className="fs-map-controls__btn" aria-label="Zoom in" onClick={() => mapRef.current?.zoomIn()}>
                    <IconPlus />
                </button>
                <button className="fs-map-controls__btn" aria-label="Zoom out" onClick={() => mapRef.current?.zoomOut()}>
                    <IconMinus />
                </button>
                <button className="fs-map-controls__btn" aria-label="Recenter" onClick={() => mapRef.current?.recenter()}>
                    <IconCrosshair />
                </button>
                <button
                    className="fs-map-controls__btn"
                    aria-label="Fullscreen"
                    onClick={() => mapRef.current?.toggleFullscreen()}
                >
                    <IconExpand />
                </button>
            </div>

                        <div className="fs-layers-panel">
                <div className="fs-layers-panel__header">
                    <IconLayers />
                    LAYERS
                    <span className="fs-layers-panel__count">
                        {String(OSM_LAYER_META.length + FLOODVIT_LAYER_META.length).padStart(2, '0')}
                    </span>
                    <button
                        type="button"
                        className="fs-layers-panel__refresh"
                        aria-label="Refresh FloodViT layers"
                        title="Refresh FloodViT layers (re-runs the offline scripts' outputs)"
                        disabled={!dataKey || refreshing}
                        onClick={handleRefreshLayers}
                    >
                        <IconRefresh />
                    </button>
                </div>
                {OSM_LAYER_META.map((layer) => {
                    const status = osmStatus[layer.id];
                    const ready = status === 'ok';
                    return (
                        <label
                            className={`fs-layer-row ${!ready ? 'fs-layer-row--disabled' : ''}`}
                            key={layer.id}
                        >
                            <input
                                type="checkbox"
                                checked={ready && layerVisible[layer.id]}
                                disabled={!ready}
                                onChange={(e) =>
                                    setLayerVisible((prev) => ({
                                        ...prev,
                                        [layer.id]: e.target.checked,
                                    }))
                                }
                            />
                            <span className="fs-layer-row__swatch" style={{ background: layer.color }} />
                            <span className="fs-layer-row__label">{layer.label}</span>
                            {status === 'loading' && (
                                <span className="fs-layer-row__badge">loading…</span>
                            )}
                            {(status === 'error' || status === 'idle') && (
                                <span className="fs-layer-row__badge">no data</span>
                            )}
                        </label>
                    );
                })}
                {FLOODVIT_LAYER_META.map((layer) => {
                    const status = floodVitStatus[layer.id];
                    const ready = status === 'ok';
                    return (
                        <label
                            className={`fs-layer-row ${!ready ? 'fs-layer-row--disabled' : ''}`}
                            key={layer.id}
                        >
                            <input
                                type="checkbox"
                                checked={ready && floodVitVisible[layer.id]}
                                disabled={!ready}
                                onChange={(e) =>
                                    setFloodVitVisible((prev) => ({
                                        ...prev,
                                        [layer.id]: e.target.checked,
                                    }))
                                }
                            />
                            <span className="fs-layer-row__swatch" style={{ background: layer.color }} />
                            <span className="fs-layer-row__label">{layer.label}</span>
                            {status === 'loading' && (
                                <span className="fs-layer-row__badge">loading…</span>
                            )}
                            {(status === 'error' || status === 'idle') && (
                                <span className="fs-layer-row__badge">no data</span>
                            )}
                        </label>
                    );
                })}
            </div>

                        <div className="fs-map-bottombar">
                <div className="fs-map-bottombar__left">
                    <div className="fs-scale-bar">
                        <span className="fs-scale-bar__line" />
                        10 km
                    </div>
                    <span>Basemap © CARTO · © OpenStreetMap contributors</span>
                    <span>ZOOM 10 · 1:250,000</span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div className="fs-map-toggle">
                        {(['before', 'flood', 'after'] as const).map((key) => (
                            <button
                                key={key}
                                className={`fs-map-toggle__btn ${activeView === key ? 'fs-map-toggle__btn--active' : ''}`}
                                onClick={() => setActiveView(key)}
                            >
                                {key === 'before' ? 'Before' : key === 'flood' ? 'Flood Map' : 'After'}
                            </button>
                        ))}
                    </div>
                    <div className="fs-opacity-control">
                        Overlay opacity
                        <input
                            type="range"
                            min={0}
                            max={100}
                            value={opacity}
                            onChange={(e) => setOpacity(Number(e.target.value))}
                        />
                        <span className="fs-opacity-control__value">{opacity}%</span>
                    </div>
                </div>
            </div>
        </section>
    );
};

export const IntelligenceSidebar: React.FC = () => {
    const [lang, setLang] = useState<'en' | 'np'>('en');

    return (
        <aside className="fs-panel fs-panel--right">
            <div className="fs-panel__inner">
                <div className="fs-intel-header">
                    <span className="fs-badge fs-badge--mint" style={{ background: 'transparent', border: 'none', padding: 0 }}>
                        <span className="fs-dot" />
                        INTELLIGENCE BRIEF
                    </span>
                    <button className="fs-intel-header__close" aria-label="Close">
                        <IconX />
                    </button>
                </div>

                <div className="fs-intel-icon">
                    <IconDoc />
                </div>

                <span className="fs-eyebrow">AUTOMATED ASSESSMENT</span>
                <h2 className="fs-heading-lg" style={{ marginTop: 6, marginBottom: 10 }}>
                    AI Situation
                    <br />
                    Report.
                </h2>

                <div className="fs-report-meta">
                    <span>KARNALI RIVER BASIN</span>
                    <span>26 AUG 2026</span>
                </div>

                <div className="fs-lang-tabs">
                    <button
                        className={`fs-lang-tabs__btn ${lang === 'en' ? 'fs-lang-tabs__btn--active' : ''}`}
                        onClick={() => setLang('en')}
                    >
                        English
                    </button>
                    <button
                        className={`fs-lang-tabs__btn ${lang === 'np' ? 'fs-lang-tabs__btn--active' : ''}`}
                        onClick={() => setLang('np')}
                    >
                        नेपाली
                    </button>
                </div>

                <p className="fs-report-text">
                    {lang === 'en' ? (
                        <>
                            Flood analysis identifies potentially affected areas from Sentinel-1
                            imagery. Infrastructure and connectivity impacts are reported once
                            verified source data becomes available.
                        </>
                    ) : (
                        <>बाढी विश्लेषणले सेन्टिनल-1 तस्बिरबाट सम्भावित प्रभावित क्षेत्र पहिचान गर्छ।</>
                    )}
                </p>

                <div className="fs-impact-note">
                    <IconInfo />
                    Potential impact only. Ground verification is required before operational decisions.
                </div>

                <button className="fs-btn-primary">
                    <IconFileCheck />
                    Generate Report
                </button>
                <button className="fs-btn-secondary">
                    <IconDownload />
                    Export PDF
                </button>

                <hr className="fs-divider" />

                <div className="fs-footnote-section">
                    <div className="fs-footnote-section__title">DATA SOURCES</div>
                    <div className="fs-footnote-section__body">
                        Copernicus Sentinel-1 · OpenStreetMap contributors
                        <br />
                        Copernicus DEM · Flood training dataset
                    </div>
                </div>

                <div className="fs-footnote-section">
                    <div className="fs-footnote-section__title">METHODOLOGY NOTE</div>
                    <div className="fs-footnote-section__body fs-footnote-section__body--accent">
                        Satellite-based potential impact assessment. Not confirmation of structural damage or
                        ground conditions.
                    </div>
                </div>
            </div>
        </aside>
    );
};

export const ImpactSummary: React.FC<{ floodAreaKm2: number | null }> = ({ floodAreaKm2 }) => {
    const stats: Array<{
        label: string;
        value: string;
        unit: string;
        color: string;
    }> = [
        {
            label: 'FLOOD AREA',
            value: floodAreaKm2 !== null ? floodAreaKm2.toFixed(1) : '—',
            unit: floodAreaKm2 !== null ? 'km²' : '',
            color: 'var(--red)',
        },
    ];

    return (
        <footer className="fs-impact-bar">
            <div className="fs-impact-bar__title">
                <div className="fs-impact-bar__kicker">IMPACT SUMMARY</div>
                <div className="fs-impact-bar__basin">Karnali River Basin</div>
                <div className="fs-impact-bar__date">26 AUG 2026 · SAR ANALYSIS</div>
            </div>

            <div className="fs-impact-stats">
                {stats.map((stat) => (
                    <div className="fs-stat" key={stat.label}>
                        <span className="fs-stat__dot" style={{ background: stat.color }} />
                        <div>
                            <div className="fs-stat__label">{stat.label}</div>
                            <div>
                                <span className="fs-stat__value">{stat.value}</span>
                                {stat.unit && <span className="fs-stat__unit">{stat.unit}</span>}
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </footer>
    );
};

export const DashboardLayout: React.FC = () => {
    const [date, setDate] = useState('2026-08-26');
    const [aoi, setAoi] = useState<AreaOfInterest | null>(null);
    const [analyzing, setAnalyzing] = useState(false);
    const [state, setState] = useState<AnalysisState | null>(null);
    const [phase, setPhase] = useState<AnalysisPhase | null>(null);
    const [analysisId, setAnalysisId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const handleAnalyze = async () => {
        if (analyzing || !aoi) return;
        setAnalyzing(true);
        setError(null);
        setState(null);
        setPhase('queued');
        setAnalysisId(null);

        try {
            const res = await fetch('/backend/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bbox: aoi.bbox, date }),
            });
            if (!res.ok) throw new BackendUnavailableError(res.status);
            const created = (await res.json()) as { analysisId: string };
            setAnalysisId(created.analysisId);
            console.info('[flood] analysis queued', created.analysisId, aoi.bbox, date);

            const deadline = Date.now() + POLL_TIMEOUT_MS;
            let settled = false;
            while (Date.now() < deadline) {
                await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));

                const pollRes = await fetch(`/backend/analyze/${created.analysisId}`);
                if (!pollRes.ok) throw new BackendUnavailableError(pollRes.status);
                const next = (await pollRes.json()) as AnalysisState;
                setState(next);
                setPhase(next.phase);

                if (next.phase === 'completed') {
                    const results = next.results ?? {};
                    console.group(`[flood] analysis ${created.analysisId} results`);
                    console.table(results);
                    console.log('acquisitions', next.before, next.after);
                    console.groupEnd();
                    settled = true;
                    break;
                }
                if (next.phase === 'error') {
                    const rawErr = next.error || 'Analysis failed';
                    const safeErr = rawErr.replace(/[^\x00-\x7F]/g, ' ');
                    // Backend-reported failures are expected outcomes, not
                    // crashes: show them inline instead of throwing (a thrown
                    // Error + console.error surfaces as a Next dev overlay).
                    setError(safeErr);
                    settled = true;
                    break;
                }
            }

            if (!settled) {
                setError(
                    `Analysis ${created.analysisId} did not finish within ` +
                        `${POLL_TIMEOUT_MS / 60000} min. It may still be running - ` +
                        'check the backend terminal.'
                );
            }
        } catch (e) {
            const rawMessage = e instanceof Error ? e.message : 'Analysis failed';
            const message = rawMessage.replace(/[^\x00-\x7F]/g, ' ');
            setError(message);
            console.warn('[flood] analysis failed:', message);
        } finally {
            setAnalyzing(false);
        }
    };

    const floodAreaKm2 =
        typeof state?.results?.floodAreaKm2 === 'number'
            ? (state.results.floodAreaKm2 as number)
            : null;

    return (
        <div className="fs-dashboard">
            <TopNavbar />
            <div className="fs-body">
                <AnalysisSidebar
                    aoi={aoi}
                    date={date}
                    onDateChange={setDate}
                    analyzing={analyzing}
                    phase={phase}
                    state={state}
                    error={error}
                    onAnalyze={handleAnalyze}
                />
                <MapPlaceholder
                    aoi={aoi}
                    onAoiSelect={setAoi}
                    floodAreaKm2={floodAreaKm2}
                    analysisId={analysisId}
                    phase={phase}
                />
                <IntelligenceSidebar />
            </div>
            <ImpactSummary floodAreaKm2={floodAreaKm2} />
        </div>
    );
};

export default DashboardLayout;