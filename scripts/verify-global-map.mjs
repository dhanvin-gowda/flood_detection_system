// Verification probe. Renders the dashboard in headless Chrome, then exercises
// the map: initial view, world-view button, HUD readout, flood-layer default,
// crossfade, and a real geocoding search. Run with:
//   node scripts/verify-global-map.mjs
import puppeteer from 'puppeteer-core';

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const URL = process.env.VERIFY_URL || 'http://localhost:3000';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
function check(name, pass, detail = '') {
    results.push({ name, pass, detail });
    console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`);
}

const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--use-gl=swiftshader', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 900 });

const consoleErrors = [];
const pageErrors = [];
const failedRequests = [];
page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text());
});
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('requestfailed', (r) => failedRequests.push(`${r.url()} :: ${r.failure()?.errorText}`));
page.on('response', (r) => {
    if (r.status() >= 400) failedRequests.push(`${r.status()} ${r.url()}`);
});

await page.goto(URL, { waitUntil: 'networkidle2', timeout: 60000 });
await page.waitForSelector('.fs-floodmap__canvas canvas', { timeout: 30000 });
// Wait for the sample layers to exist, not just for the canvas. The map mounts
// on 'load', and under StrictMode it is created, torn down and recreated, so
// probing too early can catch a map that is about to be replaced.
await page.waitForFunction(
    () => {
        const m = window.__fsMap;
        return !!m && !!m.getLayer('flood-extent-fill') && m.isStyleLoaded();
    },
    { timeout: 30000 }
);
await sleep(2500);

const readHud = () =>
    page.evaluate(() => {
        const spans = [...document.querySelectorAll('.fs-map-bottombar__left span')];
        return {
            zoom: spans.find((s) => /^ZOOM /.test(s.textContent || ''))?.textContent?.trim() ?? null,
            scale: document.querySelector('.fs-scale-bar')?.textContent?.trim() ?? null,
        };
    });

// 1. Map canvas actually painted (WebGL worker alive, style applied).
const painted = await page.evaluate(() => {
    const c = document.querySelector('.fs-floodmap__canvas canvas');
    if (!c) return { ok: false, reason: 'no canvas' };
    const gl = c.getContext('webgl2') || c.getContext('webgl');
    return { ok: !!gl, w: c.width, h: c.height };
});
check('map canvas has a live WebGL context', painted.ok, `${painted.w}x${painted.h}`);

// 2. No load-error overlay.
const overlay = await page.evaluate(() => !!document.querySelector('.fs-floodmap__status--error'));
check('no map load-error overlay', !overlay);

// 3. Default view is regional (z ~6), not the old z10.2 local window.
const hud0 = await readHud();
const z0 = parseFloat((hud0.zoom || '').replace(/[^0-9.]/g, ''));
check('initial zoom is regional (~6), not 10.2', z0 > 5 && z0 < 7, `zoom=${z0}  hud="${hud0.zoom}"`);

// 4. HUD is live: driving the map must change the readout promptly. The
//    tolerance is short on purpose — a readout that only refreshes once tiles
//    settle reads as stale to the user, even though it eventually catches up.
const before = await readHud();
await page.click('button[aria-label="Zoom in"]');
await sleep(400);
const after = await readHud();
check('HUD zoom updates on map movement', before.zoom !== after.zoom, `"${before.zoom}" -> "${after.zoom}"`);

const scaleChanged = before.scale !== after.scale;
check('HUD scale label updates too', scaleChanged, `"${before.scale}" -> "${after.scale}"`);

// 5. Flood layer hidden by default, checkbox still available and toggleable.
const floodDefault = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.fs-layer-row')];
    const row = rows.find((r) => /Flood extent/.test(r.textContent || ''));
    return { found: !!row, checked: row?.querySelector('input')?.checked ?? null };
});
check('flood extent layer hidden by default', floodDefault.found && floodDefault.checked === false, `checked=${floodDefault.checked}`);
check('flood extent checkbox still present', floodDefault.found);

// 6. World view flies out to a global framing, and is independent of recenter.
//    Asserted against the map's own camera, not the HUD, so this measures the
//    button's effect directly.
await page.click('button[aria-label="World view"]');
await sleep(2000);
const world = await page.evaluate(() => {
    const c = window.__fsMap.getCenter();
    return { lng: c.lng, lat: c.lat, zoom: window.__fsMap.getZoom() };
});
check(
    'world-view button flies to global zoom (~1.2)',
    world.zoom > 0.5 && world.zoom < 2,
    `zoom=${world.zoom.toFixed(3)}`
);
// Latitude 20 rather than 0, so the globe is not split at the equator.
check(
    'world-view is centred on 20N (not the equator)',
    Math.abs(world.lat - 20) < 1 && Math.abs(world.lng) < 1,
    `centre=${world.lng.toFixed(2)},${world.lat.toFixed(2)}`
);

await page.click('button[aria-label="Recenter on area of interest"]');
await sleep(2000);
const recent = await page.evaluate(() => {
    const c = window.__fsMap.getCenter();
    return { lng: c.lng, lat: c.lat, zoom: window.__fsMap.getZoom() };
});
check(
    'recenter returns to the AOI (independent of world view)',
    recent.zoom > 4 && Math.abs(recent.lng - 81.62) < 0.5,
    `zoom=${recent.zoom.toFixed(2)} centre=${recent.lng.toFixed(2)},${recent.lat.toFixed(2)}`
);

// 7. Crossfade: the basemap must be ONE style whose paint properties are
//    zoom-interpolated, not two styles swapped at a threshold. Verified by
//    reading the expressions off the live style.
const crossfade = await page.evaluate(() => {
    const m = window.__fsMap;
    const describe = (layer, prop) => {
        const v = m.getPaintProperty(layer, prop);
        if (!Array.isArray(v)) return String(v);
        const zi = v.indexOf('zoom');
        return zi >= 0 ? v.slice(zi, zi + 5).join('/') : v.join('/');
    };
    return {
        sources: Object.keys(m.getStyle().sources),
        hasGlyphs: !!m.getStyle().glyphs,
        satellite: describe('satellite-raster', 'raster-opacity'),
        water: describe('water', 'fill-opacity'),
        bgColor: String(m.getPaintProperty('bg', 'background-color')),
        placeColor: String(m.getPaintProperty('place', 'text-color')),
        waterLayers: m
            .getStyle()
            .layers.filter((l) => l['source-layer'] === 'water')
            .length,
    };
});
check(
    'one style, no setStyle swap (vector + raster sources coexist)',
    crossfade.sources.includes('maptiler') && crossfade.sources.includes('satellite'),
    `sources=${crossfade.sources.join(',')}`
);
check('glyph endpoint present for place labels', crossfade.hasGlyphs);
check(
    'satellite raster-opacity is a z4->z8 interpolation',
    /4\/0\/8\/1/.test(crossfade.satellite),
    crossfade.satellite
);
check(
    'ocean fill-opacity is the inverse z4->z8 interpolation',
    /4\/1\/8\/0/.test(crossfade.water),
    crossfade.water
);
// Land is the background, because the v3 schema has no land layer. If the
// background is ever painted the ocean colour again, land and sea become the
// same flat fill and only the sample points remain.
check(
    'background is the land colour (not the ocean colour)',
    crossfade.bgColor === '#3a3f45',
    `background-color=${crossfade.bgColor}`
);
check('place labels are NOT zoom-faded (constant colour)', crossfade.placeColor === '#c7ccd1', crossfade.placeColor);
// The water fill is required to distinguish sea from land; the inverse fade
// above is what stops it hiding the imagery. Asserting "no water layer" is the
// assertion that produced the blank-basemap regression.
check('exactly one vector water layer, above the raster', crossfade.waterLayers === 1, `${crossfade.waterLayers} water layers`);

// 7b. Rendered pixels, not style structure. Every assertion above reads the
//     style object, so it cannot tell "land is painted" apart from "nothing is
//     painted but the background" — which is exactly how a style with a
//     landcover fill and no land layer passed 26/26 while showing no geography.
//     These sample the composited frame and require real land and real ocean to
//     be on screen at the world framing.
// Resolves once the map has actually finished drawing the current view, rather
// than sleeping a guessed interval. Low-zoom vector tiles are large and slow, and
// reading a half-drawn frame reports land coverage that is really just unloaded
// background. Bound the wait so a stalled tile cannot hang the whole script, and
// report whether it settled so a measurement taken mid-load is visible in output.
const waitIdle = (budgetMs = 45000) =>
    page.evaluate(
        (budget) =>
            new Promise((resolve) => {
                const m = window.__fsMap;
                const settle = () => {
                    clearTimeout(timer);
                    resolve(true);
                };
                const timer = setTimeout(() => resolve(false), budget);
                if (m.loaded() && m.areTilesLoaded()) {
                    // Already settled. Resolving on a later tick still gives
                    // MapLibre a frame to draw into before the screenshot.
                    requestAnimationFrame(() => requestAnimationFrame(settle));
                    return;
                }
                m.once('idle', settle);
            }),
        budgetMs
    );

const worldFrame = async () => {
    await page.evaluate(() => window.__fsMap.jumpTo({ center: [0, 20], zoom: 1.2 }));
    const settled = await waitIdle();
    await sleep(600);
    return settled;
};

// Retries the framing rather than measuring a frame that never finished loading.
// Under-reporting land here would show up as a false failure, which is worse than
// a slow script.
let worldSettled = false;
for (let attempt = 0; attempt < 3 && !worldSettled; attempt++) {
    worldSettled = await worldFrame();
}
if (!worldSettled) {
    console.warn('WARN  map never reported idle at the world framing; pixel coverage may be understated');
}

// Counts how much of the canvas each given RGB triple covers. Sampled from a
// screenshot, not gl.readPixels: a WebGL drawing buffer is cleared once the
// frame is composited, so readPixels after the fact returns transparent black
// regardless of what was drawn. Same technique as verify-fallback.mjs.
const canvasShare = async (colours) => {
    const shot = await (await page.$('.fs-floodmap__canvas canvas')).screenshot({
        encoding: 'base64',
    });
    return page.evaluate(
        async (b64, list) => {
            const img = new Image();
            img.src = `data:image/png;base64,${b64}`;
            await img.decode();
            const c = document.createElement('canvas');
            c.width = img.width;
            c.height = img.height;
            const ctx = c.getContext('2d');
            ctx.drawImage(img, 0, 0);
            const { data } = ctx.getImageData(0, 0, c.width, c.height);
            const counts = {};
            let total = 0;
            for (let i = 0; i < data.length; i += 4) {
                total++;
                for (const [name, rgb] of list) {
                    // +/-2 tolerance: the canvas is GPU-composited, so a flat fill
                    // can land a shade or two off the authored value.
                    if (
                        Math.abs(data[i] - rgb[0]) <= 2 &&
                        Math.abs(data[i + 1] - rgb[1]) <= 2 &&
                        Math.abs(data[i + 2] - rgb[2]) <= 2
                    ) {
                        counts[name] = (counts[name] || 0) + 1;
                        break;
                    }
                }
            }
            const out = {};
            for (const [name] of list) out[name] = (counts[name] || 0) / total;
            return out;
        },
        shot,
        colours
    );
};

// `landcover` is what this style used to fill land with, and it is the layer a
// future reader is most likely to reach for again when land looks missing. Paint
// it alone in a marker colour and measure the share: landcover is forest /
// scrub / sand patches, so if it covers a fraction of the frame it cannot be
// standing in for a continent, which is the whole reason land is the background
// and `water` is the fill.
await page.evaluate(() =>
    window.__fsMap.addLayer({
        id: '__probe-landcover',
        type: 'fill',
        source: 'maptiler',
        'source-layer': 'landcover',
        paint: { 'fill-color': '#ff00ff', 'fill-opacity': 1 },
    })
);
await waitIdle();
await sleep(600);
const landcoverShare = (await canvasShare([['cover', [255, 0, 255]]])).cover;
await page.evaluate(() => window.__fsMap.removeLayer('__probe-landcover'));
check(
    'landcover is patch data, not a land layer (too small to fill continents)',
    landcoverShare < 0.15,
    `landcover covers ${(landcoverShare * 100).toFixed(1)}% of the world frame`
);

// The regression this suite missed: land and ocean both fell through to one flat
// fill, so the world view showed nothing but markers and lines.
const coverage = await canvasShare([
    ['land', [58, 63, 69]], // BASEMAP.land
    ['ocean', [43, 54, 64]], // BASEMAP.ocean
]);
const covDetail = `land=${(coverage.land * 100).toFixed(1)}% ocean=${(coverage.ocean * 100).toFixed(1)}%`;
check(
    'land is actually painted at world view (not just background + markers)',
    coverage.land > 0.15,
    covDetail
);
check('ocean is distinguishable from land at world view', coverage.ocean > 0.3, covDetail);

// Back to the AOI before the search checks, which assert the camera travels a
// long way when a result is picked.
await page.evaluate(() => window.__fsMap.jumpTo({ center: [81.62, 29.03], zoom: 6 }));
await waitIdle();

// 8. Real geocoding search: type Paris, confirm results, pick one, confirm the
//    map moves and the flood layer stays off.
await page.click('.fs-search__box input');
await page.type('.fs-search__box input', 'Paris', { delay: 40 });
await page.waitForSelector('.fs-search__results button', { timeout: 15000 });
const parisResults = await page.evaluate(() =>
    [...document.querySelectorAll('.fs-search__results button')].map((b) => b.textContent?.trim()).filter(Boolean)
);
check('search returns real results for "Paris"', parisResults.length > 0, `${parisResults.length} results; first="${parisResults[0]}"`);

if (parisResults.length) {
    const centrePre = await page.evaluate(() => {
        const c = window.__fsMap.getCenter();
        return [c.lng, c.lat];
    });
    await page.click('.fs-search__results button');
    await sleep(2200);
    const afterPick = await page.evaluate(() => {
        const m = window.__fsMap;
        const c = m.getCenter();
        return {
            centre: [c.lng, c.lat],
            zoom: m.getZoom(),
            floodVis: m.getLayoutProperty('flood-extent-fill', 'visibility'),
            checkbox: [...document.querySelectorAll('.fs-layer-row')]
                .find((r) => /Flood extent/.test(r.textContent || ''))
                ?.querySelector('input')?.checked,
        };
    });
    // Paris is ~2.33E 48.86N; the AOI is 81.6E 29.0N. A real move is a large delta.
    const moved = Math.abs(afterPick.centre[0] - centrePre[0]) > 5;
    check('picking a search result moves the map to that place', moved, `centre ${centrePre.map((n) => n.toFixed(1))} -> ${afterPick.centre.map((n) => n.toFixed(1))}`);
    check('search does NOT enable the flood polygon (map layer)', afterPick.floodVis === 'none', `visibility=${afterPick.floodVis}`);
    check('search does NOT enable the flood polygon (checkbox)', afterPick.checkbox === false, `checked=${afterPick.checkbox}`);
}

// 9. Kathmandu, to confirm non-Latin results come back.
await page.click('.fs-search__box input');
await page.type('.fs-search__box input', 'Kathmandu', { delay: 40 });
await page.waitForSelector('.fs-search__results button', { timeout: 15000 });
const kathResults = await page.evaluate(() =>
    [...document.querySelectorAll('.fs-search__results button')].map((b) => b.textContent?.trim()).filter(Boolean)
);
check('search returns results for "Kathmandu"', kathResults.length > 0, `first="${kathResults[0]}"`);

// 10. Keyboard: Enter takes the top result, Escape closes the list.
await page.click('.fs-search__box input');
await page.type('.fs-search__box input', 'Paris', { delay: 40 });
await page.waitForSelector('.fs-search__results button', { timeout: 15000 });
await page.keyboard.press('Escape');
await sleep(300);
const closedOnEsc = await page.evaluate(() => !document.querySelector('.fs-search__results'));
check('Escape closes the result list', closedOnEsc);

// 11. Flood polygon can still be enabled by hand (layer kept, not deleted).
//     Polls for the effect rather than sleeping a fixed 600ms: the toggle lands
//     via a React state change, and while the map is still decoding tiles the
//     commit can be pushed well past that, which reads as a broken layer when it
//     is only a slow frame.
const floodAfterToggle = await page.evaluate(async () => {
    const row = [...document.querySelectorAll('.fs-layer-row')].find((r) =>
        /Flood extent/.test(r.textContent || '')
    );
    const input = row.querySelector('input');
    input.click();
    const deadline = Date.now() + 10000;
    let visibility = null;
    while (Date.now() < deadline) {
        visibility = window.__fsMap.getLayoutProperty('flood-extent-fill', 'visibility');
        if (visibility === 'visible') break;
        await new Promise((r) => setTimeout(r, 100));
    }
    return { checked: input.checked, visibility };
});
check(
    'flood polygon can be re-enabled via its checkbox',
    floodAfterToggle.checked === true && floodAfterToggle.visibility === 'visible',
    `checked=${floodAfterToggle.checked} visibility=${floodAfterToggle.visibility}`
);

// 12. No uncaught errors, and no failed network requests we care about.
check('no uncaught page errors', pageErrors.length === 0, pageErrors.slice(0, 2).join(' | '));
// ERR_ABORTED on tile requests is MapLibre cancelling in-flight fetches whose
// tiles the camera moved away from. It is normal during any pan/zoom/flight and
// says nothing about tile availability, so it is not counted as a failure.
// Genuine tile problems surface as HTTP status codes, which are still checked.
const realHttpFailures = failedRequests.filter(
    (r) => !/favicon/i.test(r) && !/net::ERR_ABORTED/.test(r)
);
check('no failed HTTP requests', realHttpFailures.length === 0, realHttpFailures.slice(0, 3).join(' | '));
// Console "Failed to load resource" lines are the HTTP failures, already asserted.
// WebGL stall notices are browser perf chatter under swiftshader, not defects.
const realConsoleErrors = consoleErrors.filter(
    (t) => !/Failed to load resource|GPU stall due to ReadPixels/i.test(t)
);
check('no other console errors', realConsoleErrors.length === 0, realConsoleErrors.slice(0, 3).join(' | '));

await browser.close();

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length) {
    console.log('FAILURES:');
    failed.forEach((f) => console.log(`  - ${f.name} ${f.detail}`));
    process.exit(1);
}
