'use client'
import React, { useState, useRef } from 'react';
import './globals.css';
import { FloodMap } from './map';
import type { FloodMapHandle, FloodLayerKey } from './map';

/* ============================================================================
   ICONS — small inline SVGs, no external icon library required
   ============================================================================ */
type IconProps = { size?: number };

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

const IconSatellite = ({ size = 15 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M13 7l4 4-7.5 7.5a2.8 2.8 0 01-4-4L13 7z" />
        <path d="M16 4l4 4M18.5 1.5l4 4M2 22l3.5-3.5" strokeLinecap="round" />
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

const IconLink = ({ size = 13 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
        <path d="M7 17l10-10M9 7h8v8" strokeLinecap="round" strokeLinejoin="round" />
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

/* ============================================================================
   TOP NAVBAR
   ============================================================================ */
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

/* ============================================================================
   LEFT SIDEBAR — ANALYSIS SIDEBAR
   ============================================================================ */
const PIPELINE_STEPS = [
    'Satellite data found',
    'Before / after imagery',
    'Flood segmentation',
    'Infrastructure analysis',
    'Connectivity analysis',
];

export const AnalysisSidebar: React.FC = () => {
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
                    <button className="fs-selector">
                        <IconMap size={16} />
                        <span className="fs-selector__body">
                            <span className="fs-selector__title">Karnali River Basin</span>
                        </span>
                        <IconArrowRight size={14} />
                    </button>
                    <div className="fs-field__sublabel">AOI selected · 148.2 km² coverage</div>
                </div>

                <div className="fs-field">
                    <span className="fs-field__label">02 &nbsp;EVENT</span>
                    <div className="fs-field__sublabel" style={{ marginTop: 0, marginBottom: 8 }}>
                        Event Date
                    </div>
                    <div className="fs-date-input">
                        <span>26-08-2026</span>
                        <span className="fs-date-input__icon">
                            <IconCalendar />
                        </span>
                    </div>
                </div>

                <div className="fs-field">
                    <span className="fs-field__label">03 &nbsp;SATELLITE</span>
                    <div className="fs-selector">
                        <span className="fs-selector__icon">
                            <IconSatellite />
                        </span>
                        <span className="fs-selector__body">
                            <span className="fs-selector__title">Sentinel-1 SAR</span>
                            <span className="fs-selector__subtitle">Cloud tolerant · C-band radar</span>
                        </span>
                        <span className="fs-selector__status-dot" />
                    </div>
                </div>

                <div className="fs-field">
                    <span className="fs-field__label">04 &nbsp;IMAGERY</span>
                    <div className="fs-imagery-grid">
                        <div className="fs-imagery-card">
                            <div className="fs-imagery-card__label">BEFORE</div>
                            <div className="fs-imagery-card__date">23 AUG 2026</div>
                            <div className="fs-imagery-card__status">
                                <span className="fs-selector__status-dot" />
                                Available acquisition
                            </div>
                        </div>
                        <div className="fs-imagery-card">
                            <div className="fs-imagery-card__label">AFTER</div>
                            <div className="fs-imagery-card__date">29 AUG 2026</div>
                            <div className="fs-imagery-card__status">
                                <span className="fs-selector__status-dot" />
                                Available acquisition
                            </div>
                        </div>
                    </div>
                </div>

                <button className="fs-btn-analyze">
                    ANALYZE AREA
                    <IconArrowRight />
                </button>

                <div className="fs-step-tracker" style={{ marginBottom: 0 }}>
                    <span className="fs-eyebrow">ANALYSIS PIPELINE</span>
                    <span className="fs-step-tracker__count">5 / 5</span>
                </div>
                <div className="fs-pipeline">
                    {PIPELINE_STEPS.map((step) => (
                        <div className="fs-pipeline__item" key={step}>
                            <span className="fs-pipeline__check">
                                <IconCheck />
                            </span>
                            {step}
                        </div>
                    ))}
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

/* ============================================================================
   CENTER WORKSPACE — MAP PLACEHOLDER
   Only the dashboard chrome is rendered. The central canvas itself stays an
   empty, dark, reserved region — no basemap, terrain, markers or mapping
   library are rendered here. It is isolated and ready for a MapLibre GL JS
   instance to be mounted into it later.
   ============================================================================ */
export const MapPlaceholder: React.FC = () => {
    const [settlementOpen, setSettlementOpen] = useState(true);
    const [activeView, setActiveView] = useState<'before' | 'flood' | 'after'>('flood');
    const [opacity, setOpacity] = useState(68);
    const mapRef = useRef<FloodMapHandle>(null);

    const [layerVisibility, setLayerVisibility] = useState<Record<FloodLayerKey, boolean>>({
        flood: true,
        roads: true,
        bridges: true,
        hospitals: true,
        settlements: true,
    });

    const toggleLayer = (id: FloodLayerKey) => {
        setLayerVisibility((prev) => ({ ...prev, [id]: !prev[id] }));
    };

    // 'Buildings' and 'Connectivity' have no sample data yet, so their toggles
    // stay visible but disabled rather than wired to a layer that doesn't exist.
    const layers: Array<{
        id: FloodLayerKey | 'buildings' | 'connectivity';
        label: string;
        color: string;
        hasData: boolean;
    }> = [
            { id: 'flood', label: 'Flood extent', color: 'var(--red)', hasData: true },
            { id: 'roads', label: 'Affected roads', color: 'var(--orange)', hasData: true },
            { id: 'bridges', label: 'Bridges', color: 'var(--blue)', hasData: true },
            { id: 'buildings', label: 'Buildings', color: 'var(--text-secondary)', hasData: false },
            { id: 'settlements', label: 'Settlements', color: 'var(--purple)', hasData: true },
            { id: 'connectivity', label: 'Connectivity', color: 'var(--mint)', hasData: false },
            { id: 'hospitals', label: 'Hospitals', color: 'var(--blue)', hasData: true },
        ];

    return (
        <section className="fs-map-wrap">
            {/* Functional MapLibre map. All floating dashboard chrome below is
          layered on top of it, unchanged. */}
            <FloodMap ref={mapRef} visibleLayers={layerVisibility} />

            {/* Top overlay: location + flood status */}
            <div className="fs-map-topbar">
                <div className="fs-location-chip">
                    <div className="fs-location-chip__title">
                        <IconPin />
                        KARNALI RIVER BASIN · NEPAL
                    </div>
                    <div className="fs-location-chip__coords">28°48&apos; N&nbsp;&nbsp;81°36&apos; E · EPSG:4326</div>
                </div>

                <div className="fs-flood-alert">
                    <span className="fs-dot" />
                    FLOOD EXTENT DETECTED &nbsp;12.4 KM²
                </div>
            </div>

            <div className="fs-view-state">
                <span className="fs-view-state__label">VIEW STATE</span>
                <span className="fs-view-state__value">Settlement selected</span>
                <IconChevronDown size={12} />
            </div>

            {/* Settlement intelligence popup — illustrative sample content only */}
            {settlementOpen && (
                <div className="fs-settlement-card">
                    <div className="fs-settlement-card__header">
                        <span className="fs-settlement-card__kicker">SETTLEMENT INTELLIGENCE</span>
                        <button className="fs-settlement-card__close" onClick={() => setSettlementOpen(false)} aria-label="Close">
                            <IconX />
                        </button>
                    </div>
                    <div className="fs-settlement-card__body">
                        <div className="fs-settlement-card__title">Settlement: Chisapani</div>
                        <span className="fs-badge fs-badge--purple">
                            <IconAlertTriangle />
                            POTENTIALLY CUT OFF
                        </span>

                        <div className="fs-settlement-card__rows">
                            <div className="fs-settlement-card__row">
                                <span className="fs-settlement-card__row-label">Nearest hospital</span>
                                <span className="fs-settlement-card__row-value">District Hospital</span>
                            </div>
                            <div className="fs-settlement-card__row">
                                <span className="fs-settlement-card__row-label">Road connection</span>
                                <span className="fs-settlement-card__row-value">No available mapped route</span>
                            </div>
                            <div className="fs-settlement-card__row">
                                <span className="fs-settlement-card__row-label">Affected road segments</span>
                                <span className="fs-settlement-card__row-value">3 segments</span>
                            </div>
                            <div className="fs-settlement-card__row">
                                <span className="fs-settlement-card__row-label">Distance</span>
                                <span className="fs-settlement-card__row-value">12.6 km</span>
                            </div>
                        </div>

                        <button className="fs-btn-trace">
                            <IconLink />
                            TRACE CONNECTIVITY
                            <IconArrowRight size={12} />
                        </button>
                    </div>
                </div>
            )}

            {/* Zoom / tool controls — wired to the FloodMap instance via ref */}
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

            {/* Layers panel — doubles as the map legend and drives FloodMap's layer visibility */}
            <div className="fs-layers-panel">
                <div className="fs-layers-panel__header">
                    <IconLayers />
                    LAYERS
                    <span className="fs-layers-panel__count">{String(layers.length).padStart(2, '0')}</span>
                </div>
                {layers.map((layer) => (
                    <label
                        className={`fs-layer-row ${!layer.hasData ? 'fs-layer-row--disabled' : ''}`}
                        key={layer.label}
                    >
                        <input
                            type="checkbox"
                            checked={layer.hasData ? layerVisibility[layer.id as FloodLayerKey] : false}
                            disabled={!layer.hasData}
                            onChange={() => layer.hasData && toggleLayer(layer.id as FloodLayerKey)}
                        />
                        <span className="fs-layer-row__swatch" style={{ background: layer.color }} />
                        {layer.label}
                        {!layer.hasData && <span className="fs-layer-row__badge">no data</span>}
                    </label>
                ))}
            </div>

            {/* Bottom toolbar: scale, attribution, before/flood/after toggle, opacity */}
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
                        Flood opacity
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

/* ============================================================================
   RIGHT SIDEBAR — INTELLIGENCE SIDEBAR
   ============================================================================ */
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
                            Flood analysis identifies approximately <b>12.4 km²</b> of potentially affected
                            area. 18.7 km of mapped roads and 4 bridges intersect the detected flood extent.
                            7 settlements have no remaining mapped road connection to their nearest reference
                            location after affected road segments are excluded.
                        </>
                    ) : (
                        <>बाढी विश्लेषणले लगभग १२.४ वर्ग कि.मि. प्रभावित क्षेत्र पहिचान गरेको छ।</>
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

/* ============================================================================
   BOTTOM — IMPACT SUMMARY
   ============================================================================ */
const IMPACT_STATS: Array<{
    label: string;
    value: string;
    unit: string;
    color: string;
}> = [
        { label: 'FLOOD AREA', value: '12.4', unit: 'km²', color: 'var(--red)' },
        { label: 'AFFECTED ROADS', value: '18.7', unit: 'km', color: 'var(--orange)' },
        { label: 'AFFECTED BRIDGES', value: '04', unit: '', color: 'var(--blue)' },
        { label: 'POTENTIALLY CUT-OFF', value: '07', unit: 'settlements', color: 'var(--purple)' },
    ];

export const ImpactSummary: React.FC = () => {
    return (
        <footer className="fs-impact-bar">
            <div className="fs-impact-bar__title">
                <div className="fs-impact-bar__kicker">IMPACT SUMMARY</div>
                <div className="fs-impact-bar__basin">Karnali River Basin</div>
                <div className="fs-impact-bar__date">26 AUG 2026 · SAR ANALYSIS</div>
            </div>

            <div className="fs-impact-stats">
                {IMPACT_STATS.map((stat) => (
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

/* ============================================================================
   DASHBOARD LAYOUT — top-level composition
   ============================================================================ */
export const DashboardLayout: React.FC = () => {
    return (
        <div className="fs-dashboard">
            <TopNavbar />
            <div className="fs-body">
                <AnalysisSidebar />
                <MapPlaceholder />
                <IntelligenceSidebar />
            </div>
            <ImpactSummary />
        </div>
    );
};

export default DashboardLayout;