// Popup content builders for the four map marker types (hospitals,
// settlements, bridges, disconnected settlements).
//
// Every row is rendered from a field the backend actually returned — rows
// whose field is missing are simply omitted, and no wording is ever upgraded
// beyond what the data says ("potentially affected" / "potentially
// disconnected" stay as-is; connectivity results are never called damage).
// DOM is built with createElement/textContent, so no HTML escaping is needed.
export type MarkerPopupKind = 'hospital' | 'settlement' | 'bridge' | 'disconnected';

export interface MarkerPopupContext {
    kind: MarkerPopupKind;
    /** Feature-level GeoJSON properties from the fetched layer. */
    properties: Record<string, unknown> | null;
    /** GeoJSON feature id (e.g. "way/135241927") when the layer carries one. */
    featureId?: string | number | null;
    /** FeatureCollection-level `properties` of the same response (method note, …). */
    collection?: Record<string, unknown> | null;
    /** Actual GeoJSON [lon, lat] of the marker (hospital "location" row). */
    coordinates?: [number, number] | null;
    /** disconnected_settlements features keyed by settlement feature id — used
     *  to add connectivity status to plain OSM settlement popups. */
    disconnectedById?: Map<string, Record<string, unknown>> | null;
}

const asStr = (value: unknown): string | null =>
    typeof value === 'string' && value.trim() !== '' ? value : null;

const asNum = (value: unknown): number | null =>
    typeof value === 'number' && Number.isFinite(value) ? value : null;

const asBool = (value: unknown): boolean | null =>
    typeof value === 'boolean' ? value : null;

/** Metres → compact human units, matching the metric fields the backend emits. */
const fmtM = (metres: number): string =>
    metres >= 1000 ? `${(metres / 1000).toFixed(2)} km` : `${Math.round(metres * 10) / 10} m`;

const capitalize = (text: string): string =>
    text.length > 0 ? text[0].toUpperCase() + text.slice(1) : text;

const makeRow = (label: string, value: string): HTMLDivElement => {
    const row = document.createElement('div');
    row.className = 'fs-popup__row';
    const key = document.createElement('span');
    key.className = 'fs-popup__key';
    key.textContent = label;
    const val = document.createElement('span');
    val.className = 'fs-popup__val';
    val.textContent = value;
    row.append(key, val);
    return row;
};

const makeNote = (text: string): HTMLDivElement => {
    const note = document.createElement('div');
    note.className = 'fs-popup__note';
    note.textContent = text;
    return note;
};

/** Connectivity rows shared by the settlement popups; null when the join misses. */
const connectivityRows = (
    props: Record<string, unknown>
): HTMLDivElement[] => {
    const rows: HTMLDivElement[] = [];
    const status = asBool(props['newlyDisconnected']);
    if (status !== null) {
        rows.push(
            makeRow(
                'STATUS',
                status ? 'Potentially disconnected' : 'Disconnected (pre-existing)'
            )
        );
    }
    const roads = asNum(props['componentRoadCount']);
    const length = asNum(props['componentLengthM']);
    if (roads !== null && length !== null) {
        rows.push(
            makeRow(
                'ROAD CONNECTION',
                roads > 0 ? `${roads} roads · ${fmtM(length)} surviving` : 'No available mapped route'
            )
        );
    }
    return rows;
};

export function buildMarkerPopup(ctx: MarkerPopupContext): HTMLElement | null {
    const props = ctx.properties ?? {};
    const card = document.createElement('div');
    card.className = 'fs-popup';

    const addTitle = (fallback: string): string => {
        const title = document.createElement('div');
        title.className = 'fs-popup__title';
        const name = asStr(props['name']) ?? asStr(props['ref']) ?? fallback;
        title.textContent = name;
        card.append(title);
        return name;
    };

    if (ctx.kind === 'hospital') {
        addTitle('Hospital');

        if (asBool(props['selected']) === true) {
            const chip = document.createElement('div');
            chip.className = 'fs-popup__chip';
            chip.textContent = 'Nearest reachable hospital';
            card.append(chip);
        }

        const reachable = asBool(props['reachable']);
        if (reachable !== null) {
            card.append(
                makeRow('ACCESS', reachable ? 'Passable route available' : 'No passable route')
            );
        }
        const routeLength = asNum(props['routeLengthM']);
        if (reachable === true && routeLength !== null) {
            card.append(makeRow('ROUTE', fmtM(routeLength)));
        }
        const floodDistance = asNum(props['distanceToFloodM']);
        if (floodDistance !== null) {
            card.append(makeRow('DISTANCE TO FLOOD', fmtM(floodDistance)));
        }
        const operator = asStr(props['operator']);
        if (operator) {
            card.append(makeRow('OPERATOR', operator));
        }
        const coords = ctx.coordinates;
        if (coords && coords.length === 2) {
            card.append(makeRow('LOCATION', `${coords[1].toFixed(5)}, ${coords[0].toFixed(5)}`));
        }
    } else if (ctx.kind === 'settlement') {
        addTitle('Settlement');

        const place = asStr(props['place']);
        if (place) card.append(makeRow('TYPE', capitalize(place)));
        const population = asStr(props['population']);
        if (population) card.append(makeRow('POPULATION', population));

        const joined = ctx.featureId != null
            ? ctx.disconnectedById?.get(String(ctx.featureId)) ?? null
            : null;
        if (joined) {
            for (const row of connectivityRows(joined)) card.append(row);
        }
    } else if (ctx.kind === 'bridge') {
        const name = addTitle('Bridge');
        const named = asStr(props['name']);
        if (!named && ctx.featureId != null) {
            card.append(makeRow('OSM ID', String(ctx.featureId)));
        }
        const status = asStr(props['status']);
        if (status) card.append(makeRow('STATUS', capitalize(status)));

        const highway = asStr(props['highway']);
        const ref = asStr(props['ref']);
        const road = [highway, ref].filter(Boolean).join(' · ');
        if (road && road !== name) card.append(makeRow('ROAD', road));

        const affected = asNum(props['affectedLengthM']);
        const total = asNum(props['totalLengthM']);
        if (affected !== null && total !== null) {
            const fraction = asNum(props['affectedFraction']);
            const pct = fraction !== null ? ` (${Math.round(fraction * 100)}%)` : '';
            card.append(makeRow('AFFECTED', `${fmtM(affected)} of ${fmtM(total)}${pct}`));
        }
    } else {
        // disconnected settlement
        addTitle('Settlement');
        for (const row of connectivityRows(props)) card.append(row);

        const reason = asStr(ctx.collection?.['method']);
        if (reason) card.append(makeNote(reason));
    }

    return card.childElementCount > 0 ? card : null;
}
