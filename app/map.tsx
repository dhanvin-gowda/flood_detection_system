'use client'
import React, {
    forwardRef,
    useEffect,
    useImperativeHandle,
    useRef,
    useState,
} from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { ensureMarkerIcons } from './markerIcons';
import {
    buildMarkerPopup,
    type MarkerPopupContext,
    type MarkerPopupKind,
} from './markerPopups';


export const DEFAULT_STYLE_URL: string = (() => {
    const maptilerKey = process.env.NEXT_PUBLIC_MAPTILER_KEY;
    if (maptilerKey) {
        return `https://api.maptiler.com/maps/hybrid/style.json?key=${maptilerKey}`;
    }
    const mapboxToken = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    if (mapboxToken) {
        return `https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12?access_token=${mapboxToken}`;
    }
    return 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
})();

const INITIAL_CENTER: [number, number] = [0, 20];
const INITIAL_ZOOM = 1.2;

export type OsmLayerName = 'roads' | 'buildings' | 'settlements';

export const OSM_LAYER_NAMES: OsmLayerName[] = ['roads', 'buildings', 'settlements'];

// Structural stand-in for the GeoJSON types: @types/geojson is not a direct
// dependency of this package, so maplibre's ambient GeoJSON namespace is only
// visible under skipLibCheck and cannot be referenced from app code.
export interface OsmFeatureCollection {
    type: 'FeatureCollection';
    features: Array<{
        type: 'Feature';
        id?: string;
        properties: Record<string, unknown> | null;
        geometry:
            | { type: 'Point'; coordinates: [number, number] }
            | { type: 'LineString'; coordinates: [number, number][] }
            | { type: 'Polygon'; coordinates: [number, number][][] };
    }>;
    properties?: Record<string, unknown>;
}

const OSM_STYLE: Record<OsmLayerName, { color: string }> = {
    roads: { color: '#7fe8b8' },
    buildings: { color: '#f0b429' },
    settlements: { color: '#ffffff' },
};

const RASTER_SOURCE_ID = 'fs-raster';
const RASTER_LAYER_ID = 'fs-raster-layer';
const OSM_SOURCE_ID: Record<OsmLayerName, string> = {
    roads: 'fs-osm-roads',
    buildings: 'fs-osm-buildings',
    settlements: 'fs-osm-settlements',
};
const OSM_LAYER_ID: Record<OsmLayerName, string[]> = {
    roads: ['fs-osm-roads'],
    buildings: ['fs-osm-buildings'],
    settlements: ['fs-osm-settlements', 'fs-osm-settlements-icon', 'fs-osm-settlements-label'],
};

export const FLOODVIT_LAYER_NAMES = [
    'polygons',
    'affectedRoads',
    'affectedBridges',
    'disconnectedRoutes',
    'disconnectedSettlements',
    'hospitals',
    'hospitalRoute',
] as const;

export type FloodVitLayerName = (typeof FLOODVIT_LAYER_NAMES)[number];

interface FloodVitSpec {
    sourceId: string;
    /** Every map layer fed by the source; visibility is applied to all of them. */
    layerIds: string[];
    addLayers: (map: maplibregl.Map, sourceId: string) => void;
}

