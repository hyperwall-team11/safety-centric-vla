"""2단계: MPFB 캐릭터(피부·옷·머리·눈)를 MuJoCo <skin> 으로 내보낸다 → mujoco/models/worker/

  cd control && blender -b outputs/worker/human.blend --python scripts/worker_skin/export_mujoco_skin.py

과정
  1. 캐릭터를 걷기 모델(models/worker/kinematics.xml)의 기준자세(관절값 0: 팔다리 수직)로 맞춘다.
  2. 각 메시를 평가(armature·mask·subsurf 적용)하고 좌표를 MuJoCo 좌표계·크기로 변환한다.
  3. 뼈 가중치를 부위 body 가중치로 합친다 (손가락→팔뚝, 쇄골·목→가슴 등).
  4. body 이름에 worker_ 접두어를 붙인 .skn(바이너리)과 텍스처 PNG 를 쓴다.
     장면(scenario_scene.xml)의 worker_* mocap 바디와 <skin> 선언은 이미 들어 있다.
"""
import re
import struct
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]  # control/
DATA = Path.home() / "Library/Application Support/Blender/5.2/extensions/.user/blender_org/mpfb/data"
OUT = ROOT / "mujoco" / "models" / "worker"
TEX_MAX = 1024
PREFIX = "worker_"

R = Matrix(((0, 1, 0), (-1, 0, 0), (0, 0, 1)))  # MuJoCo → Blender
DOWN = Vector((0, 0, -1))
ALIGN = {"upperarm", "lowerarm", "thigh", "calf"}
FINGERS = {"index", "middle", "ring", "pinky", "thumb"}

# MuJoCo 기준자세에서 각 body 의 world 위치 (회전은 모두 단위)
MJ_BODY_POS = {
    "pelvis": (0, 0, 0.945), "lower_torso": (0, 0, 1.025), "chest": (0, 0, 1.245), "head": (0, 0, 1.525),
    "right_upper_arm": (0, -0.21, 1.445), "right_forearm": (0, -0.21, 1.155),
    "left_upper_arm": (0, 0.21, 1.445), "left_forearm": (0, 0.21, 1.155),
    "right_thigh": (0, -0.09, 0.895), "right_shin": (0, -0.09, 0.475), "right_foot": (0, -0.09, 0.045),
    "left_thigh": (0, 0.09, 0.895), "left_shin": (0, 0.09, 0.475), "left_foot": (0, 0.09, 0.045),
}

# 텍스처를 찾을 수 없는 메시에 직접 지정
TEX_OVERRIDE = {
    "Human": DATA / "skins/young_asian_male/young_lightskinned_male_diffuse3.png",
    "Human.high-poly": DATA / "eyes/materials/brown_eye.png",
}


def bone_to_body(bone):
    side = {"l": "left_", "r": "right_"}.get(bone.rsplit("_", 1)[-1], "")
    base = bone.split("_")[0]
    table = {"Root": "pelvis", "pelvis": "pelvis", "spine": "lower_torso", "neck": "chest", "clavicle": "chest",
             "head": "head", "upperarm": "upper_arm", "lowerarm": "forearm", "hand": "forearm",
             "thigh": "thigh", "calf": "shin", "foot": "foot", "ball": "foot"}
    if bone in ("spine_02", "spine_03"):
        return "chest"
    if base in FINGERS:
        return side + "forearm"
    b = table.get(base)
    if b is None:
        return None
    return side + b if b in ("upper_arm", "forearm", "thigh", "shin", "foot") else b


def pose_zero(rig):
    """MuJoCo 기준자세(팔다리 수직, 손가락 살짝 굽힘)."""
    bones = rig.data.bones
    rest = {b.name: b.matrix_local.to_3x3() for b in bones}
    W = {}
    for b in bones:
        bn, p = b.name, (b.parent.name if b.parent else None)
        base = bn.split("_")[0]
        rel = rest[bn] if p is None else rest[p].inverted() @ rest[bn]
        if base in ALIGN:
            W[bn] = rest[bn].col[1].normalized().rotation_difference(DOWN).to_matrix() @ rest[bn]
            basis = rel.inverted() @ W[p].inverted() @ W[bn]
        elif base in FINGERS:
            seg = int(bn.split("_")[1])
            basis = Matrix.Rotation(0.12 if base == "thumb" else 0.25 + 0.1 * seg, 3, "X")
            W[bn] = W[p] @ rel @ basis
        else:
            basis = Matrix.Identity(3)
            W[bn] = rest[bn] if p is None else W[p] @ rel
        pb = rig.pose.bones[bn]
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = basis.to_quaternion()
    rig.location = (0, 0, 0)
    bpy.context.view_layer.update()


def find_texture(obj):
    if obj.name in TEX_OVERRIDE:
        return bpy.data.images.load(str(TEX_OVERRIDE[obj.name]), check_existing=True)
    for m in obj.data.materials:
        if m and m.use_nodes:
            for n in m.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image and "diffuse" in n.name.lower():
                    return n.image
    return None


