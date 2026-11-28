"""MuJoCo ① 공장 장면을 Three.js 렌더러용 데이터로 내보낸다.

    python export_scene.py --out ../../outputs/threejs_cctv/data --duration 30 --fps 10 [--plant full|arms|bare]

기본(--plant full)은 plant_scene.xml에 로봇 팔 4대·랙·팔레트·포장대(arms.PLANT_ARMS)와 이동형 운반 로봇 2대·
거치대(amr.PLANT_AMRS)를 더한 장면이다. 팔과 AMR은 plant_demo.PlantMotion이 정해진 순서로 움직인다.
--plant bare는 예전처럼 --scene 파일만 쓴다.

출력:
    scene.json   정적 정보 (geom 형상·색, 메시, 작업자 skin 면·UV·텍스처·복장, CCTV 카메라, 구역)
    frames.bin   프레임별 float32
                 [geom 위치(3)·회전행렬(9) × ngeom] + 작업자마다 [skin 정점(3) × nskinvert, 머리 위치(3)·쿼터니언(4)]
    truth.csv    프레임·작업자별 시뮬레이션 시각, 실제 위치, 금지구역 안 여부, 로봇과의 거리

작업자 1은 MuJoCo skin(mjv_updateScene)을 그대로 쓰고, 작업자 2·3은 같은 skin 데이터로
MuJoCo와 같은 선형 블렌드 스키닝을 계산한다(작업자 1로 일치 여부를 검증).
작업자 정답 좌표는 평가용이다. CV·MuJoCo ② 안전 판정 입력으로 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import sys
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

MUJOCO_DIR = Path(__file__).resolve().parents[2] / "mujoco"
sys.path.insert(0, str(MUJOCO_DIR))

from scenario import CAMERAS, WORKER_DIR, WORKER_PARTS, WorkerPoser, WorkerScript, in_zone  # noqa: E402
from simulation import DiffDriveSim  # noqa: E402

# 작업자별 경로 (x, y, 머무는 시간 s), 속도 m/s, 복장
WORKERS = [
    {   # 작업자 1: 금지구역 밖·안, 탁자 가림 장면
        "waypoints": [
            (3.4, 1.8, 1.0),    # 금지구역 밖
            (0.75, 0.85, 1.5),  # cctv_2에서 탁자 A·로봇 뒤
            (1.2, -0.5, 2.5),   # 금지구역 안, cctv_1에서 탁자 B에 다리가 가려짐
            (1.6, 0.2, 1.5),    # 금지구역 안, 가림 없음
            (3.4, -1.8, 1.0),   # 금지구역 밖
            (2.4, -3.6, 1.0),   # 맨홀·지게차 옆
        ],
        "speed": 0.6, "vest": "lime", "pants": "navy", "helmet": "white",
    },
    {   # 작업자 2: 로봇 셀 조작반 앞에서 작업, 잠깐 이동
        "waypoints": [(-1.15, 1.55, 9.0), (0.4, 2.7, 3.0)],
        "speed": 0.5, "vest": "orange", "pants": "jeans", "helmet": "yellow",
    },
    {   # 작업자 3: 지게차 옆 통로를 따라 이동
        "waypoints": [(6.6, 0.0, 2.0), (-0.8, -2.3, 1.5), (5.5, -1.0, 0.0)],
        "speed": 0.7, "vest": "lime", "pants": "jeans", "helmet": "orange",
    },
]
# 물류창고 교차로(warehouse_scene.xml) 작업자 경로. 랙 사이 보조 통로는 CCTV 2대 모두에서 가려진다.
WAREHOUSE_WORKERS = [
    {   # 작업자 1: 교차로 옆 보조 통로(관측 공백)에서 십자 교차로로 돌발 진입
        "waypoints": [
            (6.0, -3.25, 3.0),   # 남동 보조 통로 안 (두 대 모두 가려짐)
            (1.0, -3.25, 0.0),   # 보조 통로 입구 → y통로
            (0.7, -0.5, 2.0),    # 십자 교차로 진입 (두 대 모두 보임)
            (4.5, -0.25, 2.0),   # x통로에서 멈춰 선 로봇 정면까지 접근 (head_camera 근접 장면, 로봇 중심과 약 0.5 m)
            (1.0, -0.9, 0.0),    # y통로로 되돌아와
            (1.0, -3.25, 0.0),   # 보조 통로 입구 (반복 시 랙을 가로지르지 않게)
        ],
        "speed": 0.9, "vest": "lime", "pants": "navy", "helmet": "white",
    },
    {   # 작업자 2: T자 교차로에서 북동 보조 통로로 들어가 머문 뒤 다시 나온다 (관측 공백 → 재등장)
        "waypoints": [(10.0, -0.6, 1.0), (10.0, 3.25, 0.0), (7.0, 3.25, 3.0), (10.0, 3.25, 0.0)],
        "speed": 0.8, "vest": "orange", "pants": "jeans", "helmet": "yellow",
    },
    {   # 작업자 3: 서쪽 x통로 → 교차로 → y통로 북쪽 순찰
        "waypoints": [(-10.0, 0.6, 1.0), (-1.0, 0.6, 1.0), (-1.0, 7.0, 2.0), (-1.0, 0.9, 0.0)],
        "speed": 0.8, "vest": "lime", "pants": "jeans", "helmet": "orange",
    },
]
# 장면별 작업자 경로 (없는 장면은 WORKERS)
SCENE_WORKERS = {"warehouse_scene.xml": WAREHOUSE_WORKERS}
# 장면별 로봇 주행: start초에 출발해 speed(m/s)로 직진, x가 stop_x 이하가 되면 정지.
# head_tilt(rad, 음수가 아래)로 머리 카메라(head_camera)를 통로 바닥 쪽으로 숙인다. 없는 장면은 제자리 회전만.
# 주행은 영상 데이터용 고정 시나리오이며 안전 판정(ESTOP 등)을 반영하지 않는다.
SCENE_ROBOT = {"warehouse_scene.xml": {"start": 1.0, "speed": 0.5, "stop_x": 5.0, "head_tilt": -0.35}}
ROBOT_CAMERA = "head_camera"


def drive_robot(sim, motion):
    x, _, _ = sim.pose()
    if sim.time < motion["start"] or x <= motion["stop_x"]:
        sim.stop()
    else:
        sim.set_velocity(motion["speed"], 0.0)
VEST = {"lime": (196, 236, 40), "orange": (255, 120, 20)}
# 알파 채널이 없는 속눈썹·눈썹 skin은 렌더하지 않는다 (CCTV 거리에서 보이지 않음)
SKIP_SKINS = {"worker_eyebrow001", "worker_eyelashes01"}


def b64(arr, dtype) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype=dtype).tobytes()).decode()


def geom_color(m, g):
    mat = m.geom_matid[g]
    if mat >= 0:
        return m.mat_rgba[mat].tolist(), int(m.mat_texid[mat][1]), m.mat_texrepeat[mat].tolist()
    return m.geom_rgba[g].tolist(), -1, [1, 1]


def outfit_texture(out: Path, vest: str, pants: str) -> str:
    """MakeHuman 셔츠·청바지 텍스처를 안전조끼(형광 + 반사띠)와 작업복 바지로 바꾼다.

    텍스처 위쪽(행 < 410, 열 < 735)이 몸통 앞·뒤 판, 오른쪽 위가 소매, 아래쪽 절반이 바지다.
    """
    name = f"suit_{vest}_{pants}.png"
    if (out / name).exists():
        return name
    img = np.asarray(Image.open(WORKER_DIR / "male_casualsuit04.png").convert("RGB")).astype(np.float32)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    cloth = (b - r) > 75
    rows, cols = np.mgrid[:img.shape[0], :img.shape[1]]
    lum = 0.3 * r + 0.59 * g + 0.11 * b
    torso = cloth & (rows < 410) & (cols < 735)
    shade = np.clip(lum / np.median(lum[torso]), 0.6, 1.3)[..., None]
    img[torso] = (np.array(VEST[vest]) * shade[torso])
    stripes = torso & (((rows > 250) & (rows < 278)) | ((rows > 318) & (rows < 346)))
    img[stripes] = np.array([205, 208, 210]) * shade[stripes]
    sleeves = cloth & (rows < 410) & (cols >= 735)
    img[sleeves] = np.array([45, 50, 58]) * shade[sleeves]
    if pants == "navy":
        legs = cloth & (rows >= 410)
        legshade = np.clip(lum / np.median(lum[legs]), 0.6, 1.4)[..., None]
        img[legs] = np.array([38, 44, 66]) * legshade[legs]
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(out / name)
    return name


class Skinner:
    """MuJoCo mjv_updateSkin과 같은 선형 블렌드 스키닝 (inflate는 1 mm라 무시)."""

    def __init__(self, m):
        part_of = {f"worker_{p}": i for i, p in enumerate(WORKER_PARTS)}
        self.bones = []
        for s in range(m.nskin):
            va = m.skin_vertadr[s]
            for b in range(m.skin_boneadr[s], m.skin_boneadr[s] + m.skin_bonenum[s]):
                body = m.body(m.skin_bonebodyid[b]).name
                ia, n = m.skin_bonevertadr[b], m.skin_bonevertnum[b]
                ids = m.skin_bonevertid[ia:ia + n] + va
                w = m.skin_bonevertweight[ia:ia + n]
                inv = np.zeros(4)
                mujoco.mju_negQuat(inv, m.skin_bonebindquat[b])
                rest = np.zeros((n, 3))
                for k, vid in enumerate(ids):
                    tmp = np.zeros(3)
                    mujoco.mju_rotVecQuat(tmp, m.skin_vert[vid] - m.skin_bonebindpos[b], inv)
                    rest[k] = tmp
                self.bones.append((part_of[body], ids, w[:, None], rest))
        self.n = m.nskinvert

    def __call__(self, pos, quat):
        out = np.zeros((self.n, 3))
        mats = np.zeros((len(pos), 9))
        for i, q in enumerate(quat):
            mujoco.mju_quat2Mat(mats[i], q)
        mats = mats.reshape(-1, 3, 3)
        for part, ids, w, rest in self.bones:
            np.add.at(out, ids, w * (rest @ mats[part].T + pos[part]))
        return out


def export_static(m, d, out: Path, workers_cfg=WORKERS, robot_camera=False, live_moving_cameras=()):
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

    skins = []
    for s in range(m.nskin):
        mat_name = m.mat(m.skin_matid[s]).name
        va, vn = m.skin_vertadr[s], m.skin_vertnum[s]
        fa, fn = m.skin_faceadr[s], m.skin_facenum[s]
        tex_file = WORKER_DIR / f"{mat_name.removeprefix('worker_')}.png"
        if mat_name not in SKIP_SKINS:
            Image.open(tex_file).save(out / tex_file.name)
        skins.append({
            "name": mat_name, "render": mat_name not in SKIP_SKINS,
            "vertadr": int(va), "vertnum": int(vn),
            "face": b64(m.skin_face[fa:fa + fn], "<u4"),
            "uv": b64(m.skin_texcoord[m.skin_texcoordadr[s]:m.skin_texcoordadr[s] + vn], "<f4"),
            "texture": tex_file.name,
        })

    cams = {}
    fixed = tuple(sensor for sensor in ("cctv_1", "cctv_2", "cctv_3", "cctv_4", "cctv_5")
                  if mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_CAMERA, sensor) >= 0)
    for sensor in fixed + ((ROBOT_CAMERA,) if robot_camera else ()) + tuple(live_moving_cameras):
        cid = m.camera(CAMERAS[sensor][0]).id
        cams[sensor] = {"moving": sensor == ROBOT_CAMERA or sensor in live_moving_cameras,"pos": d.cam_xpos[cid].tolist(), "mat": d.cam_xmat[cid].tolist(),
                        "fovy": float(m.cam_fovy[cid]), "width": 640, "height": 480,
                        "frame_id": f"camera_{sensor}"}

    rects = {}
    for key, name in (("keep_out", "keep_out_zone"), ("a", "zone_a_marking"), ("b", "zone_b_marking")):
        try:
            g = m.geom(name)
        except KeyError:
            continue
        rects[key] = [float(d.geom_xpos[g.id][0]), float(d.geom_xpos[g.id][1]), float(g.size[0]), float(g.size[1])]

    workers = [{"id": f"worker-{i + 1:02d}", "suit": outfit_texture(out, w["vest"], w["pants"]),
                "helmet": w["helmet"]} for i, w in enumerate(workers_cfg)]
    return {"geoms": geoms, "meshes": {str(k): v for k, v in meshes.items()}, "skins": skins,
            "nskinvert": int(m.nskinvert), "cameras": cams, "zone_rects": rects, "workers": workers}


class FramePacker:
    """MuJoCo 상태 → render.html 한 프레임(float32 바이트). 오프라인 내보내기와 브리지 실시간 렌더가 같이 쓴다.

    프레임 구성: [geom 위치(3)·회전행렬(9) × ngeom] + 작업자마다 [skin 정점(3) × nskinvert, 머리 위치(3)·쿼터니언(4)].
    작업자 1은 MuJoCo skin을 그대로 쓰고, 추가 작업자(`worker_2`, `worker_3` mocap)는 같은 skin 데이터로 스키닝한다.
    """

    def __init__(self, m, d, meta, extra_scripts):
        """meta: export_static 결과(workers는 작업자 1 + 추가 작업자 순서로 맞춘 것). extra_scripts: [(i, WorkerScript)]."""
        self.extra = extra_scripts
        self.camera_ids = [m.camera(CAMERAS[s][0]).id for s in meta.get("live_moving_cameras", ())]
        self.gids = [g["id"] for g in meta["geoms"]]
        self.opt, self.cam = mujoco.MjvOption(), mujoco.MjvCamera()
        self.scn = mujoco.MjvScene(m, maxgeom=2000)
        self.skinner = Skinner(m)
        self.poser = WorkerPoser()
        self.head = m.body("worker_head").id
        part_ids = [m.body(f"worker_{b}").id for b in WORKER_PARTS]
        self.head_part = WORKER_PARTS.index("head")

        # 안전모 위치: 정수리 정점을 머리 바디 좌표로 옮긴 값
        mujoco.mjv_updateScene(m, d, self.opt, None, self.cam, mujoco.mjtCatBit.mjCAT_ALL, self.scn)
        body_skin = next(s for s in meta["skins"] if s["name"] == "worker_body")
        verts = np.asarray(self.scn.skinvert).reshape(-1, 3)
        vb = verts[body_skin["vertadr"]:body_skin["vertadr"] + body_skin["vertnum"]]
        top = vb[np.argmax(vb[:, 2])]
        rot = d.xmat[self.head].reshape(3, 3)
        meta["helmet_offset"] = (rot.T @ (top - d.xpos[self.head])).tolist()
        # 스키닝 검증: 작업자 1의 MuJoCo 결과와 비교
        self.skin_error = float(np.abs(self.skinner(d.xpos[part_ids], d.xquat[part_ids]) - verts).max())
        assert self.skin_error < 0.005, "스키닝 결과가 MuJoCo와 다릅니다"

    def frame_length(self, m) -> int:
        return len(self.gids) * 12 + (1 + len(self.extra)) * (m.nskinvert * 3 + 7) + len(self.camera_ids) * 12

    def pack(self, m, d, sim_time):
        """추가 작업자 mocap을 sim_time 위치로 옮기고 한 프레임을 만든다. (bytes, 추가 작업자 [(x, y, yaw)])"""
        states = []
        for i, script in self.extra:
            x, y, yaw = script.position(sim_time)
            d.mocap_pos[m.body_mocapid[m.body(f"worker_{i + 1}").id]] = [x, y, 0.0]
            states.append((x, y, yaw))
        mujoco.mj_forward(m, d)
        mujoco.mjv_updateScene(m, d, self.opt, None, self.cam, mujoco.mjtCatBit.mjCAT_ALL, self.scn)
        parts = [np.concatenate([d.geom_xpos[self.gids], d.geom_xmat[self.gids]], axis=1).astype("<f4").tobytes(),
                 np.asarray(self.scn.skinvert, dtype="<f4").tobytes(),
                 np.concatenate([d.xpos[self.head], d.xquat[self.head]]).astype("<f4").tobytes()]
        for (_, script), position in zip(self.extra, states):
            pos, quat = script.body_pose(self.poser, sim_time, position)
            pos, quat = pos.copy(), quat.copy()
            parts.append(self.skinner(pos, quat).astype("<f4").tobytes())
            parts.append(np.concatenate([pos[self.head_part], quat[self.head_part]]).astype("<f4").tobytes())
        if self.camera_ids:
            parts.append(np.concatenate([d.cam_xpos[self.camera_ids], d.cam_xmat[self.camera_ids]], axis=1)
                         .astype("<f4").tobytes())
        return b"".join(parts), states


def prepare(m, d, out: Path, worker1_script=None, workers_cfg=WORKERS, robot_camera=False, live_moving_cameras=(),
            worker_scripts=None):
    """정적 장면(scene.json 내용)과 FramePacker를 만든다. 작업자 1은 worker1_script(없으면 workers_cfg[0])로 둔다.
    workers_cfg: 현장별 작업자 설정(SCENE_WORKERS), robot_camera: 로봇 탑재 카메라를 장면에 포함할지.
    worker1_script=False이면 작업자 1을 지금 자리에 둔다(브리지에서 작업자 이동을 끈 경우).

    반환한 meta에 frames·fps·ngeom_export를 채워 scene.json으로 쓴다.
    """
    names = {m.body(i).name for i in range(m.nbody)}
    extra = [i for i in range(1, len(workers_cfg)) if f"worker_{i + 1}" in names]
    worker_scripts = worker_scripts or {}
    scripts = [worker_scripts.get(i) or WorkerScript(waypoints=workers_cfg[i]["waypoints"],
                                                    speed=workers_cfg[i]["speed"]) for i in extra]
    if worker1_script is not False:
        (worker1_script or WorkerScript(waypoints=workers_cfg[0]["waypoints"],
                                        speed=workers_cfg[0]["speed"])).apply(m, d)
    mujoco.mj_forward(m, d)
    meta = export_static(m, d, out, workers_cfg, robot_camera=robot_camera,
                         live_moving_cameras=live_moving_cameras)
    if live_moving_cameras:
        meta["live_moving_cameras"] = list(live_moving_cameras)
    meta["workers"] = [meta["workers"][0]] + [meta["workers"][i] for i in extra]
    packer = FramePacker(m, d, meta, list(zip(extra, scripts)))
    meta["ngeom_export"] = len(packer.gids)
    return meta, packer


def build_sim(plant="full", scene="plant_scene.xml"):
    """(DiffDriveSim, PlantMotion 또는 None). plant: full(팔 + AMR) | arms | bare."""
    if plant == "bare":
        return DiffDriveSim("scenario", model_path=MUJOCO_DIR / "models" / scene), None
    import amr
    from plant_demo import PlantMotion
    if plant == "full":
        model, data, kins, plant_amrs = amr.build_plant_with_amrs()
    else:
        import arms
        model, data, kins = arms.build_plant_with_arms()
        plant_amrs = None
    sim = DiffDriveSim("scenario", model=model, data=data)
    return sim, PlantMotion(model, data, kins, plant_amrs)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--scene", default="plant_scene.xml",
                   help="--plant bare일 때 mujoco/models 안의 장면 파일 (prop_* geom은 Three.js가 설비 모델로 덮어 그린다)")
    p.add_argument("--plant", choices=("full", "arms", "bare"), default=None,
                   help="full(기본): 팔 4대 + AMR 2대, arms: 팔 4대만, bare: --scene 파일 그대로 "
                        "(--scene을 바꾸고 --plant를 생략하면 bare)")
    args = p.parse_args()
    if args.plant is None:
        args.plant = "full" if args.scene == "plant_scene.xml" else "bare"
    elif args.plant != "bare" and args.scene != "plant_scene.xml":
        p.error("--scene은 --plant bare와 함께 써야 합니다 (full·arms는 공장 장면을 직접 만든다)")

    # motion: 팔·AMR 동작(PlantMotion, --plant full·arms). robot_motion: 이동 카메라 로봇 경로(물류창고 장면)
    sim, motion = build_sim(args.plant, args.scene)
    m, d = sim.model, sim.data
    workers_cfg = SCENE_WORKERS.get(args.scene, WORKERS)
    worker1 = WorkerScript(waypoints=workers_cfg[0]["waypoints"], speed=workers_cfg[0]["speed"])
    robot_motion = SCENE_ROBOT.get(args.scene)
    if robot_motion:
        d.ctrl[m.actuator("head_2_joint_position").id] = robot_motion["head_tilt"]
        d.qpos[m.jnt_qposadr[m.joint("head_2_joint").id]] = robot_motion["head_tilt"]
    meta, packer = prepare(m, d, args.out, worker1, workers_cfg=workers_cfg, robot_camera=bool(robot_motion))
    print(f"스키닝 최대 오차 {packer.skin_error * 1000:.2f} mm")
    # 이동 카메라는 프레임마다 위치·회전(cam→world, MuJoCo 카메라 축)과 로봇 자기 위치(오도메트리 x, y, yaw)를 남긴다
    track = []
    robot_cam = m.camera(CAMERAS[ROBOT_CAMERA][0]).id if robot_motion else None

    n = int(round(args.duration * args.fps))
    dt = 1.0 / args.fps
    with open(args.out / "frames.bin", "wb") as fb, open(args.out / "truth.csv", "w", newline="") as fc:
        w = csv.writer(fc)
        w.writerow(["frame", "sim_time", "worker_id", "x", "y", "yaw", "in_keep_out",
                    "dist_to_robot", "robot_x", "robot_y"])
        if not robot_motion:
            sim.set_velocity(0.0, 0.4)  # 로봇은 처음 1.2초 제자리 회전 후 정지
        for f in range(n):
            target = f * dt
            while sim.time < target - 1e-9:
                worker1.apply(m, d)
                if robot_motion:
                    drive_robot(sim, robot_motion)
                before = sim.time
                sim.step(min(0.02, target - sim.time))
                if motion:
                    motion.step(d, sim.time, sim.time - before)
                if not robot_motion and sim.time > 1.2:
                    sim.stop()
            states = [worker1.apply(m, d)]
            frame, extra_states = packer.pack(m, d, sim.time)  # 추가 작업자를 옮기고 mj_forward까지 한다
            states += extra_states
            if robot_motion:
                track.append({"pos": d.cam_xpos[robot_cam].tolist(), "mat": d.cam_xmat[robot_cam].tolist(),
                              "base": list(sim.pose())})
            fb.write(frame)
            rx, ry, _ = sim.pose()
            for k, (x, y, yaw) in enumerate(states):
                w.writerow([f, f"{sim.time:.3f}", meta["workers"][k]["id"], f"{x:.4f}", f"{y:.4f}", f"{yaw:.4f}",
                            int(in_zone(m, "keep_out_zone", x, y)), f"{math.hypot(x - rx, y - ry):.3f}",
                            f"{rx:.4f}", f"{ry:.4f}"])

    meta.update({"frames": n, "fps": args.fps, "scene": args.scene})
    if robot_motion:
        meta["camera_tracks"] = {ROBOT_CAMERA: track}
    (args.out / "scene.json").write_text(json.dumps(meta))
    print(f"{n} 프레임, 작업자 {len(meta['workers'])}명 → {args.out}")


if __name__ == "__main__":
    main()
