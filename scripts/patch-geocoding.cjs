const fs = require('fs');

let mapCode = fs.readFileSync('app/map.tsx', 'utf8');
mapCode = mapCode.replace(/import '@maptiler\/geocoding-control\/style\.css';\r?\n/, '');
fs.writeFileSync('app/map.tsx', mapCode, 'utf8');
console.log('Removed invalid style.css import from app/map.tsx');

const gcPath = 'node_modules/@maptiler/geocoding-control/dist/maplibregl.js';
if (fs.existsSync(gcPath)) {
  let gcCode = fs.readFileSync(gcPath, 'utf8');
  gcCode = gcCode.replace('import H from "maplibre-gl";', 'import * as H from "maplibre-gl";');
  fs.writeFileSync(gcPath, gcCode, 'utf8');
  console.log('Patched maplibregl.js in @maptiler/geocoding-control');
}