const FLOODVIT_SPECS: Record<FloodVitLayerName, FloodVitSpec> = {
    polygons: {
        sourceId: 'fs-floodvit',
        layerIds: ['fs-floodvit-fill'],
        addLayers: (map, sourceId) => {
            map.addLayer({
                id: 'fs-floodvit-fill',
                type: 'fill',
                source: sourceId,
                paint: {
                    'fill-color': '#22d3ee',
                    'fill-opacity': 0.45,
                    'fill-outline-color': '#a5f3fc',
                },
            });
        },
    },
    affectedRoads: {
        sourceId: 'fs-fv-affected-roads',
        layerIds: ['fs-fv-affected-roads'],
        addLayers: (map, sourceId) => {
            map.addLayer({
                id: 'fs-fv-affected-roads',
                type: 'line',
                source: sourceId,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': '#fb923c',
                    'line-opacity': 0.95,
                    'line-width': 2.5,
                },
            });
        },
    },
    affectedBridges: {
        sourceId: 'fs-fv-affected-bridges',
        layerIds: ['fs-fv-affected-bridges', 'fs-fv-affected-bridges-icon'],
        addLayers: (map, sourceId) => {
            map.addLayer({
                id: 'fs-fv-affected-bridges',
                type: 'line',
                source: sourceId,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': '#f472b6',
                    'line-opacity': 1,
                    'line-width': 4,
                },
            });
            try {
                // One marker per bridge at the centre of its real LineString —
                // no coordinates are invented or rewritten for the icon.
                map.addLayer({
                    id: 'fs-fv-affected-bridges-icon',
                    type: 'symbol',
                    source: sourceId,
                    layout: {
                        'symbol-placement': 'line-center',
                        'icon-image': 'fs-icon-bridge',
                        'icon-size': 0.6,
                        'icon-anchor': 'center',
                        'icon-allow-overlap': true,
                        'icon-ignore-placement': true,
                        'icon-rotation-alignment': 'viewport',
                    },
                });
            } catch (e) {
                console.warn('FloodMap: bridge markers unavailable', e);
            }
        },
    },
    disconnectedRoutes: {
        sourceId: 'fs-fv-disconnected-routes',
        layerIds: ['fs-fv-disconnected-routes'],
        addLayers: (map, sourceId) => {
            map.addLayer({
                id: 'fs-fv-disconnected-routes',
                type: 'line',
                source: sourceId,
                paint: {
                    'line-color': '#ef4444',
                    'line-opacity': 0.95,
                    'line-width': 2.2,
                    'line-dasharray': [1.5, 1.2],
                },
            });
        },
    },
    disconnectedSettlements: {
        sourceId: 'fs-fv-disconnected-settlements',
        layerIds: [
            'fs-fv-disconnected-settlements',
            'fs-fv-disconnected-settlements-icon',
            'fs-fv-disconnected-settlements-label',
        ],
        addLayers: (map, sourceId) => {
            map.addLayer({
                id: 'fs-fv-disconnected-settlements',
                type: 'circle',
                source: sourceId,
                paint: {
                    'circle-radius': 6,
                    'circle-color': '#ef4444',
                    'circle-stroke-color': '#0f172a',
                    'circle-stroke-width': 1.5,
                },
            });
            try {
                // Distinct warning marker for potentially cut-off settlements.
                map.addLayer({
                    id: 'fs-fv-disconnected-settlements-icon',
                    type: 'symbol',
                    source: sourceId,
                    layout: {
                        'icon-image': 'fs-icon-disconnected',
                        'icon-size': 0.65,
                        'icon-anchor': 'center',
                        'icon-allow-overlap': true,
                        'icon-ignore-placement': true,
                    },
                });
            } catch (e) {
                console.warn('FloodMap: disconnected settlement markers unavailable', e);
            }
            try {
                map.addLayer({
                    id: 'fs-fv-disconnected-settlements-label',
                    type: 'symbol',
                    source: sourceId,
                    layout: {
                        'text-field': ['coalesce', ['get', 'name'], ''],
                        'text-size': 11,
                        'text-offset': [0, 1.25],
                        'text-anchor': 'top',
                        'text-optional': true,
                    },
                    paint: {
                        'text-color': '#fecaca',
                        'text-halo-color': 'rgba(15, 23, 42, 0.9)',
                        'text-halo-width': 1.3,
                    },
                });
            } catch (e) {
                console.warn('FloodMap: disconnected settlement labels unavailable', e);
            }
        },
    },
    hospitals: {
        sourceId: 'fs-fv-hospitals',
        layerIds: [
            'fs-fv-hospitals',
            'fs-fv-hospitals-selected',
            'fs-fv-hospitals-icon',
            'fs-fv-hospitals-label',
        ],
        addLayers: (map, sourceId) => {
            map.addLayer({
                id: 'fs-fv-hospitals',
                type: 'circle',
                source: sourceId,
                paint: {
                    'circle-radius': 5,
                    'circle-color': '#38bdf8',
                    'circle-opacity': 0.85,
                    'circle-stroke-color': '#0f172a',
                    'circle-stroke-width': 1.5,
                },
            });
            map.addLayer({
                id: 'fs-fv-hospitals-selected',
                type: 'circle',
                source: sourceId,
                filter: ['==', ['get', 'selected'], true],
                // Selection plate sized to sit behind the 22px icon badge (the
                // old r9 disc would have been covered by it); same semantics.
                paint: {
                    'circle-radius': 17,
                    'circle-color': '#22d3ee',
                    'circle-stroke-color': '#ffffff',
                    'circle-stroke-width': 2.5,
                },
            });
            try {
                map.addLayer({
                    id: 'fs-fv-hospitals-icon',
                    type: 'symbol',
                    source: sourceId,
                    layout: {
                        'icon-image': 'fs-icon-hospital',
                        'icon-size': 0.7,
                        'icon-anchor': 'center',
                        'icon-allow-overlap': true,
                        'icon-ignore-placement': true,
                    },
                });
            } catch (e) {
                console.warn('FloodMap: hospital markers unavailable', e);
            }
            try {
                map.addLayer({
                    id: 'fs-fv-hospitals-label',
                    type: 'symbol',
                    source: sourceId,
                    filter: ['==', ['get', 'selected'], true],
                    layout: {
                        'text-field': ['coalesce', ['get', 'name'], 'Hospital'],
                        'text-size': 12,
                        'text-offset': [0, 1.25],
                        'text-anchor': 'top',
                        'text-optional': true,
                    },
                    paint: {
                        'text-color': '#a5f3fc',
                        'text-halo-color': 'rgba(15, 23, 42, 0.9)',
                        'text-halo-width': 1.3,
                    },
                });
            } catch (e) {
                console.warn('FloodMap: hospital labels unavailable', e);
            }
        },
    },
    hospitalRoute: {
        sourceId: 'fs-fv-hospital-route',
        layerIds: ['fs-fv-hospital-route-casing', 'fs-fv-hospital-route'],
        addLayers: (map, sourceId) => {
            map.addLayer({
                id: 'fs-fv-hospital-route-casing',
                type: 'line',
                source: sourceId,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': '#0f172a',
                    'line-opacity': 0.85,
                    'line-width': 7,
                },
            });
            map.addLayer({
                id: 'fs-fv-hospital-route',
                type: 'line',
                source: sourceId,
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                paint: {
                    'line-color': '#4ade80',
                    'line-opacity': 1,
                    'line-width': 4,
                },
            });
        },
    },
};

