// MuJoCo prop_<종류> geom을 실사형 설비 모델로 바꾼다.
// 모든 빌더는 geom 지역 좌표(z 위, 원점 = geom 중심)에서 만들고, size는 MuJoCo geom size를 그대로 받는다.
//   box: (반길이 x, y, z)   cylinder: (반지름, 반높이)
import * as THREE from 'three';

const S = THREE.SRGBColorSpace;
const mat = (hex, rough = 0.6, metal = 0.1, extra = {}) =>
  new THREE.MeshStandardMaterial({ color: new THREE.Color(hex), roughness: rough, metalness: metal, ...extra });
export const M = {
  yellow: mat(0xe6b31e, 0.45, 0.2), yellowPaint: mat(0xf2c418, 0.55, 0.05),
  steel: mat(0xc9ccd0, 0.28, 0.9), darkSteel: mat(0x3a3d42, 0.5, 0.7), galv: mat(0x9a9fa5, 0.45, 0.8),
  black: mat(0x161616, 0.8, 0.1), rubber: mat(0x1b1b1b, 0.95, 0), concrete: mat(0xb8b4ab, 0.92, 0),
  grey: mat(0x7d8288, 0.6, 0.3), wood: mat(0xa7835a, 0.85, 0), carton: mat(0xc9b08a, 0.9, 0),
  red: mat(0xb3261e, 0.6, 0.1), green: mat(0x3f7f4a, 0.5, 0.3), blue: mat(0x2f5f9e, 0.5, 0.3),
  hole: new THREE.MeshBasicMaterial({ color: 0x050505 }),
};

const box = (w, d, h, m) => new THREE.Mesh(new THREE.BoxGeometry(w, d, h), m);
const cylZ = (r, h, m, seg = 32) => new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, seg).rotateX(Math.PI / 2), m);
const cylX = (r, h, m, seg = 24) => new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, seg).rotateZ(Math.PI / 2), m);
const cylY = (r, h, m, seg = 24) => new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, seg), m);
const at = (o, x, y, z) => { o.position.set(x, y, z); return o; };

function canvasTex(w, h, draw) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c); t.colorSpace = S; t.anisotropy = 8; return t;
}

// 경고 표지판 (빨간 바탕, 흰 삼각형·글자)
export function sign(lines, w = 1.6, h = 0.7, color = '#b3261e') {
  const tex = canvasTex(512, Math.round(512 * h / w), (g, W, H) => {
    g.fillStyle = color; g.fillRect(0, 0, W, H);
    g.strokeStyle = '#fff'; g.lineWidth = 6; g.strokeRect(8, 8, W - 16, H - 16);
    const s = H * 0.42, x0 = 26, y0 = H / 2;
    g.fillStyle = '#fff'; g.beginPath(); g.moveTo(x0, y0 + s / 2); g.lineTo(x0 + s / 2, y0 - s / 2); g.lineTo(x0 + s, y0 + s / 2); g.fill();
    g.fillStyle = color; g.font = `bold ${s * 0.6}px sans-serif`; g.textAlign = 'center'; g.fillText('!', x0 + s / 2, y0 + s * 0.4);
    g.fillStyle = '#fff'; g.textAlign = 'left';
    const tx = x0 + s + 22, room = W - tx - 24;
    let fs = Math.min(H / (lines.length + 0.8), 46);
    lines.forEach((l, i) => { g.font = `bold ${i ? fs * 0.7 : fs}px sans-serif`;
      const k = Math.min(1, room / g.measureText(l).width); fs = Math.min(fs, fs * k / (i ? 1 : 1)); });
    lines.forEach((l, i) => { g.font = `bold ${i ? fs * 0.7 : fs}px sans-serif`;
      g.fillText(l, tx, H / 2 + (i - (lines.length - 1) / 2) * fs * 1.05 + fs * 0.35, room); });
  });
  return new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshStandardMaterial({ map: tex, roughness: 0.7 }));
}

