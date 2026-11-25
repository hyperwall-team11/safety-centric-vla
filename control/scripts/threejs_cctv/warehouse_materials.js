import * as THREE from 'three';
import { M, pbr } from './props.js';

const cache = new Map();
function variant(key, create) {
  if (!cache.has(key)) cache.set(key, create());
  return cache.get(key);
}

function surface(width, height, paint) {
  const canvas = Object.assign(document.createElement('canvas'), {width, height});
  paint(canvas.getContext('2d'));
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 8;
  return map;
}

function carton(index, top) {
  return variant(`carton-${index}-${top}`, () => {
    const map = surface(256, 256, ctx => {
      const colors = ['#ac885a', '#c7a475', '#b59266', '#d0ae79'];
      ctx.fillStyle = colors[index];
      ctx.fillRect(0, 0, 256, 256);
      let state = index + 1;
      for (let i = 0; i < 2600; i++) {
        state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
        ctx.fillStyle = i % 2 ? '#3d291411' : '#fff3d51a';
        ctx.fillRect(state & 255, (state >>> 14) & 255, 1 + (state >>> 30), 1);
      }
      ctx.strokeStyle = '#63432666';
      ctx.lineWidth = 5;
      ctx.strokeRect(2, 2, 252, 252);
      if (top) {
        ctx.fillStyle = '#8c693850';
        ctx.fillRect(105, 0, 46, 256);
        ctx.fillStyle = '#593d263f';
        ctx.fillRect(127, 0, 2, 256);
      } else {
        ctx.fillStyle = '#e1d9bd';
        ctx.fillRect(30 + index * 12, 154, 98, 55);
        ctx.fillStyle = '#35332c';
        ctx.font = 'bold 17px monospace';
        ctx.fillText(`LOT 0${index + 3}`, 37 + index * 12, 175);
        for (let i = 0; i < 20; i++) {
          if (i % 3 !== 1) ctx.fillRect(37 + index * 12 + i * 4, 181, i % 2 + 1, 19);
        }
        ctx.font = 'bold 19px sans-serif';
        ctx.fillText('↑↑', 179, 225);
      }
    });
    return new THREE.MeshStandardMaterial({map, roughness: top ? 0.78 : 0.91});
  });
}

export function warehouseMaterial(name, size) {
  const rack = /^rack_(\d+)_/.exec(name);
  if (rack && name.includes('_sign_')) {
    const number = Number(rack[1]) + 3;
    return variant(`sign-${number}`, () => {
      const map = surface(128, 128, ctx => {
        ctx.fillStyle = '#e2e3db';
        ctx.fillRect(0, 0, 128, 128);
        ctx.fillStyle = '#223038';
        ctx.fillRect(0, 82, 128, 46);
        ctx.font = 'bold 69px sans-serif';
        ctx.fillText(String(number).padStart(2, '0'), 23, 66);
        ctx.fillStyle = '#f0f1eb';
        ctx.font = 'bold 22px sans-serif';
        ctx.fillText('STOCK', 27, 112);
      });
      return new THREE.MeshStandardMaterial({map, roughness: 0.58});
    });
  }
  if ((rack && name.includes('_box_')) || /pallet:.*\/cargo_/.test(name)) {
    const endings = name.match(/\d+/g).map(Number);
    const face = endings.reduce((sum, n) => sum + n, 0) % 4;
    const side = carton(face, false);
    return [side, side, side, side, carton(face, true), side];
  }
  if (rack && name.includes('_shelf_')) return M.orange;
  if (rack && name.includes('_guard_')) return M.yellowPaint;
  if (rack && /_(post|brace|deck)_/.test(name)) return M.galv;
  if (name.startsWith('ceiling_fixture_')) {
    return variant('fixture', () => new THREE.MeshStandardMaterial({
      color: 0xf7f6ee, emissive: 0xfff5de, emissiveIntensity: 1.65, roughness: 0.32,
    }));
  }
  if (/^warehouse_shutter_-?\d/.test(name)) {
    return variant('shutter', () => new THREE.MeshStandardMaterial({
      map: surface(512, 64, ctx => {
        for (let x = 0; x < 512; x += 16) {
          ctx.fillStyle = '#848e90';
          ctx.fillRect(x, 0, 16, 64);
          ctx.fillStyle = '#a4aeae';
          ctx.fillRect(x + 1, 0, 3, 64);
          ctx.fillStyle = '#515d62';
          ctx.fillRect(x + 14, 0, 2, 64);
        }
      }),
      roughness: 0.57,
      metalness: 0.28,
    }));
  }
  if (/^warehouse_.*_wall$/.test(name)) {
    return variant('wall', () => pbr('wall', 3, 1, { color: new THREE.Color(0.79, 0.78, 0.73) }));
  }
  if (name === 'floor') {
    const [sx, sy] = size;
    return pbr('floor', sx / 2, sy / 2, {
      color: new THREE.Color(1.56, 1.81, 2.11), metalness: 0.06, roughness: 0.72,
      normalScale: new THREE.Vector2(0.13, 0.13),
    });
  }
  return null;
}

export function warehouseFloorJoints(sx, sy) {
  const pixelsPerMeter = 64;
  const width = Math.round(2 * sx * pixelsPerMeter);
  const height = Math.round(2 * sy * pixelsPerMeter);
  return surface(width, height, ctx => {
    ctx.strokeStyle = '#a5abb143';
    ctx.lineWidth = 1.25;
    ctx.beginPath();
    for (let x = pixelsPerMeter; x < width; x += 3 * pixelsPerMeter) {
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
    }
    for (let y = pixelsPerMeter; y < height; y += 3 * pixelsPerMeter) {
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
    }
    ctx.stroke();
  });
}
