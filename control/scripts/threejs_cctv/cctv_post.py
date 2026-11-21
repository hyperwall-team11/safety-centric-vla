"""Blender 원본 렌더(raw_*.png)에 CCTV 효과를 입혀 최종 PNG를 만든다.

    python cctv_post.py --dir ../../outputs/threejs_cctv [--cameras cctv_1,cctv_2]

효과(render.html의 CCTV 셰이더와 같은 값): 약한 흐림, 채도 85 %, 약한 푸른 기, 비네팅, 센서 노이즈, JPEG(품질 72) 1회 압축.
"""
import argparse
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

p = argparse.ArgumentParser()
p.add_argument("--dir", type=Path, required=True)
p.add_argument("--cameras", default="cctv_1,cctv_2")
p.add_argument("--keep-raw", action="store_true")
args = p.parse_args()

rng = np.random.default_rng(7)
for cam in args.cameras.split(","):
    raws = sorted((args.dir / cam).glob("raw_*.png"))
    for raw in raws:
        img = Image.open(raw).convert("RGB")
        blur = np.asarray(img.filter(ImageFilter.GaussianBlur(0.6)), np.float32)
        a = np.asarray(img, np.float32) * 0.65 + blur * 0.35
        lum = a @ np.array([0.299, 0.587, 0.114], np.float32)
        a = (lum[..., None] * 0.15 + a * 0.85) * np.array([1.0, 1.0, 1.03], np.float32)
        h, w = lum.shape
        yy, xx = np.mgrid[:h, :w]
        d2 = ((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2)
        a *= (1.0 - 0.7 * d2)[..., None]
        a += rng.normal(0, 4.0, a.shape)
        buf = io.BytesIO()
        Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=72)
        Image.open(buf).save(raw.with_name(raw.name.removeprefix("raw_")))
        if not args.keep_raw:
            raw.unlink()
    print(f"{cam}: {len(raws)}장")