const fenceTex = canvasTex(256, 256, (g, W, H) => {
  g.clearRect(0, 0, W, H); g.strokeStyle = '#2b2d30'; g.lineWidth = 6;
  for (let i = 0; i <= W; i += 32) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, H); g.stroke(); g.beginPath(); g.moveTo(0, i); g.lineTo(W, i); g.stroke(); }
});
fenceTex.wrapS = fenceTex.wrapT = THREE.RepeatWrapping;

const hazardTex = canvasTex(512, 64, (g, W, H) => {
  g.fillStyle = '#f2c418'; g.fillRect(0, 0, W, H); g.fillStyle = '#111';
  for (let x = -H; x < W + H; x += 64) { g.beginPath(); g.moveTo(x, H); g.lineTo(x + 32, H); g.lineTo(x + 32 + H, 0); g.lineTo(x + H, 0); g.fill(); }
});
hazardTex.wrapS = THREE.RepeatWrapping;

// ---- 실사 에셋 (Poly Haven CC0, fetch_assets.py) ----
export const T = {};
export async function applyAssets(manifest, base) {
  const loader = new THREE.TextureLoader();
  const load = async (path, srgb, rep) => { const t = await loader.loadAsync(base + path);
    t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 8; if (srgb) t.colorSpace = S; t.repeat.set(rep, rep); return t; };
  const set = async (role, rep) => { const m = manifest.textures[role];
    T[role] = { map: await load(m.diff, true, rep), normalMap: await load(m.nor, false, rep), roughnessMap: await load(m.arm, false, rep) };
    T[role].metalnessMap = T[role].roughnessMap; T[role].aoMap = T[role].roughnessMap; return T[role]; };
  await set('floor', 1); await set('wall', 1); await set('building', 1);
  const metal = await set('metal', 2), rust = await set('rust', 2);
  // 금속: 판재 법선·거칠기 맵, 도장면: 녹슨 도장의 법선·거칠기 맵 (색은 유지)
  // Blender 렌더용 표시: 텍스처는 GLB에 넣지 않고 Blender가 원본 파일을 다시 붙인다
  for (const [k, m] of Object.entries(M)) { m.name = `m_${k}`;
    m.userData.pbr = ['steel', 'galv', 'darkSteel', 'grey'].includes(k) ? 'metal' : ['yellow', 'yellowPaint', 'red', 'green', 'blue'].includes(k) ? 'rust' : undefined;
    if (m.userData.pbr) { m.userData.repeat = [2, 2]; m.userData.mapsOnly = 'detail'; } }
  for (const k of ['steel', 'galv', 'darkSteel', 'grey']) Object.assign(M[k], { normalMap: metal.normalMap, roughnessMap: metal.roughnessMap, metalnessMap: metal.roughnessMap, normalScale: new THREE.Vector2(0.6, 0.6) });
  for (const k of ['yellow', 'yellowPaint', 'red', 'green', 'blue']) Object.assign(M[k], { normalMap: rust.normalMap, roughnessMap: rust.roughnessMap, metalnessMap: rust.roughnessMap, normalScale: new THREE.Vector2(0.4, 0.4) });
  Object.values(M).forEach(m => { m.needsUpdate = true; });
}
const pbr = (role, rx, ry, extra = {}) => {
  if (!T[role]) return null;
  const maps = {};
  for (const [k, t] of Object.entries(T[role])) { const c = t.clone(); c.repeat.set(rx, ry); c.needsUpdate = true; maps[k] = c; }
  maps.metalnessMap = maps.roughnessMap;   // Poly Haven ARM = glTF metallicRoughness 배치 (G 거칠기, B 금속)
  const m = new THREE.MeshStandardMaterial({ ...maps, roughness: 1, metalness: 0, ...extra });
  m.name = `pbr_${role}`;
  m.userData.pbr = role; m.userData.repeat = [rx, ry];
  // glTF는 기본색 1 초과를 허용하지 않으므로 밝기 배율을 extras로 넘긴다 (Blender 렌더에서 사용)
  if (extra.color && extra.color.isColor) m.userData.brightness = Math.max(extra.color.r, extra.color.g, extra.color.b);
  return m;
};
export { pbr };

