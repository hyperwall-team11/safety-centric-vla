"""Three.js CCTV 렌더용 CC0 에셋(Poly Haven)을 assets/ 에 받는다. assets/ 는 커밋하지 않는다.

    python fetch_assets.py

라이선스: Poly Haven 에셋은 모두 CC0 (https://polyhaven.com/license).
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
API = "https://api.polyhaven.com/files/"
UA = {"User-Agent": "scv-threejs-cctv/1.0"}

# (용도, 에셋 이름, 해상도)
TEXTURES = [
    ("floor", "hangar_concrete_floor", "2k"),
    ("wall", "corrugated_iron_02", "1k"),
    ("metal", "metal_plate", "1k"),
    ("building", "concrete_floor_02", "1k"),
    ("rust", "rusty_painted_metal", "1k"),
]
HDRI = ("factory_yard", "2k")
MODELS = [
    "barrel_03", "wooden_crate_02", "concrete_road_barrier", "korean_fire_extinguisher_01",
    "tool_cart", "power_box_01", "plastic_crate_01", "metal_trash_can", "propane_tank",
    "portable_welding_cart", "worn_metal_rack", "cardboard_box_01",
]


def get(url: str, dest: Path):
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
        dest.write_bytes(r.read())
    print(f"받음: {dest.relative_to(ASSETS)}")


def files(name: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(API + name, headers=UA)) as r:
        return json.load(r)


def main():
    manifest = {"textures": {}, "hdri": None, "models": {}}
    for role, name, res in TEXTURES:
        f = files(name)
        maps = {}
        for key, out in (("Diffuse", "diff"), ("nor_gl", "nor"), ("arm", "arm")):
            url = f[key][res]["jpg"]["url"]
            dest = ASSETS / "textures" / name / f"{out}.jpg"
            get(url, dest)
            maps[out] = str(dest.relative_to(ASSETS))
        manifest["textures"][role] = maps
    name, res = HDRI
    url = files(name)["hdri"][res]["hdr"]["url"]
    get(url, ASSETS / "hdri" / f"{name}.hdr")
    manifest["hdri"] = f"hdri/{name}.hdr"
    for name in MODELS:
        g = files(name)["gltf"]["1k"]["gltf"]
        base = ASSETS / "models" / name
        get(g["url"], base / f"{name}.gltf")
        for rel, inc in g.get("include", {}).items():
            get(inc["url"], base / rel)
        manifest["models"][name] = f"models/{name}/{name}.gltf"
    (ASSETS / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"완료: {ASSETS}")


if __name__ == "__main__":
    main()
