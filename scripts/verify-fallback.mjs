import puppeteer from 'puppeteer-core';

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const URL = process.env.VERIFY_URL || 'http://localhost:3000';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
function check(name, pass, detail = '') {
    results.push({ name, pass });
    console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`);
}

const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--use-gl=swiftshader', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 900 });

const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));

await page.goto(URL, { waitUntil: 'networkidle2', timeout: 60000 });
await page.waitForSelector('.fs-floodmap__canvas canvas', { timeout: 30000 });
await page.waitForFunction(() => !!window.__fsMap && window.__fsMap.isStyleLoaded(), { timeout: 30000 });
await sleep(2500);

const state = await page.evaluate(() => {
    const m = window.__fsMap;
    const style = m.getStyle();
    const canvas = document.querySelector('.fs-floodmap__canvas canvas');
    return {
        styleName: style.name,
        sources: Object.keys(style.sources),
        layers: style.layers.map((l) => l.id),
        glyphs: style.glyphs ?? null,
        searchPresent: !!document.querySelector('.fs-search'),
        errorOverlay: document.querySelector('.fs-floodmap__status--error')?.textContent?.trim() ?? null,
        sampleLayers: ['flood-extent-fill', 'roads-line', 'bridges-point', 'settlements-point'].map((id) => ({
            id,
            exists: !!m.getLayer(id),
            vis: m.getLayer(id) ? m.getLayoutProperty(id, 'visibility') : null,
        })),
        canvas: canvas ? { w: canvas.width, h: canvas.height } : null,
    };
});

check('key is actually blank (search box self-hides)', !state.searchPresent);
check('fallback style is in use', /fallback/i.test(state.styleName || ''), `name="${state.styleName}"`);
check('fallback makes NO remote requests', state.sources.length === 1 && 'graticule' in state.sources, `sources=${state.sources.join(',')}`);
check('fallback needs no glyph endpoint', state.glyphs === null);
check('graticule + background layers present', state.layers.includes('bg') && state.layers.includes('graticule'), state.layers.join(','));
check('no load-error overlay', state.errorOverlay === null, state.errorOverlay ?? '');

// The overlay layers are Nepal sample data and are independent of the basemap,
// so they must still be injected and still respect their toggles.
check(
    'Nepal sample layers still injected on the fallback style',
    state.sampleLayers.every((l) => l.exists),
    state.sampleLayers.map((l) => `${l.id}:${l.vis}`).join(' ')
);
check(
    'flood polygon still hidden by default on fallback',
    state.sampleLayers.find((l) => l.id === 'flood-extent-fill')?.vis === 'none'
);

// The canvas must actually be painting the fallback, not sitting empty.
// Sampled from a screenshot rather than gl.readPixels: a WebGL drawing buffer
// is cleared once the frame is composited, so readPixels after the fact returns
// transparent black regardless of what was drawn.
const shot = await page.screenshot({ encoding: 'base64', clip: { x: 700, y: 400, width: 2, height: 2 } });
const pixel = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = img.width;
    c.height = img.height;
    const ctx = c.getContext('2d');
    ctx.drawImage(img, 0, 0);
    return [...ctx.getImageData(0, 0, 1, 1).data];
}, shot);
// Background colour is BASEMAP.ocean (#2b3640) => 43,54,64.
check(
    'fallback canvas actually paints the ocean background',
    pixel[0] === 43 && pixel[1] === 54 && pixel[2] === 64,
    `sampled rgba=${pixel.join(',')}`
);

// Camera controls must still work with no basemap to load.
await page.click('button[aria-label="Zoom in"]');
await sleep(500);
const zoomed = await page.evaluate(() => window.__fsMap.getZoom());
check('map is still pannable/zoomable on the fallback', zoomed > 6, `zoom=${zoomed.toFixed(2)}`);

const hud = await page.evaluate(() => {
    const spans = [...document.querySelectorAll('.fs-map-bottombar__left span')];
    return spans.find((s) => /^ZOOM /.test(s.textContent || ''))?.textContent?.trim() ?? null;
});
check('HUD readout still live on the fallback', /ZOOM 7/.test(hud || ''), hud ?? 'null');

check('no uncaught page errors', pageErrors.length === 0, pageErrors.slice(0, 2).join(' | '));

await browser.close();
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
if (failed.length) process.exit(1);