"""MuJoCo ① 시나리오 장면을 Three.js 렌더러용 데이터로 내보낸다.

    python export_scene.py --out ../../outputs/threejs_cctv --duration 30 --fps 10

출력:
    scene.json   정적 정보 (geom 형상·색, 메시, 작업자 skin 면·UV·텍스처, CCTV 카메라)
    frames.bin   프레임별 float32 [geom 위치(3)·회전행렬(9) × ngeom, skin 정점(3) × nskinvert]
    truth.csv    프레임별 시뮬레이션 시각, 작업자 실제 위치, 금지구역 안 여부, 로봇 위치

작업자 정답 좌표는 평가용이다. CV·MuJoCo ② 안전 판정 입력으로 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import shutil
import sys
from pathlib import Path

import mujoco
import numpy as np

MUJOCO_DIR = Path(__file__).resolve().parents[2] / "mujoco"
sys.path.insert(0, str(MUJOCO_DIR))

from scenario import CAMERAS, WORKER_DIR, WorkerScript, in_zone  # noqa: E402
from simulation import DiffDriveSim  # noqa: E402

# 30초 동안 금지구역 밖·안, 탁자 가림 장면이 모두 나오는 경로 (x, y, 머무는 시간 s)
#   B: cctv_2에서 보면 탁자 A 뒤 (금지구역 밖)
#   C: cctv_1에서 보면 탁자 B 뒤 (금지구역 안, 다리가 가려짐)
TEST_WAYPOINTS = [
    (3.4, 1.8, 1.0),    # A 금지구역 밖
    (0.75, 0.85, 1.5),  # B 탁자 A 뒤
    (1.2, -0.5, 2.5),   # C 금지구역 안, 탁자 B 뒤
    (1.6, 0.2, 1.5),    # D 금지구역 안, 가림 없음
    (3.4, -1.8, 1.0),   # E 금지구역 밖
]
# 알파 채널이 없는 속눈썹·눈썹 skin은 렌더하지 않는다 (CCTV 거리에서 보이지 않음)
SKIP_SKINS = {"worker_eyebrow001", "worker_eyelashes01"}


def b64(arr, dtype) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype=dtype).tobytes()).decode()


def geom_color(m, g):
    mat = m.geom_matid[g]
    if mat >= 0:
        return m.mat_rgba[mat].tolist(), int(m.mat_texid[mat][1]), m.mat_texrepeat[mat].tolist()
    return m.geom_rgba[g].tolist(), -1, [1, 1]


def export_static(m, d, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    geoms, meshes = [], {}
    for g in range(m.ngeom):
        rgba, tex, rep = geom_color(m, g)
        if rgba[3] == 0:
            continue
        item = {"id": g, "name": m.geom(g).name, "type": int(m.geom_type[g]),
                "size": m.geom_size[g].tolist(), "rgba": rgba, "tex": tex, "texrepeat": rep}
        if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            mid = int(m.geom_dataid[g])
            item["mesh"] = mid
            if mid not in meshes:
                va, vn = m.mesh_vertadr[mid], m.mesh_vertnum[mid]
                fa, fn = m.mesh_faceadr[mid], m.mesh_facenum[mid]
                meshes[mid] = {"vert": b64(m.mesh_vert[va:va + vn], "<f4"),
                               "face": b64(m.mesh_face[fa:fa + fn], "<u4")}
        geoms.append(item)

    textures = {}
    for g in geoms:
        t = g["tex"]
        if t >= 0 and t not in textures and not m.tex(t).name.startswith("worker_"):
            from PIL import Image
            w, h, c = m.tex_width[t], m.tex_height[t], m.tex_nchannel[t]
            a = m.tex_adr[t]
            img = m.tex_data[a:a + w * h * c].reshape(h, w, c)
            name = f"tex_{m.tex(t).name}.png"
            Image.fromarray(img).save(out / name)
            textures[t] = name

    skins = []
    for s in range(m.nskin):
        name = m.skin(s).name or m.mat(m.skin_matid[s]).name
        mat = m.skin_matid[s]
        mat_name = m.mat(mat).name
        va, vn = m.skin_vertadr[s], m.skin_vertnum[s]
        fa, fn = m.skin_faceadr[s], m.skin_facenum[s]
        tex_file = WORKER_DIR / f"{mat_name.removeprefix('worker_')}.png"
        skins.append({
            "name": mat_name, "render": mat_name not in SKIP_SKINS,
            "vertadr": int(va), "vertnum": int(vn),
            "face": b64(m.skin_face[fa:fa + fn], "<u4"),
            "uv": b64(m.skin_texcoord[m.skin_texcoordadr[s]:m.skin_texcoordadr[s] + vn], "<f4")
            if m.skin_texcoordadr[s] >= 0 else None,
            "texture": tex_file.name,
        })
        shutil.copy(tex_file, out / tex_file.name)

    cams = {}
    for sensor in ("cctv_1", "cctv_2"):
        cid = m.camera(CAMERAS[sensor][0]).id
        cams[sensor] = {"pos": d.cam_xpos[cid].tolist(), "mat": d.cam_xmat[cid].tolist(),
                        "fovy": float(m.cam_fovy[cid]), "width": 640, "height": 480,
                        "frame_id": f"camera_{sensor}"}

    lights = [{"pos": m.light_pos[i].tolist(), "dir": m.light_dir[i].tolist(),
               "diffuse": m.light_diffuse[i].tolist()} for i in range(m.nlight)]
    zone = m.geom("keep_out_zone")
    return {"geoms": geoms, "meshes": {str(k): v for k, v in meshes.items()},
            "textures": {str(k): v for k, v in textures.items()}, "skins": skins,
            "nskinvert": int(m.nskinvert), "cameras": cams, "lights": lights,
            "keep_out_zone": {"pos": zone.pos.tolist(), "size": zone.size.tolist()}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--fps", type=float, default=10.0)
    args = p.parse_args()

    sim = DiffDriveSim("scenario")
    m, d = sim.model, sim.data
    worker = WorkerScript(waypoints=TEST_WAYPOINTS)
    worker.apply(m, d)
    mujoco.mj_forward(m, d)

    meta = export_static(m, d, args.out)
    gids = [g["id"] for g in meta["geoms"]]
    opt, cam = mujoco.MjvOption(), mujoco.MjvCamera()
    scn = mujoco.MjvScene(m, maxgeom=2000)

    n = int(round(args.duration * args.fps))
    dt = 1.0 / args.fps
    with open(args.out / "frames.bin", "wb") as fb, open(args.out / "truth.csv", "w", newline="") as fc:
        w = csv.writer(fc)
        w.writerow(["frame", "sim_time", "worker_x", "worker_y", "worker_yaw", "in_keep_out",
                    "robot_x", "robot_y"])
        # 로봇은 처음 1.2초 동안 제자리 회전한 뒤 멈춘다 (scenario.snapshot과 같은 동작)
        sim.set_velocity(0.0, 0.4)
        for f in range(n):
            target = f * dt
            while sim.time < target - 1e-9:
                worker.apply(m, d)
                sim.step(min(0.02, target - sim.time))
                if sim.time > 1.2:
                    sim.stop()
            x, y, yaw = worker.apply(m, d)
            mujoco.mj_forward(m, d)
            mujoco.mjv_updateScene(m, d, opt, None, cam, mujoco.mjtCatBit.mjCAT_ALL, scn)
            pose = np.concatenate([d.geom_xpos[gids], d.geom_xmat[gids]], axis=1)
            fb.write(pose.astype("<f4").tobytes())
            fb.write(np.asarray(scn.skinvert, dtype="<f4").tobytes())
            rx, ry, _ = sim.pose()
            w.writerow([f, f"{sim.time:.3f}", f"{x:.4f}", f"{y:.4f}", f"{yaw:.4f}",
                        int(in_zone(m, "keep_out_zone", x, y)), f"{rx:.4f}", f"{ry:.4f}"])

    meta.update({"frames": n, "fps": args.fps, "ngeom_export": len(gids)})
    (args.out / "scene.json").write_text(json.dumps(meta))
    print(f"{n} 프레임 → {args.out}")


if __name__ == "__main__":
    main()
