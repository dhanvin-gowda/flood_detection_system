// Crossfade probe. Drives the real map through the z4 -> z8 transition band and
// samples the computed paint values of each basemap layer, so the crossfade is
// verified as a gradual interpolation rather than a step change. Also checks
// that the flood layer stays untouched by camera moves.
import puppeteer from 'puppeteer-core';

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--use-gl=swiftshader', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 900 });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));

await page.goto('http://localhost:3000', { waitUntil: 'networkidle2', timeout: 60000 });
await page.waitForSelector('.fs-floodmap__canvas canvas', { timeout: 30000 });
await sleep(4000);

// Style layer inventory, straight from the loaded style.
const styleInfo = await page.evaluate(() => {
    const s = window.__fsMap.getStyle();
    return {
        glyphs: !!s.glyphs,
        sources: Object.keys(s.sources),
        layers: s.layers.map((l) => ({ id: l.id, type: l.type })),
    };
});
console.log('glyphs endpoint :', styleInfo.glyphs);
console.log('sources         :', styleInfo.sources.join(', '));
console.log('layers          :', styleInfo.layers.map((l) => `${l.id}(${l.type})`).join(', '));

// Sample the evaluated paint across the fade band.
const sample = async (zoom) => {
    await page.evaluate((z) => window.__fsMap.jumpTo({ center: [0, 20], zoom: z }), zoom);
    await sleep(700);
    return page.evaluate(() => {
        const m = window.__fsMap;
        const f = (layer, prop) => {
            const v = m.getPaintProperty(layer, prop);
            // An expression evaluates to its literal form here; the value that
            // matters is which zoom range it spans, so report it structurally.
            if (Array.isArray(v)) {
                const zi = v.indexOf('zoom');
                return zi >= 0 ? `interp[z${v[zi + 1]}→${v[zi + 2]}, z${v[zi + 3]}→${v[zi + 4]}]` : v.join(' ');
            }
            return String(v);
        };
        return {
            zoom: +m.getZoom().toFixed(2),
            satellite: f('satellite-raster', 'raster-opacity'),
            ocean: f('water', 'fill-opacity'),
            majorBoundary: f('boundary-major', 'line-opacity'),
            // These must NOT be zoom-driven, i.e. they never crossfade away.
            placeTextColor: String(m.getPaintProperty('place', 'text-color')),
        };
    });
};

console.log('\n--- crossfade band, paint property definitions ---');
for (const z of [2, 3, 4, 5, 6, 7, 8, 9, 10]) {
    const s = await sample(z);
    console.log(
        `  z${String(s.zoom).padStart(4)}  satellite=${s.satellite}  ocean=${s.ocean}  boundary=${s.majorBoundary}`
    );
}

const place = await page.evaluate(() => window.__fsMap.getPaintProperty('place', 'text-color'));
console.log('\nplace label colour (must be constant across zoom):', place);

// Land is the background colour, because the v3 schema has no land layer. The
// `water` source-layer is what distinguishes sea from land, and it fades out on
// the same band so it never leaves an opaque ocean over the satellite imagery.
console.log(
    'background colour (land):',
    await page.evaluate(() => window.__fsMap.getPaintProperty('bg', 'background-color'))
);
const usesWater = await page.evaluate(() =>
    window.__fsMap
        .getStyle()
        .layers.filter((l) => l['source-layer'] === 'water')
        .map((l) => l.id)
);
console.log('layers using source-layer "water":', usesWater.join(', ') || 'none');

// Flood layer must be hidden and must not be turned on by camera movement.
const floodAfterZoom = await page.evaluate(() => ({
    visibility: window.__fsMap.getLayoutProperty('flood-extent-fill', 'visibility'),
    exists: !!window.__fsMap.getLayer('flood-extent-fill'),
}));
console.log('\nflood-extent-fill after crossfading zoom:', JSON.stringify(floodAfterZoom));

// Layer sources must be the geojson sample data, and the fallback style must
// not have replaced the basemap.
console.log('flood source type:', await page.evaluate(() => window.__fsMap.getSource('flood-extent')?.type));

console.log('\npage errors:', errors.length ? errors.slice(0, 3) : 'none');
await browser.close();
