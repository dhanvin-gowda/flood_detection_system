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

const INITIAL_CENTER: [number, number] = [81.62, 29.03];
const INITIAL_ZOOM = 10.2;

type GeoJSONGeometry = { type: string; coordinates: unknown };
type GeoJSONFeature = {
    type: 'Feature';
    properties: Record<string, unknown>;
    geometry: GeoJSONGeometry;
};
export type FeatureCollection = {
    type: 'FeatureCollection';
    features: GeoJSONFeature[];
};

export const SAMPLE_FLOOD_EXTENT: FeatureCollection = {
    type: 'FeatureCollection',
    features: [
        {
            type: 'Feature',
            properties: {
                name: 'Illustrative flood extent',
                areaKm2: 12.4,
                note: 'Sample polygon only — not a verified flood boundary.',
            },
            geometry: {
                type: 'Polygon',
                coordinates: [
                    [
                        [81.55, 29.08],
                        [81.605, 29.1],
                        [81.655, 29.075],
                        [81.67, 29.025],
                        [81.635, 28.985],
                        [81.575, 28.99],
                        [81.535, 29.03],
                        [81.55, 29.08],
                    ],
                ],
            },
        },
    ],
};

export const SAMPLE_ROADS: FeatureCollection = {
    type: 'FeatureCollection',
    features: [
        {
            type: 'Feature',
            properties: { name: 'Sample road segment A', status: 'Affected' },
            geometry: {
                type: 'LineString',
                coordinates: [
                    [81.5, 29.12],
                    [81.58, 29.08],
                    [81.615, 29.04],
                ],
            },
        },
        {
            type: 'Feature',
            properties: { name: 'Sample road segment B', status: 'Affected' },
            geometry: {
                type: 'LineString',
                coordinates: [
                    [81.705, 29.05],
                    [81.65, 29.0],
                    [81.6, 28.95],
                ],
            },
        },
        {
            type: 'Feature',
            properties: { name: 'Sample road segment C', status: 'Affected' },
            geometry: {
                type: 'LineString',
                coordinates: [
                    [81.45, 28.975],
                    [81.52, 28.965],
                    [81.58, 28.94],
                ],
            },
        },
    ],
};

export const SAMPLE_BRIDGES: FeatureCollection = {
    type: 'FeatureCollection',
    features: [
        { name: 'Sample bridge 01', coords: [81.582, 29.083] },
        { name: 'Sample bridge 02', coords: [81.615, 29.04] },
        { name: 'Sample bridge 03', coords: [81.63, 29.0] },
        { name: 'Sample bridge 04', coords: [81.597, 28.965] },
    ].map((b) => ({
        type: 'Feature' as const,
        properties: { name: b.name, status: 'Sample data' },
        geometry: { type: 'Point', coordinates: b.coords },
    })),
};

export const SAMPLE_HOSPITALS: FeatureCollection = {
    type: 'FeatureCollection',
    features: [
        {
            type: 'Feature',
            properties: { name: 'District Hospital (sample)', status: 'Sample data' },
            geometry: { type: 'Point', coordinates: [81.523, 29.07] },
        },
        {
            type: 'Feature',
            properties: { name: 'Raskot Health Post (sample)', status: 'Sample data' },
            geometry: { type: 'Point', coordinates: [81.705, 28.998] },
        },
    ],
};

export const SAMPLE_SETTLEMENTS: FeatureCollection = {
    type: 'FeatureCollection',
    features: [
        { name: 'Chisapani', status: 'Potentially cut off', coords: [81.565, 29.063] },
        { name: 'Birgada', status: 'Connected', coords: [81.47, 29.075] },
        { name: 'Manma', status: 'Connected', coords: [81.615, 29.12] },
        { name: 'Raskot', status: 'Potentially cut off', coords: [81.705, 28.993] },
        { name: 'Tikapur', status: 'Connected', coords: [81.44, 28.92] },
        { name: 'Kalikot', status: 'Potentially cut off', coords: [81.695, 28.9] },
        { name: 'Ramaghat', status: 'Potentially cut off', coords: [81.6, 29.0] },
    ].map((s) => ({
        type: 'Feature' as const,
        properties: { name: s.name, status: s.status },
        geometry: { type: 'Point', coordinates: s.coords },
    })),
};