// ---- 빌더 ----
const B = {};

B.pole = ([r, h]) => {
  const g = new THREE.Group();
  g.add(cylZ(r, 2 * h, M.galv));
  const arm = at(box(0.5, 0.08, 0.08, M.galv), 0, 0, h - 0.1); g.add(arm);
  const dome = at(new THREE.Mesh(new THREE.SphereGeometry(0.16, 24, 12, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2), mat(0x222222, 0.1, 0.2, { transparent: true, opacity: 0.85 })), 0, 0, h + 0.05);
  const housing = at(cylZ(0.18, 0.12, mat(0xf0f0f0, 0.4, 0.1)), 0, 0, h + 0.12);
  g.add(dome, housing); return g;
};

B.fence = ([sx, sy, sz]) => {
  const g = new THREE.Group();
  const along = sx > sy, L = 2 * Math.max(sx, sy), H = 2 * sz;
  const t = fenceTex.clone(); t.needsUpdate = true; t.repeat.set(L / 0.6, H / 0.6);
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(L, H - 0.15),
    new THREE.MeshStandardMaterial({ map: t, transparent: true, alphaTest: 0.05, side: THREE.DoubleSide, color: 0x6a6f75, metalness: 0.6, roughness: 0.5 }));
  panel.rotation.x = Math.PI / 2; if (!along) panel.rotation.y = Math.PI / 2;
  panel.position.z = 0.05; panel.castShadow = true; g.add(panel);
  const n = Math.round(L / 1.25);
  for (let i = 0; i <= n; i++) {
    const s = -L / 2 + (L * i) / n;
    g.add(at(box(0.06, 0.06, H, M.yellowPaint), along ? s : 0, along ? 0 : s, 0));
  }
  const rail = along ? box(L, 0.05, 0.05, M.yellowPaint) : box(0.05, L, 0.05, M.yellowPaint);
  g.add(at(rail, 0, 0, sz)); g.add(at(rail.clone(), 0, 0, -sz + 0.08));
  return g;
};

// 산업용 6축 로봇팔 (노란색). 프레임 시각에 따라 천천히 움직인다.
B.arm = ([r, h], idx) => {
  const g = new THREE.Group();
  g.add(at(cylZ(r * 1.1, 0.3, M.darkSteel), 0, 0, -h + 0.15));
  const turret = new THREE.Group(); turret.position.z = -h + 0.3; g.add(turret);
  turret.add(at(cylZ(r * 0.9, 0.45, M.yellow), 0, 0, 0.22));
  const shoulder = new THREE.Group(); shoulder.position.set(0, 0, 0.55); turret.add(shoulder);
  shoulder.add(cylY(0.22, 0.5, M.darkSteel).rotateX(Math.PI / 2));
  const upper = at(box(0.28, 0.32, 1.25, M.yellow), 0, 0, 0.62); shoulder.add(upper);
  const elbow = new THREE.Group(); elbow.position.z = 1.25; shoulder.add(elbow);
  elbow.add(cylY(0.18, 0.42, M.darkSteel).rotateX(Math.PI / 2));
  const fore = at(box(1.1, 0.24, 0.24, M.yellow), 0.55, 0, 0); elbow.add(fore);
  const wrist = new THREE.Group(); wrist.position.x = 1.1; elbow.add(wrist);
  wrist.add(cylX(0.12, 0.22, M.darkSteel), at(box(0.1, 0.3, 0.06, M.galv), 0.18, 0, -0.08));
  const ph = idx * 1.7;
  g.userData.update = t => {
    turret.rotation.z = ph + 0.6 * Math.sin(0.35 * t + ph);
    shoulder.rotation.y = 0.35 + 0.25 * Math.sin(0.5 * t + ph);
    elbow.rotation.y = 0.5 + 0.35 * Math.sin(0.5 * t + ph + 1);
    wrist.rotation.y = 0.6 * Math.sin(0.8 * t + ph);
  };
  return g;
};

