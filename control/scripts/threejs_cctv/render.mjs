// export_scene.py가 만든 데이터를 헤드리스 Chromium의 Three.js로 렌더해 CCTV PNG·마스크를 저장한다.
//
//   node render.mjs --data ../../outputs/threejs_cctv/data --out ../../outputs/threejs_cctv [--frames 0:300]
//   node render.mjs --ros [http://127.0.0.1:8765]   # 브리지 실시간 렌더 → /cctv_N/image_raw (#55)
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
const rosIdx = process.argv.indexOf('--ros');
const rosUrl = rosIdx > 0 ? (process.argv[rosIdx + 1]?.startsWith('http') ? process.argv[rosIdx + 1] : 'http://127.0.0.1:8765') : null;
const dataDir = path.resolve(arg('data', path.join(here, rosUrl ? '../../outputs/threejs_live/data' : '../../outputs/threejs_cctv/data')));
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

const types = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.gltf': 'model/gltf+json', '.bin': 'application/octet-stream', '.hdr': 'application/octet-stream' };
const server = http.createServer((req, res) => {
  if (rosUrl && req.url.startsWith('/ros/')) {   // 브리지 프레임·영상 중계 (같은 출처로 두려고 여기서 넘긴다)
    const up = http.request(rosUrl + req.url.slice(4), { method: req.method, headers: req.headers }, r => {
      res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    up.on('error', e => { res.writeHead(502).end(String(e.message)); });
    req.pipe(up);
    return;
  }
  if (req.method === 'POST' && req.url.startsWith('/upload/')) {   // exportGLB 결과를 data 폴더에 저장
    const chunks = []; req.on('data', c => chunks.push(c));
    req.on('end', () => { fs.writeFileSync(path.join(dataDir, path.basename(req.url)), Buffer.concat(chunks)); res.writeHead(200).end(); });
    return;
  }
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

if (!fs.existsSync(path.join(here, 'assets', 'manifest.json'))) {
  console.error('assets/manifest.json이 없습니다. 먼저 `python fetch_assets.py`를 실행하세요.'); process.exit(1);
}
const browser = await chromium.launch({ executablePath: findChromium(),
  args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 1300, height: 500 } });
if (rosUrl) {   // 브리지가 scene.json을 쓴 뒤 렌더를 시작하고, 종료 신호까지 5초마다 처리율을 출력한다
  const scene = path.join(dataDir, 'scene.json');
  for (let i = 0; !fs.existsSync(scene) || !JSON.parse(fs.readFileSync(scene)).live; i++) {
    if (i === 0) console.log(`브리지를 기다리는 중: ${scene}`);
    await new Promise(r => setTimeout(r, 500));
  }
  page.on('pageerror', e => console.error('[page]', e.message));
  await page.goto(`http://127.0.0.1:${port}/?ros`);
  await page.waitForFunction(() => window.ready === true, null, { timeout: 300000 });
  console.log(`Three.js 실시간 렌더 시작: ${rosUrl}`);
  let prev = { frames: 0, images: 0 }, t = Date.now();
  const timer = setInterval(async () => {
    const s = await page.evaluate(() => window.rosStats).catch(() => null);
    if (!s) return;
    const dt = (Date.now() - t) / 1000; t = Date.now();
    console.log(`프레임 ${((s.frames - prev.frames) / dt).toFixed(1)} Hz, 영상 ${((s.images - prev.images) / dt).toFixed(1)} 장/s, `
      + `건너뜀 ${s.skipped}, 오류 ${s.errors}${s.lastError ? ` (${s.lastError})` : ''}`);
    prev = s;
  }, 5000);
  const stop = async () => { clearInterval(timer); await browser.close(); server.close(); process.exit(0); };
  process.on('SIGINT', stop); process.on('SIGTERM', stop);
  await new Promise(() => {});
}
page.on('console', m => { if (m.type() === 'error' || process.argv.includes('--glb')) console.error('[page]', m.text()); });
page.on('pageerror', e => console.error('[page]', e.message));
await page.goto(`http://127.0.0.1:${port}/` + (process.argv.includes('--glb') ? '?nobake' : ''));
await page.waitForFunction(() => window.ready === true, null, { timeout: 300000 });
const meta = await page.evaluate(() => window.meta);
if (process.argv.includes('--live-test')) {   // 실시간 재생 속도 측정 (5초)
  await page.goto(`http://127.0.0.1:${port}/?live&cam=${cams[0]}`);
  await page.waitForFunction(() => window.ready === true, null, { timeout: 300000 });
  await new Promise(r => setTimeout(r, 6000));
  console.log(await page.evaluate(() => document.body.lastElementChild.textContent || document.querySelector('div')?.textContent));
  await browser.close(); server.close(); process.exit(0);
}
if (process.argv.includes('--glb')) {   // Blender Cycles용 정적 장면만 내보내고 끝낸다
  console.log(await page.evaluate(() => window.exportGLB()));
  await browser.close(); server.close(); process.exit(0);
}
const [f0, f1] = arg('frames', `0:${meta.frames}`).split(':').map(Number);

for (const c of cams) for (const sub of [c, `${c}_mask`]) fs.mkdirSync(path.join(outDir, sub), { recursive: true });
const t0 = Date.now();
const boxes = [];
for (let f = f0; f < f1; f++) {
  for (const c of cams) {
    const { rgb, mask, boxes: b } = await page.evaluate(([c, f]) => window.renderFrame(c, f), [c, f]);
    boxes.push({ frame: f, camera: c, workers: b });
    const name = `${String(f).padStart(6, '0')}.png`;
    fs.writeFileSync(path.join(outDir, c, name), Buffer.from(rgb.split(',')[1], 'base64'));
    fs.writeFileSync(path.join(outDir, `${c}_mask`, name), Buffer.from(mask.split(',')[1], 'base64'));
  }
  if ((f + 1) % 50 === 0) console.log(`${f + 1}/${f1} 프레임 (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
}

// 정답 기록: 프레임 번호 = 파일 이름. 시뮬레이션 시각과 작업자 실제 위치 (MuJoCo 월드, m)
fs.copyFileSync(path.join(dataDir, 'truth.csv'), path.join(outDir, 'frames.csv'));
// 작업자별 화면에 보이는 영역 상자 (가려진 부분 제외, 픽셀 좌표). pixels=0 이면 화면에 안 보임
fs.writeFileSync(path.join(outDir, 'boxes.json'), JSON.stringify(boxes));
const camInfo = Object.fromEntries(Object.entries(meta.cameras).map(([k, v]) => {
  const fy = v.height / 2 / Math.tan(v.fovy * Math.PI / 360);
  return [k, { ...v, fx: fy, fy, cx: v.width / 2, cy: v.height / 2 }];
}));
fs.writeFileSync(path.join(outDir, 'cameras.json'), JSON.stringify({ fps: meta.fps, cameras: camInfo, workers: meta.workers, zone_rects: meta.zone_rects }, null, 2));
console.log(`완료: ${outDir}`);
await browser.close();
server.close();