// Marker/icon + label layers that open a popup when clicked, in hit-test
// priority order (first match wins, so the disconnected warning always beats
// the plain settlement marker drawn beneath it). Layers that do not exist are
// skipped, and layers with visibility "none" never match.
const MARKER_LAYERS: Array<{ id: string; kind: MarkerPopupKind }> = [
    { id: 'fs-fv-disconnected-settlements-icon', kind: 'disconnected' },
    { id: 'fs-fv-disconnected-settlements-label', kind: 'disconnected' },
    { id: 'fs-fv-disconnected-settlements', kind: 'disconnected' },
    { id: 'fs-fv-hospitals-icon', kind: 'hospital' },
    { id: 'fs-fv-hospitals-label', kind: 'hospital' },
    { id: 'fs-fv-hospitals-selected', kind: 'hospital' },
    { id: 'fs-fv-hospitals', kind: 'hospital' },
    { id: 'fs-fv-affected-bridges-icon', kind: 'bridge' },
    { id: 'fs-osm-settlements-icon', kind: 'settlement' },
    { id: 'fs-osm-settlements-label', kind: 'settlement' },
    { id: 'fs-osm-settlements', kind: 'settlement' },
];

export interface RasterOverlay {
    /** Stretched preview PNG from the backend (before.png / after.png / flood_mask.png). */
    url: string;
    /** [west, south, east, north] — the grid the raster was written on. */
    bounds: [number, number, number, number];
    /** 0..1 */
    opacity: number;
}