B.console = ([sx, sy, sz]) => {
  const g = new THREE.Group();
  g.add(box(2 * sx, 2 * sy, 2 * sz, M.grey));
  const screen = at(box(0.36, 0.02, 0.26, mat(0x0b2a4a, 0.2, 0, { emissive: 0x1d5c9e, emissiveIntensity: 0.8 })), 0, -sy - 0.01, sz - 0.25);
  g.add(screen, at(box(0.04, 0.03, 0.04, mat(0xff2a2a, 0.4, 0, { emissive: 0xff0000, emissiveIntensity: 1 })), 0.18, -sy - 0.02, sz - 0.5));
  return g;
};

B.tank = ([r, h]) => {
  const g = new THREE.Group();
  g.add(cylZ(r, 2 * h - 0.6, M.steel, 48));
  const dome = new THREE.Mesh(new THREE.SphereGeometry(r, 48, 16, 0, Math.PI * 2, 0, Math.PI / 2).rotateX(Math.PI / 2), M.steel);
  dome.scale.z = 0.35; dome.position.z = h - 0.3; g.add(dome);
  for (const z of [-h + 0.6, 0, h - 0.6]) g.add(at(cylZ(r + 0.02, 0.06, M.galv, 48), 0, 0, z));
  for (let i = 0; i < 4; i++) { const a = i * Math.PI / 2 + 0.4;
    g.add(at(box(0.12, 0.12, 0.6, M.galv), Math.cos(a) * r * 0.85, Math.sin(a) * r * 0.85, -h + 0.3)); }
  // 사다리
  for (const dx of [-0.2, 0.2]) g.add(at(box(0.04, 0.04, 2 * h, M.yellowPaint), dx, -r - 0.18, 0));
  for (let z = -h + 0.3; z < h; z += 0.3) g.add(at(box(0.4, 0.03, 0.03, M.yellowPaint), 0, -r - 0.18, z));
  // 상부 난간
  const ring = new THREE.Mesh(new THREE.TorusGeometry(r * 0.75, 0.025, 6, 40), M.yellowPaint); ring.position.z = h + 0.6; g.add(ring);
  for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4; g.add(at(box(0.03, 0.03, 0.6, M.yellowPaint), Math.cos(a) * r * 0.75, Math.sin(a) * r * 0.75, h + 0.3)); }
  const s = sign(['CHEMICAL STORAGE', 'RESTRICTED ACCESS'], 1.4, 0.45); s.rotation.x = Math.PI / 2; s.position.set(0, -r - 0.02, 0.2); g.add(s);
  return g;
};

B.piperack = ([sx, sy, sz]) => {
  const g = new THREE.Group();
  const n = Math.round(sx / 1.4);
  for (let i = 0; i <= n; i++) { const x = -sx + (2 * sx * i) / n;
    for (const y of [-sy, sy]) g.add(at(box(0.25, 0.25, 2 * sz, M.galv), x, y, 0));
    for (const z of [-0.2, sz * 0.5, sz - 0.1]) g.add(at(box(0.2, 2 * sy, 0.2, M.galv), x, 0, z));
  }
  const pipes = [[0.25, M.steel], [0.18, M.yellowPaint], [0.15, M.green], [0.2, M.galv], [0.12, M.red], [0.3, M.steel]];
  pipes.forEach(([r, m], i) => {
    const level = i < 3 ? -0.2 : sz * 0.5, y = -sy + 0.35 + (i % 3) * (2 * sy - 0.7) / 2;
    g.add(at(cylX(r, 2 * sx + 1, m), 0, y, level + 0.1 + r));
  });
  g.add(at(cylX(0.35, 2 * sx + 1, M.steel), 0, 0, sz + 0.25));
  return g;
};