export type FloodLayerKey = 'flood' | 'roads' | 'bridges' | 'hospitals' | 'settlements';

const LAYER_IDS: Record<FloodLayerKey, string[]> = {
    flood: ['flood-extent-fill', 'flood-extent-outline'],
    roads: ['roads-line'],
    bridges: ['bridges-point'],
    hospitals: ['hospitals-point', 'hospitals-symbol'],
    settlements: ['settlements-point', 'settlements-label'],
};

const INTERACTIVE_LAYER_IDS = ['bridges-point', 'hospitals-point', 'settlements-point'];

function popupHTML(kicker: string, title: string, rows: Array<[string, string]>) {
    const rowsHTML = rows
        .map(
            ([label, value]) =>
                `<div class="fs-maplibre-popup__row"><span>${label}</span><span>${value}</span></div>`
        )
        .join('');
    return `
    <div class="fs-maplibre-popup__kicker">${kicker}</div>
    <div class="fs-maplibre-popup__title">${title}</div>
    <div class="fs-maplibre-popup__rows">${rowsHTML}</div>
    <div class="fs-maplibre-popup__note">Illustrative sample data — not verified</div>
  `;
}

export interface FloodMapProps {

    styleUrl?: string;

    visibleLayers: Record<FloodLayerKey, boolean>;
    className?: string;
}

export interface FloodMapHandle {
    zoomIn: () => void;
    zoomOut: () => void;
    recenter: () => void;
    resize: () => void;
    toggleFullscreen: () => void;
    flyTo: (opts: { center?: [number, number]; zoom?: number; duration?: number }) => void;
}

function applyLayerVisibility(
    map: maplibregl.Map,
    visibleLayers: Record<FloodLayerKey, boolean>
) {
    (Object.keys(LAYER_IDS) as FloodLayerKey[]).forEach((key) => {
        const visible = !!visibleLayers[key];
        LAYER_IDS[key].forEach((layerId) => {
            if (map.getLayer(layerId)) {
                map.setLayoutProperty(layerId, 'visibility', visible ? 'visible' : 'none');
            }
        });
    });
}

function addSampleLayers(map: maplibregl.Map) {
    map.addSource('flood-extent', { type: 'geojson', data: SAMPLE_FLOOD_EXTENT });
    map.addLayer({
        id: 'flood-extent-fill',
        type: 'fill',
        source: 'flood-extent',
        paint: { 'fill-color': '#ef5a5a', 'fill-opacity': 0.28 },
    });
    map.addLayer({
        id: 'flood-extent-outline',
        type: 'line',
        source: 'flood-extent',
        paint: { 'line-color': '#ef5a5a', 'line-width': 2 },
    });

    map.addSource('roads', { type: 'geojson', data: SAMPLE_ROADS });
    map.addLayer({
        id: 'roads-line',
        type: 'line',
        source: 'roads',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#f0a23f', 'line-width': 3 },
    });

    map.addSource('bridges', { type: 'geojson', data: SAMPLE_BRIDGES });
    map.addLayer({
        id: 'bridges-point',
        type: 'circle',
        source: 'bridges',
        paint: {
            'circle-radius': 6,
            'circle-color': '#5b9bf0',
            'circle-stroke-width': 2,
            'circle-stroke-color': '#0a0d12',
        },
    });

    map.addSource('hospitals', { type: 'geojson', data: SAMPLE_HOSPITALS });
    map.addLayer({
        id: 'hospitals-point',
        type: 'circle',
        source: 'hospitals',
        paint: {
            'circle-radius': 7,
            'circle-color': '#4fcf86',
            'circle-stroke-width': 2,
            'circle-stroke-color': '#0a0d12',
        },
    });
    map.addLayer({
        id: 'hospitals-symbol',
        type: 'symbol',
        source: 'hospitals',
        layout: {
            'text-field': '+',
            'text-size': 11,
            'text-font': ['Noto Sans Bold'],
            'text-allow-overlap': true,
        },
        paint: { 'text-color': '#06130d' },
    });

    map.addSource('settlements', { type: 'geojson', data: SAMPLE_SETTLEMENTS });
    map.addLayer({
        id: 'settlements-point',
        type: 'circle',
        source: 'settlements',
        paint: {
            'circle-radius': 6.5,
            'circle-color': [
                'match',
                ['get', 'status'],
                'Potentially cut off',
                '#b18af2',
                '#7fe8b8',
            ],
            'circle-stroke-width': 2,
            'circle-stroke-color': '#0a0d12',
        },
    });
    map.addLayer({
        id: 'settlements-label',
        type: 'symbol',
        source: 'settlements',
        layout: {
            'text-field': ['get', 'name'],
            'text-size': 10,
            'text-offset': [0, 1.3],
            'text-anchor': 'top',
            'text-font': ['Noto Sans Regular'],
        },
        paint: {
            'text-color': '#8c96a3',
            'text-halo-color': '#0a0d12',
            'text-halo-width': 1,
        },
    });
}

