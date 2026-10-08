// Map marker icons for the four point-marker types (hospitals, settlements,
// bridges, disconnected settlements). Each icon is a small inline SVG encoded
// as a data URI and rasterised to a canvas, then registered once with
// map.addImage so the symbol layers can reference them by id — no emoji, no
// external sprite sheet.
//
// The icon is drawn onto a <canvas> because map.loadImage()/createImageBitmap
// cannot decode SVG sources in Chromium ("source image could not be decoded");
// an HTMLCanvasElement is a natively supported addImage input, so every icon
// registers synchronously from the vector definition.
//
// Colour language matches the existing map palette:
//   hospital    #38bdf8 (fs-fv-hospitals sky circle)
//   settlement  #e7ecf1 / white (fs-osm-settlements white circle)
//   bridge      #f472b6 (fs-fv-affected-bridges pink line)
//   disconnected #ef4444 (fs-fv-disconnected-* red)
// Every glyph carries a dark #0f172a outline so it stays legible over the
// satellite basemap and the cyan flood fill.
import * as maplibregl from 'maplibre-gl';

export const MARKER_ICON_IDS = [
    'fs-icon-hospital',
    'fs-icon-settlement',
    'fs-icon-bridge',
    'fs-icon-disconnected',
] as const;

export type MarkerIconId = (typeof MARKER_ICON_IDS)[number];

const BADGE = (
    body: string
) => `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">${body}</svg>`;

const ICON_SVGS: Record<MarkerIconId, string> = {
    // Rounded badge with a medical cross — reads as "medical facility".
    'fs-icon-hospital': BADGE(
        '<rect x="3.5" y="3.5" width="25" height="25" rx="7" fill="#38bdf8" stroke="#0f172a" stroke-width="1.6"/>' +
            '<path d="M16 9v14M9 16h14" stroke="#ffffff" stroke-width="4.2" stroke-linecap="round"/>'
    ),
    // Location pin — a small "place" marker matching the white settlements layer.
    'fs-icon-settlement': BADGE(
        '<path d="M16 3.5c-5.1 0-9.2 4.1-9.2 9.2 0 6.7 9.2 15.8 9.2 15.8s9.2-9.1 9.2-15.8c0-5.1-4.1-9.2-9.2-9.2z" ' +
            'fill="#e7ecf1" stroke="#0f172a" stroke-width="1.6" stroke-linejoin="round"/>' +
            '<circle cx="16" cy="12.6" r="3.8" fill="#0f172a"/>'
    ),
    // Arch bridge (deck + columns + arch) on the pink affected-bridges badge.
    'fs-icon-bridge': BADGE(
        '<rect x="3.5" y="3.5" width="25" height="25" rx="7" fill="#f472b6" stroke="#0f172a" stroke-width="1.6"/>' +
            '<g stroke="#ffffff" stroke-width="2.4" fill="none" stroke-linecap="round">' +
            '<path d="M6.5 17.5h19"/>' +
            '<path d="M7.5 17.5v8M24.5 17.5v8"/>' +
            '<path d="M7.5 25q8.5-9 17 0"/>' +
            '<path d="M12 17.5v3.1M16 17.5v2.1M20 17.5v3.1"/>' +
            '</g>'
    ),
    // Warning triangle — the distinct "potentially disconnected" marker.
    'fs-icon-disconnected': BADGE(
        '<path d="M14.1 5.6a2.4 2.4 0 0 1 3.8 0l11 17.2a2.4 2.4 0 0 1-2 3.6H5.1a2.4 2.4 0 0 1-2-3.6z" ' +
            'fill="#ef4444" stroke="#0f172a" stroke-width="1.6" stroke-linejoin="round"/>' +
            '<path d="M16 12.2v6.4" stroke="#ffffff" stroke-width="3" stroke-linecap="round"/>' +
            '<circle cx="16" cy="23.2" r="1.8" fill="#ffffff"/>'
    ),
};

export const markerIconDataUri = (id: MarkerIconId): string =>
    `data:image/svg+xml;charset=utf-8,${encodeURIComponent(ICON_SVGS[id])}`;

/**
 * Rasterise one SVG icon to ImageData. Rasterizing in the browser keeps the
 * vector definitions ('#38bdf8' + '<rect>') in one place while giving MapLibre
 * an image it can upload without createImageBitmap (which cannot decode SVG in
 * Chromium) and without an HTMLCanvasElement (which maplibre-gl 6.11 does not
 * accept in addImage — the width/height/data branch reads `e.data`, and a
 * canvas has none). ImageData carries those three fields directly.
 */
const renderMarkerIcon = (id: MarkerIconId): Promise<HTMLCanvasElement> =>
    new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => {
            try {
                const canvas = document.createElement('canvas');
                canvas.width = 32;
                canvas.height = 32;
                const ctx = canvas.getContext('2d');
                if (!ctx) throw new Error('2d canvas context unavailable');
                ctx.drawImage(img, 0, 0, 32, 32);
                resolve(canvas);
            } catch (e) {
                reject(e);
            }
        };
        img.onerror = () => reject(new Error(`failed to decode marker icon ${id}`));
        img.src = markerIconDataUri(id);
    });

/**
 * Register every marker icon with the map (idempotent, safe to await before
 * the first overlay sync). A failed render only means the matching symbol
 * layer draws nothing — the circle layers underneath stay as fallback dots.
 */
export async function ensureMarkerIcons(map: maplibregl.Map): Promise<void> {
    for (const id of MARKER_ICON_IDS) {
        if (map.hasImage(id)) continue;
        try {
            const image = await renderMarkerIcon(id);
            if (!map.hasImage(id)) {
                map.addImage(id, image);
            }
        } catch (e) {
            console.warn(`FloodMap: marker icon ${id} unavailable`, e);
        }
    }
}
