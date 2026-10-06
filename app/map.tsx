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

const INITIAL_CENTER: [number, number] = [0, 20];
const INITIAL_ZOOM = 1.2;

export interface FloodMapProps {

    styleUrl?: string;

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

export const FloodMap = forwardRef<FloodMapHandle, FloodMapProps>(
    ({ styleUrl = DEFAULT_STYLE_URL, className }, ref) => {
        const containerRef = useRef<HTMLDivElement | null>(null);
        const mapInstanceRef = useRef<maplibregl.Map | null>(null);
        const [loading, setLoading] = useState(true);
        const [loadError, setLoadError] = useState<string | null>(null);

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
                setLoading(false);
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