function wireInteractivity(map: maplibregl.Map) {
    INTERACTIVE_LAYER_IDS.forEach((layerId) => {
        map.on('mouseenter', layerId, () => {
            map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', layerId, () => {
            map.getCanvas().style.cursor = '';
        });
    });

    map.on('click', 'bridges-point', (e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        const f = e.features?.[0];
        if (!f) return;
        const coords = (f.geometry as { type: string; coordinates: [number, number] }).coordinates.slice() as [number, number];
        new maplibregl.Popup({ className: 'fs-maplibre-popup' })
            .setLngLat(coords)
            .setHTML(popupHTML('BRIDGE', String(f.properties?.name ?? 'Sample bridge'), [
                ['Status', String(f.properties?.status ?? 'Sample data')],
            ]))
            .addTo(map);
    });

    map.on('click', 'hospitals-point', (e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        const f = e.features?.[0];
        if (!f) return;
        const coords = (f.geometry as { type: string; coordinates: [number, number] }).coordinates.slice() as [number, number];
        new maplibregl.Popup({ className: 'fs-maplibre-popup' })
            .setLngLat(coords)
            .setHTML(popupHTML('HOSPITAL', String(f.properties?.name ?? 'Sample hospital'), [
                ['Status', String(f.properties?.status ?? 'Sample data')],
            ]))
            .addTo(map);
    });

    map.on('click', 'settlements-point', (e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        const f = e.features?.[0];
        if (!f) return;
        const coords = (f.geometry as { type: string; coordinates: [number, number] }).coordinates.slice() as [number, number];
        new maplibregl.Popup({ className: 'fs-maplibre-popup' })
            .setLngLat(coords)
            .setHTML(
                popupHTML('SETTLEMENT', String(f.properties?.name ?? 'Sample settlement'), [
                    ['Status', String(f.properties?.status ?? 'Unknown')],
                ])
            )
            .addTo(map);
    });
}

export const FloodMap = forwardRef<FloodMapHandle, FloodMapProps>(
    ({ styleUrl = DEFAULT_STYLE_URL, visibleLayers, className }, ref) => {
        const containerRef = useRef<HTMLDivElement | null>(null);
        const mapInstanceRef = useRef<maplibregl.Map | null>(null);
        const visibleLayersRef = useRef(visibleLayers);
        const [loading, setLoading] = useState(true);
        const [loadError, setLoadError] = useState<string | null>(null);

        useEffect(() => {
            visibleLayersRef.current = visibleLayers;
            const map = mapInstanceRef.current;
            if (map && map.isStyleLoaded()) {
                applyLayerVisibility(map, visibleLayers);
            }
        }, [visibleLayers]);

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

            map.on('load', () => {
                try {
                    addSampleLayers(map);
                    wireInteractivity(map);
                    applyLayerVisibility(map, visibleLayersRef.current);
                    setLoading(false);
                } catch (err) {
                    console.error('FloodMap: failed to add sample layers', err);
                    setLoadError('Map layers failed to initialize.');
                    setLoading(false);
                }
            });

            map.on('error', (e) => {
                console.error('FloodMap: map error', e?.error ?? e);
                setLoadError(
                    'Base map failed to load — check network access to the style/tile provider.'
                );
                setLoading(false);
            });

            return () => {
                map.remove();
                mapInstanceRef.current = null;
                if (typeof window !== 'undefined') {
                    delete (window as unknown as { __fsMap?: maplibregl.Map }).__fsMap;
                }
            };
        }, [styleUrl]);

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
                    document.exitFullscreen?.();
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