// 지게차: 길이 방향 x, +x 쪽이 포크
B.forklift = ([sx, sy, sz]) => {
  const g = new THREE.Group(); const z0 = -sz;
  const bodyL = 1.5, bx = -sx + bodyL / 2 + 0.1;
  g.add(at(box(bodyL, 2 * sy - 0.1, 0.7, M.yellowPaint), bx, 0, z0 + 0.6));
  g.add(at(box(0.5, 2 * sy - 0.1, 0.6, M.darkSteel), -sx + 0.3, 0, z0 + 1.0));            // 균형추
  g.add(at(box(0.5, 0.5, 0.5, M.black), bx, 0, z0 + 1.2));                                   // 좌석
  for (const [dx, dy] of [[-0.6, -0.55], [-0.6, 0.55], [0.6, -0.55], [0.6, 0.55]])           // 안전 가드
    g.add(at(box(0.06, 0.06, 1.25, M.black), bx + dx, dy, z0 + 1.55));
  g.add(at(box(1.3, 1.16, 0.05, M.black), bx, 0, z0 + 2.17));
  for (const [dx, r] of [[-0.55, 0.28], [0.55, 0.33]]) for (const dy of [-sy + 0.12, sy - 0.12]) {
    const w = at(cylY(r, 0.22, M.rubber), bx + dx, dy, z0 + r); g.add(w); }
  const mx = bx + bodyL / 2 + 0.1;
  for (const dy of [-0.3, 0.3]) g.add(at(box(0.1, 0.1, 2.15, M.darkSteel), mx, dy, z0 + 1.08));
  for (const dy of [-0.25, 0.25]) g.add(at(box(1.0, 0.1, 0.04, M.darkSteel), mx + 0.55, dy, z0 + 0.15));
  // 팔레트 + 상자 적재
  const px = mx + 0.6; g.add(at(box(1.0, 1.0, 0.12, M.wood), px, 0, z0 + 0.24));
  for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++) for (let k = 0; k < 3; k++)
    g.add(at(box(0.46, 0.46, 0.3, M.carton), px - 0.24 + 0.48 * i, -0.24 + 0.48 * j, z0 + 0.46 + 0.31 * k));
  return g;
};

B.building = ([sx, sy, sz]) => {
  const g = new THREE.Group();
  const wallTex = canvasTex(512, 512, (c, W, H) => {
    c.fillStyle = '#c7c3ba'; c.fillRect(0, 0, W, H);
    let seed = 3; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    for (let i = 0; i < 4000; i++) { c.fillStyle = `rgba(80,80,70,${rnd() * 0.08})`; c.fillRect(rnd() * W, rnd() * H, 3, 3); }
    c.strokeStyle = 'rgba(90,90,85,0.6)'; c.lineWidth = 2;
    for (let y = 0; y < H; y += 128) { c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke(); }
  });
  wallTex.wrapS = wallTex.wrapT = THREE.RepeatWrapping; wallTex.repeat.set(2, 2);
  g.add(box(2 * sx, 2 * sy, 2 * sz, pbr('building', 2, 2, { color: new THREE.Color(1.6, 1.58, 1.52) }) || new THREE.MeshStandardMaterial({ map: wallTex, roughness: 0.9 })));
  g.add(at(box(2 * sx + 0.2, 2 * sy + 0.2, 0.2, M.concrete), 0, 0, sz + 0.1));
  // 문 (현장 쪽 -x 면)
  g.add(at(box(0.04, 1.3, 2.3, mat(0x1d1f22, 0.7, 0.3)), -sx - 0.01, 0.2, -sz + 1.15));
  g.add(at(box(0.08, 1.45, 0.08, M.galv), -sx - 0.03, 0.2, -sz + 2.34));
  const s = sign(['HIGH VOLTAGE', 'ELECTRICAL ROOM', 'AUTHORIZED ACCESS ONLY'], 1.8, 0.8);
  s.rotation.set(Math.PI / 2, -Math.PI / 2, 0); s.position.set(-sx - 0.02, 0.2, -sz + 3.1); g.add(s);
  g.add(at(box(0.08, 0.25, 0.35, M.grey), -sx - 0.05, 1.1, -sz + 1.3));
  g.add(at(box(0.9, 0.7, 0.6, M.galv), 0.3, -0.6, sz + 0.5));                               // 옥상 실외기
  return g;
};