export interface FloodMapProps {

    styleUrl?: string;

    className?: string;

    /** Georeferenced preview image drawn above the basemap; null hides it. */
    raster?: RasterOverlay | null;

    /** OSM feature collections per layer; absent layers are not drawn. */
    osm?: Partial<Record<OsmLayerName, OsmFeatureCollection | null>>;

    /** Per-layer checkbox state; defaults to visible when data is present. */
    visible?: Partial<Record<OsmLayerName, boolean>>;

    /** FloodViT offline outputs per layer; absent layers are not drawn. */
    floodVit?: Partial<Record<FloodVitLayerName, OsmFeatureCollection | null>>;

    /** Per-layer checkbox state for the FloodViT outputs; defaults to visible when data is present. */
    floodVitVisible?: Partial<Record<FloodVitLayerName, boolean>>;
}

export interface FloodMapHandle {
    zoomIn: () => void;
    zoomOut: () => void;
    recenter: () => void;
    resize: () => void;
    toggleFullscreen: () => void;
    flyTo: (opts: { center?: [number, number]; zoom?: number; duration?: number }) => void;
}

type MapCorner = [number, number];

function rasterCoordinates(
    bounds: [number, number, number, number]
): [MapCorner, MapCorner, MapCorner, MapCorner] {
    const [west, south, east, north] = bounds;
    // ImageSource wants corners clockwise starting top-left.
    return [
        [west, north],
        [east, north],
        [east, south],
        [west, south],
    ];
}

