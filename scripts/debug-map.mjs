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
await page.goto('http://localhost:3000', { waitUntil: 'networkidle2', timeout: 60000 });
await page.waitForSelector('.fs-floodmap__canvas canvas', { timeout: 30000 });
await sleep(4000);

const hud = () =>
    page.evaluate(() => {
        const spans = [...document.querySelectorAll('.fs-map-bottombar__left span')];
        return {
            zoomTxt: spans.find((s) => /^ZOOM /.test(s.textContent || ''))?.textContent?.trim(),
            scaleTxt: document.querySelector('.fs-scale-bar')?.textContent?.trim(),
            mapZoom: window.__fsMap ? window.__fsMap.getZoom().toFixed(3) : 'NO_MAP',
        };
    });

const buttons = await page.evaluate(() =>
    [...document.querySelectorAll('.fs-map-controls__btn')].map((b) => ({
        label: b.getAttribute('aria-label'),
        disabled: b.disabled,
        rect: (({ x, y, width, height }) => ({ x, y, width, height }))(b.getBoundingClientRect()),
    }))
);
console.log('control buttons:', JSON.stringify(buttons, null, 2));

console.log('\ninitial:', JSON.stringify(await hud()));

await page.click('button[aria-label="Zoom in"]');
await sleep(2000);
console.log('after zoomIn :', JSON.stringify(await hud()));

// Is the handle wired at all? Drive the exposed map directly to compare.
await page.evaluate(() => window.__fsMap.zoomIn({ duration: 0 }));
await sleep(2000);
console.log('after direct :', JSON.stringify(await hud()));

await page.evaluate(() => window.__fsMap.flyTo({ center: [0, 20], zoom: 1.2, duration: 0 }));
await sleep(2500);
console.log('after flyTo  :', JSON.stringify(await hud()));

const worldBtn = await page.evaluate(() => {
    const b = document.querySelector('button[aria-label="World view"]');
    if (!b) return { found: false };
    const r = b.getBoundingClientRect();
    const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return { found: true, coveredBy: top?.tagName + '.' + (top?.className || ''), isSelf: top === b };
});
console.log('\nworld button hit-test:', JSON.stringify(worldBtn));

await browser.close();