B.drum = ([r, h]) => {
  const g = new THREE.Group();
  g.add(cylZ(r, 2 * h, M.steel, 40));
  for (const z of [-h, h]) g.add(at(cylZ(r + 0.06, 0.08, M.galv, 40), 0, 0, z));
  for (let i = 0; i < 16; i++) { const a = i * Math.PI / 8;
    g.add(at(cylZ(0.025, 0.1, M.darkSteel, 8), Math.cos(a) * (r + 0.02), Math.sin(a) * (r + 0.02), -h - 0.05)); }
  const s = sign(['DANGER', 'CONFINED SPACE'], 0.8, 0.6, '#c62828');
  s.position.set(0, 0, -h - 0.1); s.rotation.set(Math.PI, 0, 0); g.add(s);
  return g;
};

B.manhole = ([r]) => {
  const g = new THREE.Group();
  const hole = new THREE.Mesh(new THREE.CircleGeometry(r * 0.7, 48), M.hole); hole.position.z = 0.004; g.add(hole);
  const rim = new THREE.Mesh(new THREE.RingGeometry(r * 0.7, r * 0.82, 48), M.galv); rim.position.z = 0.005; g.add(rim);
  const t = hazardTex.clone(); t.needsUpdate = true; t.repeat.set(6, 1);
  const ring = new THREE.Mesh(new THREE.RingGeometry(r * 0.88, r * 1.25, 64), new THREE.MeshStandardMaterial({ map: t, roughness: 0.8 }));
  ring.position.z = 0.004; g.add(ring);
  return g;
};

B.rail = ([sx, sy, sz]) => {
  const g = new THREE.Group(); const L = 2 * Math.max(sx, sy), along = sx > sy;
  const n = Math.round(L / 1.2);
  for (let i = 0; i <= n; i++) { const s = -L / 2 + (L * i) / n; g.add(at(box(0.05, 0.05, 2 * sz, M.yellowPaint), along ? s : 0, along ? 0 : s, 0)); }
  for (const z of [sz - 0.03, 0]) g.add(at(along ? box(L, 0.05, 0.05, M.yellowPaint) : box(0.05, L, 0.05, M.yellowPaint), 0, 0, z));
  g.add(at(along ? box(L, 0.02, 0.12, M.yellowPaint) : box(0.02, L, 0.12, M.yellowPaint), 0, 0, -sz + 0.06));
  return g;
};

export function buildProp(name, size, index) {
  const kind = name.replace(/^prop_/, '').replace(/_\d+$/, '');
  const f = B[kind];
  if (!f) return null;
  const g = f(size, index);
  g.traverse(o => { if (o.isMesh) { o.castShadow = !(kind === 'manhole'); o.receiveShadow = true; } });
  return g;
}