export const FloodMap = forwardRef<FloodMapHandle, FloodMapProps>(
    ({ styleUrl = DEFAULT_STYLE_URL, className, raster, osm, visible, floodVit, floodVitVisible }, ref) => {
        const containerRef = useRef<HTMLDivElement | null>(null);
        const mapInstanceRef = useRef<maplibregl.Map | null>(null);
        const [loading, setLoading] = useState(true);
        const [loadError, setLoadError] = useState<string | null>(null);

        const propsRef = useRef({ raster, osm, visible, floodVit, floodVitVisible });
        propsRef.current = { raster, osm, visible, floodVit, floodVitVisible };
        const rasterUrlRef = useRef<string | null>(null);
        const rasterBoundsRef = useRef<string | null>(null);
        const rasterOpacityRef = useRef<number | null>(null);
        // Single reusable marker popup; popupLayerRef remembers which marker
        // layer it is anchored to so it can be closed if that layer is hidden
        // or removed.
        const popupRef = useRef<maplibregl.Popup | null>(null);
        const popupLayerRef = useRef<string | null>(null);
        // Gate on style-JSON readiness only. map.isStyleLoaded() is false
        // whenever any tile/glyph/image request is in flight (maplibre's
        // Style.loaded checks every tile manager + the image manager), which
        // would make overlay syncs silently skip the renders that matter.
        const styleReadyRef = useRef(false);

        const syncRaster = (map: maplibregl.Map) => {
            const overlay = propsRef.current.raster ?? null;
            const hasSource = !!map.getSource(RASTER_SOURCE_ID);

            if (!overlay) {
                if (map.getLayer(RASTER_LAYER_ID)) map.removeLayer(RASTER_LAYER_ID);
                if (hasSource) map.removeSource(RASTER_SOURCE_ID);
                rasterUrlRef.current = null;
                rasterBoundsRef.current = null;
                rasterOpacityRef.current = null;
                return false;
            }

            const key = overlay.bounds.join(',');
            const urlChanged = rasterUrlRef.current !== overlay.url;
            const boundsChanged = rasterBoundsRef.current !== key;

            if (!hasSource) {
                map.addSource(RASTER_SOURCE_ID, {
                    type: 'image',
                    url: overlay.url,
                    coordinates: rasterCoordinates(overlay.bounds),
                });
                map.addLayer({
                    id: RASTER_LAYER_ID,
                    type: 'raster',
                    source: RASTER_SOURCE_ID,
                    paint: {
                        'raster-opacity': overlay.opacity,
                        'raster-fade-duration': 0,
                    },
                });
            } else {
                const source = map.getSource(RASTER_SOURCE_ID) as maplibregl.ImageSource;
                if (urlChanged) {
                    source.updateImage({
                        url: overlay.url,
                        coordinates: rasterCoordinates(overlay.bounds),
                    });
                } else if (boundsChanged) {
                    source.setCoordinates(rasterCoordinates(overlay.bounds));
                }
            }
            if (rasterOpacityRef.current !== overlay.opacity) {
                map.setPaintProperty(RASTER_LAYER_ID, 'raster-opacity', overlay.opacity);
            }
            rasterUrlRef.current = overlay.url;
            rasterBoundsRef.current = key;
            rasterOpacityRef.current = overlay.opacity;
            return true;
        };

        const removeOsm = (map: maplibregl.Map, name: OsmLayerName) => {
            for (const id of OSM_LAYER_ID[name]) {
                if (map.getLayer(id)) map.removeLayer(id);
            }
            if (map.getSource(OSM_SOURCE_ID[name])) map.removeSource(OSM_SOURCE_ID[name]);
        };

        const syncOsm = (map: maplibregl.Map, name: OsmLayerName): boolean => {
            const data = propsRef.current.osm?.[name] ?? null;
            const show = propsRef.current.visible?.[name] ?? true;
            if (!data) {
                removeOsm(map, name);
                return false;
            }

            let added = false;
            const sourceId = OSM_SOURCE_ID[name];
            if (!map.getSource(sourceId)) {
                added = true;
                map.addSource(sourceId, { type: 'geojson', data });
                const color = OSM_STYLE[name].color;
                if (name === 'roads') {
                    map.addLayer({
                        id: 'fs-osm-roads',
                        type: 'line',
                        source: sourceId,
                        layout: { 'line-cap': 'round', 'line-join': 'round' },
                        paint: {
                            'line-color': color,
                            'line-opacity': 0.9,
                            'line-width': [
                                'match',
                                ['get', 'highway'],
                                ['motorway', 'trunk', 'primary'], 3.5,
                                ['secondary'], 2.5,
                                ['tertiary', 'residential', 'unclassified', 'service'], 1.6,
                                1.1,
                            ],
                        },
                    });
                } else if (name === 'buildings') {
                    map.addLayer({
                        id: 'fs-osm-buildings',
                        type: 'fill',
                        source: sourceId,
                        paint: {
                            'fill-color': color,
                            'fill-opacity': 0.4,
                            'fill-outline-color': '#fcd34d',
                        },
                    });
                } else {
                    map.addLayer({
                        id: 'fs-osm-settlements',
                        type: 'circle',
                        source: sourceId,
                        paint: {
                            'circle-radius': 5.5,
                            'circle-color': color,
                            'circle-stroke-color': '#0f172a',
                            'circle-stroke-width': 1.5,
                        },
                    });
                    try {
                        map.addLayer({
                            id: 'fs-osm-settlements-icon',
                            type: 'symbol',
                            source: sourceId,
                            layout: {
                                'icon-image': 'fs-icon-settlement',
                                'icon-size': 0.55,
                                'icon-anchor': 'center',
                                'icon-allow-overlap': true,
                                'icon-ignore-placement': true,
                            },
                        });
                    } catch (e) {
                        console.warn('FloodMap: settlement markers unavailable', e);
                    }
                    try {
                        map.addLayer({
                            id: 'fs-osm-settlements-label',
                            type: 'symbol',
                            source: sourceId,
                            layout: {
                                'text-field': ['coalesce', ['get', 'name'], ''],
                                'text-size': 11,
                                'text-offset': [0, 1.25],
                                'text-anchor': 'top',
                                'text-optional': true,
                            },
                            paint: {
                                'text-color': '#ffffff',
                                'text-halo-color': 'rgba(15, 23, 42, 0.9)',
                                'text-halo-width': 1.3,
                            },
                        });
                    } catch (e) {
                        console.warn('FloodMap: settlement labels unavailable', e);
                    }
                }
            } else {
                (map.getSource(sourceId) as maplibregl.GeoJSONSource).setData(data);
            }

            const visibility = show ? 'visible' : 'none';
            for (const id of OSM_LAYER_ID[name]) {
                if (map.getLayer(id)) {
                    map.setLayoutProperty(id, 'visibility', visibility);
                }
            }
            return added;
        };

        const syncFloodVit = (map: maplibregl.Map): boolean => {
            let added = false;
            for (const name of FLOODVIT_LAYER_NAMES) {
                const spec = FLOODVIT_SPECS[name];
                const data = propsRef.current.floodVit?.[name] ?? null;
                const show = propsRef.current.floodVitVisible?.[name] ?? true;
                if (!data) {
                    for (const id of spec.layerIds) {
                        if (map.getLayer(id)) map.removeLayer(id);
                    }
                    if (map.getSource(spec.sourceId)) map.removeSource(spec.sourceId);
                    continue;
                }

                if (!map.getSource(spec.sourceId)) {
                    added = true;
                    map.addSource(spec.sourceId, { type: 'geojson', data });
                    spec.addLayers(map, spec.sourceId);
                } else {
                    (map.getSource(spec.sourceId) as maplibregl.GeoJSONSource).setData(data);
                }

                const visibility = show ? 'visible' : 'none';
                for (const id of spec.layerIds) {
                    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility);
                }
            }
            return added;
        };

        const applyOverlays = () => {
            const map = mapInstanceRef.current;
            if (!map || !styleReadyRef.current) return;
            try {
                const addedRaster = syncRaster(map);
                const addedFloodVit = syncFloodVit(map);
                let addedOsm = false;
                for (const name of OSM_LAYER_NAMES) {
                    addedOsm = syncOsm(map, name) || addedOsm;
                }
                if (addedRaster || addedFloodVit || addedOsm) {
                    // Added sources land on top of everything; restack so the
                    // order stays basemap -> raster -> FloodViT flood area ->
                    // OSM roads/buildings/settlements (+ marker icons) ->
                    // FloodViT affected roads -> bridges (+ marker icons) ->
                    // disconnected routes/settlements (+ warning icons) ->
                    // hospital route -> hospitals (+ marker icons).
                    const stack = [
                        RASTER_LAYER_ID,
                        'fs-floodvit-fill',
                        'fs-osm-roads',
                        'fs-osm-buildings',
                        'fs-osm-settlements',
                        'fs-osm-settlements-icon',
                        'fs-osm-settlements-label',
                        'fs-fv-affected-roads',
                        'fs-fv-affected-bridges',
                        'fs-fv-affected-bridges-icon',
                        'fs-fv-disconnected-routes',
                        'fs-fv-disconnected-settlements',
                        'fs-fv-disconnected-settlements-icon',
                        'fs-fv-disconnected-settlements-label',
                        'fs-fv-hospital-route-casing',
                        'fs-fv-hospital-route',
                        'fs-fv-hospitals',
                        'fs-fv-hospitals-selected',
                        'fs-fv-hospitals-icon',
                        'fs-fv-hospitals-label',
                    ];
                    for (const id of stack) {
                        if (map.getLayer(id)) map.moveLayer(id);
                    }
                }

                // A popup must not outlive its marker layer (data removed or
                // checkbox switched off while the popup is open).
                const anchor = popupLayerRef.current;
                if (popupRef.current && anchor) {
                    const anchorVisible =
                        !!map.getLayer(anchor) &&
                        map.getLayoutProperty(anchor, 'visibility') !== 'none';
                    if (!anchorVisible) {
                        popupRef.current.remove();
                        popupRef.current = null;
                        popupLayerRef.current = null;
                    }
                }
            } catch (e) {
                console.error('FloodMap: overlay sync failed', e);
            }
        };

        // The load handler is registered once inside the mount effect, so it
        // reaches applyOverlays through a ref instead of a stale closure
        // (and exhaustive-deps stays happy without re-creating the map).
        const overlaysRef = useRef(applyOverlays);
        overlaysRef.current = applyOverlays;

        useEffect(() => {
            if (!containerRef.current || mapInstanceRef.current) return;

            maplibregl.setWorkerUrl('/maplibre-gl-worker.mjs');

            const map = new maplibregl.Map({
                container: containerRef.current,
                style: styleUrl,
                center: INITIAL_CENTER,
                zoom: INITIAL_ZOOM,
                attributionControl: false,
                dragRotate: true,
                touchZoomRotate: true,
            });
            mapInstanceRef.current = map;
            if (typeof window !== 'undefined') {
                (window as unknown as { __fsMap?: maplibregl.Map }).__fsMap = map;
            }

            map.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'top-right');

            // ---- marker interaction (registered once; reads current data
            // through propsRef so no handler ever closes over stale props) ----
            const closePopup = () => {
                popupRef.current?.remove();
                popupRef.current = null;
                popupLayerRef.current = null;
            };

            const markerHit = (point: maplibregl.PointLike) => {
                const layers = MARKER_LAYERS.filter((l) => map.getLayer(l.id)).map(
                    (l) => l.id
                );
                if (layers.length === 0) return null;
                let features: maplibregl.MapGeoJSONFeature[];
                try {
                    features = map.queryRenderedFeatures(point, { layers });
                } catch {
                    return null;
                }
                if (features.length === 0) return null;
                for (const spec of MARKER_LAYERS) {
                    const feature = features.find((f) => f.layer?.id === spec.id);
                    if (feature) return { kind: spec.kind, layerId: spec.id, feature };
                }
                return null;
            };

            const openPopup = (
                hit: NonNullable<ReturnType<typeof markerHit>>,
                lngLat: maplibregl.LngLat
            ) => {
                const { kind, feature, layerId } = hit;
                const collection =
                    kind === 'hospital'
                        ? propsRef.current.floodVit?.hospitals
                        : kind === 'bridge'
                          ? propsRef.current.floodVit?.affectedBridges
                          : kind === 'disconnected'
                            ? propsRef.current.floodVit?.disconnectedSettlements
                            : propsRef.current.osm?.settlements;

                // Plain settlements inherit connectivity status from the
                // disconnected-settlements layer (joined on the shared GeoJSON
                // feature id — no invented per-settlement data).
                let disconnectedById: Map<string, Record<string, unknown>> | null = null;
                if (kind === 'settlement') {
                    const disc = propsRef.current.floodVit?.disconnectedSettlements;
                    if (disc) {
                        disconnectedById = new Map();
                        for (const f of disc.features) {
                            if (f.id != null && f.properties) {
                                disconnectedById.set(String(f.id), f.properties);
                            }
                        }
                    }
                }

                const geometry = feature.geometry;
                const ctx: MarkerPopupContext = {
                    kind,
                    properties: (feature.properties ?? null) as Record<string, unknown> | null,
                    featureId: feature.id ?? null,
                    collection: collection?.properties ?? null,
                    coordinates:
                        geometry && geometry.type === 'Point' ? geometry.coordinates : null,
                    disconnectedById,
                };
                const content = buildMarkerPopup(ctx);
                closePopup();
                if (!content) return;
                popupRef.current = new maplibregl.Popup({
                    maxWidth: '280px',
                    closeButton: true,
                    offset: 12,
                })
                    .setLngLat(lngLat)
                    .setDOMContent(content)
                    .addTo(map);
                popupLayerRef.current = layerId;
            };

            map.on('click', (e) => {
                const hit = markerHit(e.point);
                if (!hit) {
                    closePopup();
                    return;
                }
                openPopup(hit, e.lngLat);
            });

            map.on('mousemove', (e) => {
                map.getCanvas().style.cursor = markerHit(e.point) ? 'pointer' : '';
            });

            map.on('load', () => {
                setLoading(false);
                // A fatal error can land just before the style finishes (slow
                // style + fast failure); once we are up, drop any stale banner.
                setLoadError(null);
                // Marker icons must be registered before the first overlay
                // sync so the symbol layers never race a missing image (a
                // layer asking for an unloaded icon would stay blank).
                ensureMarkerIcons(map).finally(() => {
                    styleReadyRef.current = true;
                    overlaysRef.current();
                });
            });

            map.on('error', (e) => {
                const err = (e as { error?: unknown })?.error ?? e;
                // MapLibre emits `error` for single-tile fetch failures too
                // (e.g. a transient network blip: "AJAXError: Failed to fetch
                // (0)"). Those recover on their own when tiles re-enter the
                // viewport, so only a failure *before* the style loaded means
                // the basemap itself is unusable and worth the banner.
                if (!styleReadyRef.current) {
                    console.error('FloodMap: map error', err);
                    setLoadError(
                        'Base map failed to load — check network access to the style/tile provider.'
                    );
                    setLoading(false);
                } else {
                    console.warn('FloodMap: resource error (will retry on interaction)', err);
                }
            });

            return () => {
                popupRef.current?.remove();
                popupRef.current = null;
                popupLayerRef.current = null;
                map.remove();
                mapInstanceRef.current = null;
                styleReadyRef.current = false;
                rasterUrlRef.current = null;
                rasterBoundsRef.current = null;
                rasterOpacityRef.current = null;
                if (typeof window !== 'undefined') {
                    delete (window as unknown as { __fsMap?: maplibregl.Map }).__fsMap;
                }
            };
        }, [styleUrl]);

        useEffect(() => {
            applyOverlays();
        });

        useEffect(() => {
            const el = containerRef.current;
            if (!el || typeof ResizeObserver === 'undefined') return;
            const observer = new ResizeObserver(() => {
                mapInstanceRef.current?.resize();
            });
            observer.observe(el);
            return () => observer.disconnect();
        }, []);

        useImperativeHandle(ref, () => ({
            zoomIn: () => mapInstanceRef.current?.zoomIn({ duration: 200 }),
            zoomOut: () => mapInstanceRef.current?.zoomOut({ duration: 200 }),
            recenter: () =>
                mapInstanceRef.current?.flyTo({ center: INITIAL_CENTER, zoom: INITIAL_ZOOM, duration: 500 }),
            resize: () => mapInstanceRef.current?.resize(),
            flyTo: (opts: { center?: [number, number]; zoom?: number; duration?: number }) => {
                const m = mapInstanceRef.current;
                if (!m) return;
                m.flyTo({
                    ...(opts.center ? { center: opts.center } : {}),
                    ...(opts.zoom !== undefined ? { zoom: opts.zoom } : {}),
                    duration: opts.duration ?? 500,
                });
            },
            toggleFullscreen: () => {
                const el = containerRef.current?.parentElement ?? containerRef.current;
                if (!el) return;
                if (!document.fullscreenElement) {
                    el.requestFullscreen?.();
                } else {
                    document.exitFullscreen();
                }
            },
        }));

        return (
            <div className={`fs-floodmap ${className ?? ''}`}>
                <div ref={containerRef} className="fs-floodmap__canvas" aria-label="Interactive flood map" />
                {loading && !loadError && (
                    <div className="fs-floodmap__status">
                        <span>Loading map…</span>
                    </div>
                )}
                {loadError && (
                    <div className="fs-floodmap__status fs-floodmap__status--error">
                        <span>{loadError}</span>
                    </div>
                )}
            </div>
        );
    }
);

FloodMap.displayName = 'FloodMap';

export default FloodMap;
