import puppeteer from 'puppeteer-core';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--use-gl=swiftshader', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 900 });
const logs = [];
page.on('console', (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on('pageerror', (e) => logs.push(`[pageerror] ${e}`));
await page.goto('http://localhost:3000', { waitUntil: 'networkidle2', timeout: 60000 });
await page.waitForSelector('.fs-floodmap__canvas canvas', { timeout: 30000 });
await sleep(5000);

const state = await page.evaluate(() => {
    const m = window.__fsMap;
    return {
        hasMap: !!m,
        isStyleLoaded: m?.isStyleLoaded(),
        loaded: m?.loaded(),
        styleLoaded: m?.styleLoaded,
        sources: Object.keys(m.getStyle().sources),
        layers: m.getStyle().layers.map((l) => l.id),
        floodSource: !!m.getSource('flood-extent'),
        overlay: document.querySelector('.fs-floodmap__status')?.textContent?.trim() ?? null,
        canvasCount: document.querySelectorAll('.fs-floodmap__canvas canvas').length,
    };
});
console.log(JSON.stringify(state, null, 2));
console.log('\n--- console ---');
console.log(logs.slice(0, 25).join('\n'));
await browser.close();