// MuJoCo 바닥(world 좌표 m)을 그대로 그리는 바닥 텍스처: 콘크리트 + 이음매 + 도장 표시
export function floorTexture(halfX, halfY, zones, ppm = 96) {
  const W = Math.round(2 * halfX * ppm), H = Math.round(2 * halfY * ppm);
  const X = x => (x + halfX) * ppm, Y = y => (halfY - y) * ppm;
  return canvasTex(W, H, (g) => {
    let seed = 11; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    g.clearRect(0, 0, W, H);   // 콘크리트는 PBR 텍스처, 여기는 도장·얼룩만
    for (let i = 0; i < 160; i++) { const r = 20 + rnd() * 160; g.fillStyle = `rgba(60,58,54,${0.02 + rnd() * 0.04})`;
      g.beginPath(); g.ellipse(rnd() * W, rnd() * H, r, r * (0.4 + rnd()), rnd() * 3, 0, 7); g.fill(); }
    g.strokeStyle = 'rgba(60,58,55,0.55)'; g.lineWidth = 2;                                    // 3 m 슬래브 이음매
    for (let x = -halfX; x <= halfX; x += 3) { g.beginPath(); g.moveTo(X(x), 0); g.lineTo(X(x), H); g.stroke(); }
    for (let y = -halfY; y <= halfY; y += 3) { g.beginPath(); g.moveTo(0, Y(y)); g.lineTo(W, Y(y)); g.stroke(); }
    const rect = (cx, cy, sx, sy) => [X(cx - sx), Y(cy + sy), 2 * sx * ppm, 2 * sy * ppm];
    // 금지구역: 빨간 빗금 + 노랑·검정 경계
    if (zones.keep_out) { const [x, y, w, h] = rect(...zones.keep_out);
      g.save(); g.beginPath(); g.rect(x, y, w, h); g.clip();
      g.fillStyle = 'rgba(190,40,35,0.35)'; g.fillRect(x, y, w, h);
      g.strokeStyle = 'rgba(200,35,30,0.85)'; g.lineWidth = 0.06 * ppm;
      for (let k = -h; k < w; k += 0.25 * ppm) { g.beginPath(); g.moveTo(x + k, y + h); g.lineTo(x + k + h, y); g.stroke(); }
      g.restore();
      const b = 0.1 * ppm; g.lineWidth = b;
      g.setLineDash([0.2 * ppm, 0.2 * ppm]); g.strokeStyle = '#f2c418'; g.strokeRect(x - b / 2, y - b / 2, w + b, h + b);
      g.lineDashOffset = 0.2 * ppm; g.strokeStyle = '#111'; g.strokeRect(x - b / 2, y - b / 2, w + b, h + b); g.setLineDash([]); g.lineDashOffset = 0;
    }
    for (const [z, col] of [[zones.a, '#2f6fd0'], [zones.b, '#2fa04a']]) if (z) {               // 작업대 구역 외곽선
      const [x, y, w, h] = rect(...z); g.strokeStyle = col; g.lineWidth = 0.06 * ppm; g.strokeRect(x, y, w, h); }
    // 보행 통로: 노란 실선 두 줄 + 진행 방향 화살표
    g.strokeStyle = 'rgba(235,190,30,0.9)'; g.lineWidth = 0.1 * ppm;
    const lane = (x0, y0, x1, y1) => { g.beginPath(); g.moveTo(X(x0), Y(y0)); g.lineTo(X(x1), Y(y1)); g.stroke(); };
    lane(-1.5, -2.0, 13.5, -2.0); 
    lane(-9, 0.4, -1.8, 0.4); lane(2.6, 1.6, 13.5, 1.6); lane(2.6, 1.6, 2.6, 6.8);
    g.fillStyle = 'rgba(240,195,25,0.95)';
    const chevron = (x, y, dir) => { g.save(); g.translate(X(x), Y(y)); g.rotate(-dir); const s = 0.22 * ppm;
      g.beginPath(); g.moveTo(-s, -s); g.lineTo(0, 0); g.lineTo(-s, s); g.lineTo(-s * 0.55, s); g.lineTo(s * 0.45, 0); g.lineTo(-s * 0.55, -s); g.fill(); g.restore(); };
    for (let x = 4; x < 13; x += 0.8) chevron(x, -2.45, 0);
    for (let y = 2.4; y < 6.5; y += 0.8) chevron(3.0, y, Math.PI / 2);
    // 로봇 셀 주변 빨간 점선 경계
    g.strokeStyle = 'rgba(200,40,35,0.85)'; g.lineWidth = 0.07 * ppm; g.setLineDash([0.4 * ppm, 0.25 * ppm]);
    g.strokeRect(X(-7.4), Y(5.4), 5.8 * ppm, 4.8 * ppm); g.setLineDash([]);
    // 기름 얼룩·타이어 자국
    for (let i = 0; i < 25; i++) { g.fillStyle = `rgba(40,38,35,${0.05 + rnd() * 0.1})`; g.beginPath();
      g.ellipse(X(-10 + rnd() * 22), Y(-8 + rnd() * 15), 10 + rnd() * 40, 6 + rnd() * 20, rnd() * 3, 0, 7); g.fill(); }
    g.strokeStyle = 'rgba(30,30,30,0.12)'; g.lineWidth = 0.18 * ppm;
    for (const off of [-0.45, 0.45]) { g.beginPath(); g.moveTo(X(1), Y(-3 + off)); g.bezierCurveTo(X(5), Y(-3 + off), X(8), Y(-1 + off), X(12), Y(-1 + off)); g.stroke(); }
  });
}

