// export_scene.py가 만든 데이터를 헤드리스 Chromium의 Three.js로 렌더해 CCTV PNG·마스크를 저장한다.
//
//   node render.mjs --data ../../outputs/threejs_cctv/data --out ../../outputs/threejs_cctv [--frames 0:300]
//
// 출력: <out>/cctv_N/000000.png, <out>/cctv_N_mask/000000.png, <out>/frames.csv, <out>/cameras.json
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const here = path.dirname(fileURLToPath(import.meta.url));
const arg = (name, def) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : def; };
const dataDir = path.resolve(arg('data', path.join(here, '../../outputs/threejs_cctv/data')));
const outDir = path.resolve(arg('out', path.join(here, '../../outputs/threejs_cctv')));
const cams = arg('cameras', 'cctv_1,cctv_2').split(',');

function findChromium() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  const root = path.join(os.homedir(), 'Library/Caches/ms-playwright');
  const linux = path.join(os.homedir(), '.cache/ms-playwright');
  for (const base of [root, linux]) {
    if (!fs.existsSync(base)) continue;
    for (const d of fs.readdirSync(base).filter(d => d.startsWith('chromium')).sort().reverse()) {
      for (const rel of ['chrome-headless-shell-mac-arm64/chrome-headless-shell',
        'chrome-headless-shell-linux64/chrome-headless-shell',
        'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
        'chrome-linux64/chrome']) {
        const p = path.join(base, d, rel);
        if (fs.existsSync(p)) return p;
      }
    }
  }
  throw new Error('Chromium을 찾지 못했습니다. CHROMIUM_PATH를 지정하거나 `npx playwright install chromium`을 실행하세요.');
}

const types = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png' };
const server = http.createServer((req, res) => {
  const url = decodeURIComponent(req.url.split('?')[0]);
  const file = url.startsWith('/data/') ? path.join(dataDir, url.slice(6))
    : path.join(here, url === '/' ? 'render.html' : url);
  if (!file.startsWith(dataDir) && !file.startsWith(here)) { res.writeHead(403).end(); return; }
  fs.readFile(file, (err, buf) => {
    if (err) { res.writeHead(404).end(); return; }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(buf);
  });
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const port = server.address().port;

const browser = await chromium.launch({ executablePath: findChromium(),
  args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 1300, height: 500 } });
page.on('console', m => { if (m.type() === 'error') console.error('[page]', m.text()); });
page.on('pageerror', e => console.error('[page]', e.message));
await page.goto(`http://127.0.0.1:${port}/`);
await page.waitForFunction(() => window.ready === true, null, { timeout: 120000 });
const meta = await page.evaluate(() => window.meta);
const [f0, f1] = arg('frames', `0:${meta.frames}`).split(':').map(Number);

for (const c of cams) for (const sub of [c, `${c}_mask`]) fs.mkdirSync(path.join(outDir, sub), { recursive: true });
const t0 = Date.now();
for (let f = f0; f < f1; f++) {
  for (const c of cams) {
    const { rgb, mask } = await page.evaluate(([c, f]) => window.renderFrame(c, f), [c, f]);
    const name = `${String(f).padStart(6, '0')}.png`;
    fs.writeFileSync(path.join(outDir, c, name), Buffer.from(rgb.split(',')[1], 'base64'));
    fs.writeFileSync(path.join(outDir, `${c}_mask`, name), Buffer.from(mask.split(',')[1], 'base64'));
  }
  if ((f + 1) % 50 === 0) console.log(`${f + 1}/${f1} 프레임 (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
}

// 정답 기록: 프레임 번호 = 파일 이름. 시뮬레이션 시각과 작업자 실제 위치 (MuJoCo 월드, m)
fs.copyFileSync(path.join(dataDir, 'truth.csv'), path.join(outDir, 'frames.csv'));
const camInfo = Object.fromEntries(Object.entries(meta.cameras).map(([k, v]) => {
  const fy = v.height / 2 / Math.tan(v.fovy * Math.PI / 360);
  return [k, { ...v, fx: fy, fy, cx: v.width / 2, cy: v.height / 2 }];
}));
fs.writeFileSync(path.join(outDir, 'cameras.json'), JSON.stringify({ fps: meta.fps, cameras: camInfo }, null, 2));
console.log(`완료: ${outDir}`);
await browser.close();
server.close();