def save_texture(img, name):
    img = img.copy()
    w, h = img.size
    if max(w, h) > TEX_MAX:
        img.scale(TEX_MAX * w // max(w, h), TEX_MAX * h // max(w, h))
    path = OUT / f"{name}.png"
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    return path


def export_mesh(obj, rig, scale, offset):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    mesh = ev.to_mesh()
    mesh.calc_loop_triangles()
    uv = mesh.uv_layers.active.data if mesh.uv_layers.active else None
    vg_names = {g.index: g.name for g in obj.vertex_groups}

    # (정점, uv) 쌍마다 새 정점 — MuJoCo skin 은 정점당 uv 하나
    key2new, verts, uvs, src = {}, [], [], []
    faces = []
    mw = obj.matrix_world
    RT = R.transposed()
    for tri in mesh.loop_triangles:
        f = []
        for li, vi in zip(tri.loops, tri.vertices):
            u = tuple(round(c, 5) for c in uv[li].uv) if uv else (0.0, 0.0)
            k = (vi, u)
            if k not in key2new:
                key2new[k] = len(verts)
                p = RT @ (mw @ mesh.vertices[vi].co) / scale + offset
                verts.append(tuple(p))
                uvs.append((u[0], 1.0 - u[1]))  # MuJoCo 텍스처는 v 가 아래로
                src.append(vi)
            f.append(key2new[k])
        faces.append(f)

    # body 별 가중치
    weights = {}
    for new_i, vi in enumerate(src):
        acc = {}
        for g in mesh.vertices[vi].groups:
            body = bone_to_body(vg_names.get(g.group, ""))
            if body and g.weight > 1e-4 and vg_names[g.group] in rig.data.bones:
                acc[body] = acc.get(body, 0) + g.weight
        tot = sum(acc.values())
        if tot <= 0:
            acc, tot = {"pelvis": 1.0}, 1.0
        for b, w in acc.items():
            weights.setdefault(b, ([], []))
            weights[b][0].append(new_i)
            weights[b][1].append(w / tot)
    ev.to_mesh_clear()
    return np.array(verts, np.float32), np.array(uvs, np.float32), np.array(faces, np.int32), weights


def write_skn(path, verts, uvs, faces, weights):
    with open(path, "wb") as fh:
        fh.write(struct.pack("4i", len(verts), len(uvs), len(faces), len(weights)))
        fh.write(verts.astype("<f4").tobytes())
        fh.write(uvs.astype("<f4").tobytes())
        fh.write(faces.astype("<i4").tobytes())
        for body, (ids, ws) in weights.items():
            fh.write((PREFIX + body).encode().ljust(40, b"\0"))
            fh.write(struct.pack("3f", *MJ_BODY_POS[body]))
            fh.write(struct.pack("4f", 1, 0, 0, 0))
            fh.write(struct.pack("i", len(ids)))
            fh.write(np.array(ids, "<i4").tobytes())
            fh.write(np.array(ws, "<f4").tobytes())


def main():
    global OUT, TEX_MAX, PREFIX
    import argparse, sys
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=str(OUT))
    ap.add_argument("--prefix", default=PREFIX, help="MuJoCo body 이름 접두어")
    ap.add_argument("--texmax", type=int, default=TEX_MAX)
    a = ap.parse_args(argv)
    OUT, TEX_MAX, PREFIX = Path(a.outdir), a.texmax, a.prefix
    OUT.mkdir(parents=True, exist_ok=True)
    rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    pose_zero(rig)

    hip = (rig.data.bones["thigh_r"].head_local.z + rig.data.bones["thigh_l"].head_local.z) / 2
    scale = hip / 0.895
    pel = rig.data.bones["pelvis"].head_local
    # Blender 골반 → MuJoCo 골반 x,y 정렬 / 높이는 아래에서 발바닥=0 으로 맞춘다
    offset = Vector((0, 0, 0)) - (R.transposed() @ Vector((pel.x, pel.y, 0))) / scale

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    exported = {o.name: export_mesh(o, rig, scale, offset) for o in meshes}
    zmin = min(v[0][:, 2].min() for n, v in exported.items() if "shoes" in n)
    for name, (verts, uvs, faces, weights) in exported.items():
        verts[:, 2] -= zmin
        short = re.sub(r"^Human\.?", "", name) or "body"
        short = {"high-poly": "eyes"}.get(short, short)
        write_skn(OUT / f"{short}.skn", verts, uvs, faces, weights)
        img = find_texture(bpy.data.objects[name])
        if img is not None:
            save_texture(img, short)
        print(f"skin {short}: {len(verts)} verts, {len(faces)} faces, bodies={sorted(weights)}")

    print("WROTE skins to", OUT)

if __name__ == "__main__":
    main()