// 현장 바깥 배경 (MuJoCo에 없음, 작업자 이동 범위 밖): 공장 건물 벽·지붕 트러스·먼 배관
export function backdrop(halfX, halfY) {
  const g = new THREE.Group();
  const panel = canvasTex(512, 512, (c, W, H) => {
    c.fillStyle = '#8e969c'; c.fillRect(0, 0, W, H);
    for (let x = 0; x < W; x += 16) { c.fillStyle = x % 32 ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.12)'; c.fillRect(x, 0, 8, H); }
  });
  panel.wrapS = panel.wrapT = THREE.RepeatWrapping;
  const wallM = new THREE.MeshStandardMaterial({ map: panel, roughness: 0.6, metalness: 0.4 });
  const wall = (L, h, x, y, rotZ) => { const t = panel.clone(); t.needsUpdate = true; t.repeat.set(L / 4, h / 4);
    const m = new THREE.Mesh(new THREE.PlaneGeometry(L, h), pbr('wall', L / 3, h / 3, { metalness: 0.2, color: new THREE.Color(1.9, 2.0, 2.1), side: THREE.DoubleSide }) || new THREE.MeshStandardMaterial({ map: t, roughness: 0.6, metalness: 0.4 }));
    m.rotation.set(Math.PI / 2, rotZ, 0); m.position.set(x, y, h / 2); m.receiveShadow = true; g.add(m); };
  const X = halfX + 3, Y = halfY + 3;
  wall(2 * X, 7, 0, Y, 0); wall(2 * Y, 7, -X, 0, Math.PI / 2); wall(2 * Y, 7, X, 0, -Math.PI / 2); wall(2 * X, 7, 0, -Y, Math.PI);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(2 * X, 2 * Y), pbr('floor', X / 2, Y / 2, { color: new THREE.Color(2.2, 2.15, 2.1) }) || mat(0x8f8c86, 0.95, 0)); ground.position.z = -0.01; ground.receiveShadow = true; g.add(ground);
  // 지붕 트러스(그림자용)와 기둥
  for (let x = -X; x <= X; x += 6) {
    g.add(at(box(0.4, 0.4, 7, M.galv), x, Y - 0.3, 3.5), at(box(0.4, 0.4, 7, M.galv), x, -Y + 0.3, 3.5));
  }
  // 벽면 고정 탱크·배관
  for (let i = 0; i < 4; i++) { const t = new THREE.Group(); t.add(cylZ(1.4, 6, M.steel, 40));
    const d = new THREE.Mesh(new THREE.SphereGeometry(1.4, 32, 12, 0, Math.PI * 2, 0, Math.PI / 2).rotateX(Math.PI / 2), M.steel); d.scale.z = 0.4; d.position.z = 3; t.add(d);
    t.position.set(-X + 2.5 + i * 3.3, Y - 2.2, 3); g.add(t); }
  for (let k = 0; k < 4; k++) g.add(at(cylY(0.15 + 0.05 * k, 2 * Y, [M.steel, M.yellowPaint, M.green, M.galv][k]), X - 0.6 - 0.4 * k, 0, 4 + 0.6 * k));
  g.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  return g;
}
