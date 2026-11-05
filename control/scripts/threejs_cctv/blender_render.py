"""Blender Cycles로 MuJoCo ① 공장 장면을 광선추적 렌더한다 (CV 입력용 실사 CCTV 영상).

    blender -b -P blender_render.py -- --data <data 폴더> --out <출력 폴더> [--frames 0:300] [--samples 96]

입력 (data 폴더):
    scene.glb    render.mjs --glb 로 내보낸 정적 장면 (설비·바닥·로봇·소품, 프레임 0 자세)
    scene.json, frames.bin   export_scene.py 출력 (geom 자세, 작업자 skin 정점, 머리 자세)
    *.png        작업자 피부·옷 텍스처
    <assets>/hdri/*.hdr      fetch_assets.py 의 HDRI (--assets 로 지정)
출력: <out>/<camera>/raw_000000.png  (CCTV 후처리 전 원본. cctv_post.py가 최종 PNG를 만든다)

GPU는 OPTIX → CUDA → HIP → METAL 순으로 찾고, 없으면 CPU로 렌더한다.
마스크·박스는 같은 장면·카메라로 render.mjs가 만든다 (렌더러가 달라도 형상·카메라가 같으므로 일치한다).
"""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
p = argparse.ArgumentParser()
p.add_argument("--data", type=Path, required=True)
p.add_argument("--out", type=Path, required=True)
p.add_argument("--assets", type=Path, default=Path(__file__).resolve().parent / "assets")
p.add_argument("--frames", default=None)
p.add_argument("--cameras", default="cctv_1,cctv_2")
p.add_argument("--samples", type=int, default=96)
p.add_argument("--device", default="auto", help="auto | cpu")
args = p.parse_args(argv)

meta = json.loads((args.data / "scene.json").read_text())
n_geom, n_workers, nsv = meta["ngeom_export"], len(meta["workers"]), meta["nskinvert"]
worker_len = nsv * 3 + 7
frame_len = n_geom * 12 + n_workers * worker_len
frames = np.memmap(args.data / "frames.bin", dtype="<f4", mode="r").reshape(-1, frame_len)
f0, f1 = (map(int, args.frames.split(":")) if args.frames else (0, meta["frames"]))

# ---- 장면 초기화와 GLB 가져오기 ----
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.import_scene.gltf(filepath=str(args.data / "scene.glb"))

# 텍스처를 뺀 재질에 Poly Haven 원본 텍스처를 다시 붙인다 (pbr 역할 → assets/textures/<이름>/diff·nor·arm.jpg)
manifest = json.loads((args.assets / "manifest.json").read_text())


def load_img(path, color):
    img = bpy.data.images.load(str(args.assets / path), check_existing=True)
    if not color:
        img.colorspace_settings.name = "Non-Color"
    return img


for mat in bpy.data.materials:
    if not mat.use_nodes:
        continue
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        continue
    if mat.name.startswith("floor_paint"):
        tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None) or nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(args.data / "floor_paint.png"))
        # 캔버스(위가 +y)와 glTF UV의 세로 방향이 반대라 v를 뒤집는다
        uvn = nt.nodes.new("ShaderNodeTexCoord"); fl = nt.nodes.new("ShaderNodeMapping")
        fl.inputs["Scale"].default_value = (1, -1, 1); fl.inputs["Location"].default_value = (0, 1, 0)
        nt.links.new(uvn.outputs["UV"], fl.inputs["Vector"]); nt.links.new(fl.outputs["Vector"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        mat.blend_method = "HASHED" if hasattr(mat, "blend_method") else None
        continue
    role = mat.get("pbr")
    if not role:
        continue
    maps = manifest["textures"][role]
    rep = list(mat.get("repeat", [1, 1]))
    uv = nt.nodes.new("ShaderNodeTexCoord"); mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (rep[0], rep[1], 1)
    nt.links.new(uv.outputs["UV"], mp.inputs["Vector"])

    def tex(path, color):
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = load_img(path, color)
        nt.links.new(mp.outputs["Vector"], t.inputs["Vector"]); return t

    arm = tex(maps["arm"], False); sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(arm.outputs["Color"], sep.inputs["Color"])
    if role == "metal":   # 스테인리스·아연도금: 판재 거칠기를 낮춰 금속 반사를 살린다
        mul = nt.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"; mul.inputs[1].default_value = 0.45
        nt.links.new(sep.outputs["Green"], mul.inputs[0]); nt.links.new(mul.outputs[0], bsdf.inputs["Roughness"])
        bsdf.inputs["Metallic"].default_value = 0.95
    else:
        nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
    nm = nt.nodes.new("ShaderNodeNormalMap"); nm.inputs["Strength"].default_value = 0.6 if mat.get("mapsOnly") else 1.0
    nt.links.new(tex(maps["nor"], False).outputs["Color"], nm.inputs["Color"])
    nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    if not mat.get("mapsOnly"):   # 바닥·벽·건물: 색 텍스처까지 (도장 설비는 원래 색 유지)
        diff = tex(maps["diff"], True)
        b = float(mat.get("brightness", 1.0))
        mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0; mix.inputs[7].default_value = (b, b, b, 1.0)
        nt.links.new(diff.outputs["Color"], mix.inputs[6]); nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
        bsdf.inputs["Metallic"].default_value = 0.0

# 소품: Poly Haven glTF 원본을 Three.js와 같은 위치·방향에 둔다
for name, places in json.loads((args.data / "decor.json").read_text()):
    path = manifest["models"].get(name)
    if not path:
        continue
    for i, (x, y, yaw) in enumerate(places):
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(args.assets / path))
        roots = [o for o in set(bpy.data.objects) - before if o.parent is None]
        for o in roots:
            o.location = (x, y, 1.0 if name == "power_box_01" else 0.0)
            o.rotation_mode = "XYZ"; o.rotation_euler = (o.rotation_euler[0], o.rotation_euler[1], yaw)

geom_obj = {}
for o in bpy.data.objects:
    name = o.name.split(".")[0]
    if name.startswith("geom_"):
        geom_obj[int(name[5:])] = o


def mat4(pos, rot9):
    r = np.asarray(rot9).reshape(3, 3)
    return Matrix(((r[0, 0], r[0, 1], r[0, 2], pos[0]), (r[1, 0], r[1, 1], r[1, 2], pos[1]),
                   (r[2, 0], r[2, 1], r[2, 2], pos[2]), (0, 0, 0, 1)))


# 움직이는 geom(로봇)만 매 프레임 갱신한다
poses = frames[:, :n_geom * 12].reshape(len(frames), n_geom, 12)
dynamic = [i for i in geom_obj if np.ptp(poses[:, i], axis=0).max() > 1e-5]
print(f"geom {len(geom_obj)}개, 움직이는 geom {len(dynamic)}개")

# ---- 작업자 ----
def principled(name, image=None, color=None, rough=0.8, sss=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = rough
    if sss:
        bsdf.inputs["Subsurface Weight"].default_value = sss
        bsdf.inputs["Subsurface Radius"].default_value = (0.9, 0.35, 0.2)
        bsdf.inputs["Subsurface Scale"].default_value = 0.01
    if image:
        tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = bpy.data.images.load(str(image), check_existing=True)
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if color:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    return m


def srgb(hexv):
    c = [((hexv >> s) & 255) / 255 for s in (16, 8, 0)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


HELMET = {"white": 0xF2F2EE, "yellow": 0xF5C518, "orange": 0xF26B1D}
skins = [s for s in meta["skins"] if s["render"]]
import base64
dec = lambda b, t: np.frombuffer(base64.b64decode(b), dtype=t)
workers = []
for k, wk in enumerate(meta["workers"]):
    parts = []
    for s in skins:
        faces = dec(s["face"], "<u4").reshape(-1, 3)
        uv = dec(s["uv"], "<f4").reshape(-1, 2)
        me = bpy.data.meshes.new(f"{wk['id']}_{s['name']}")
        me.vertices.add(s["vertnum"]); me.loops.add(len(faces) * 3); me.polygons.add(len(faces))
        me.loops.foreach_set("vertex_index", faces.ravel())
        me.polygons.foreach_set("loop_start", np.arange(0, len(faces) * 3, 3))
        me.polygons.foreach_set("use_smooth", np.ones(len(faces), bool))
        uvl = me.uv_layers.new()
        luv = uv[faces.ravel()].copy(); luv[:, 1] = 1.0 - luv[:, 1]   # MuJoCo v(위→아래) → Blender v(아래→위)
        uvl.data.foreach_set("uv", luv.ravel())
        me.update()
        tex = wk["suit"] if s["name"] == "worker_male_casualsuit04" else s["texture"]
        sss = 0.15 if s["name"] == "worker_body" else 0.0
        rough = 0.75 if "suit" in tex else 0.6 if s["name"] == "worker_body" else 0.85
        me.materials.append(principled(me.name, args.data / tex, rough=rough, sss=sss))
        ob = bpy.data.objects.new(me.name, me); scene.collection.objects.link(ob)
        parts.append((ob, s))
    # 안전모: 반구(돔) + 챙. 돔 축은 지역 z
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=0.135)
    cap = bpy.context.object
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="DESELECT"); bpy.ops.object.mode_set(mode="OBJECT")
    for v in cap.data.vertices:
        v.select = v.co.z < -1e-4
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.delete(type="VERT"); bpy.ops.object.mode_set(mode="OBJECT")
    cap.scale = (1.0, 0.85, 1.12); bpy.ops.object.transform_apply(scale=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.163, depth=0.012)
    brim = bpy.context.object; brim.scale = (1.0, 1.2, 1.0); bpy.ops.object.transform_apply(scale=True)
    bpy.ops.object.select_all(action="DESELECT"); cap.select_set(True); brim.select_set(True)
    bpy.context.view_layer.objects.active = cap; bpy.ops.object.join()
    bpy.ops.object.shade_smooth()
    helmet = cap; helmet.name = f"{wk['id']}_helmet"
    hm = principled(helmet.name, color=srgb(HELMET.get(wk["helmet"], 0xFFFFFF)), rough=0.3)
    hm.node_tree.nodes["Principled BSDF"].inputs["Coat Weight"].default_value = 0.4
    helmet.data.materials.append(hm)
    workers.append({"parts": parts, "helmet": helmet, "up": None})

h_off = Vector(meta["helmet_offset"])


def set_frame(f):
    row = frames[f]
    for i in dynamic:
        o = geom_obj[i]
        o.matrix_world = mat4(row[i * 12:i * 12 + 3], row[i * 12 + 3:i * 12 + 12])
    for k, w in enumerate(workers):
        base = n_geom * 12 + k * worker_len
        verts = row[base:base + nsv * 3]
        for ob, s in w["parts"]:
            ob.data.vertices.foreach_set("co", verts[s["vertadr"] * 3:(s["vertadr"] + s["vertnum"]) * 3])
            ob.data.update()
        hp = row[base + nsv * 3:base + worker_len]
        q = Quaternion((hp[3], hp[4], hp[5], hp[6]))
        if w["up"] is None:   # 세계 z를 머리 좌표로 옮긴 방향 = 머리의 위쪽
            w["up"] = (q.inverted() @ Vector((0, 0, 1))).normalized()
        center = Vector(hp[:3]) + q @ (h_off - w["up"] * 0.075)
        align = Vector((0, 0, 1)).rotation_difference(w["up"])
        w["helmet"].matrix_world = Matrix.Translation(center) @ (q @ align).to_matrix().to_4x4()


# ---- 조명: HDRI 환경광 + 햇빛 ----
world = bpy.data.worlds.new("world"); scene.world = world; world.use_nodes = True
wn = world.node_tree.nodes; env = wn.new("ShaderNodeTexEnvironment")
hdr = next((args.assets / "hdri").glob("*.hdr"))
env.image = bpy.data.images.load(str(hdr))
world.node_tree.links.new(env.outputs["Color"], wn["Background"].inputs["Color"])
wn["Background"].inputs["Strength"].default_value = 1.0
sun_data = bpy.data.lights.new("sun", "SUN"); sun_data.energy = 4.0; sun_data.angle = math.radians(1.5)
sun_data.color = (1.0, 0.95, 0.88)
sun = bpy.data.objects.new("sun", sun_data); scene.collection.objects.link(sun)
direction = Vector((1, 0, 0)) - Vector((-9, -12, 18))
sun.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

# ---- 카메라 (MuJoCo와 Blender 카메라 모두 -z를 보고 +y가 위) ----
cams = {}
for name in args.cameras.split(","):
    c = meta["cameras"][name]
    cd = bpy.data.cameras.new(name); cd.sensor_fit = "VERTICAL"; cd.angle_y = math.radians(c["fovy"])
    cd.clip_start = 0.1; cd.clip_end = 200
    co = bpy.data.objects.new(name, cd); scene.collection.objects.link(co)
    co.matrix_world = mat4(c["pos"], c["mat"])
    cams[name] = co

# ---- Cycles 설정 ----
scene.render.engine = "CYCLES"
cy = scene.cycles
prefs = bpy.context.preferences.addons["cycles"].preferences
device = "CPU"
if args.device != "cpu":
    for backend in ("OPTIX", "CUDA", "HIP", "METAL"):
        try:
            prefs.compute_device_type = backend
            prefs.get_devices()
        except TypeError:
            continue
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend
            device = backend
            break
cy.device = "GPU" if device != "CPU" else "CPU"
print(f"Cycles 장치: {device}")
cy.samples = args.samples
cy.use_adaptive_sampling = True
cy.adaptive_threshold = 0.02
cy.use_denoising = True
cy.denoiser = "OPTIX" if device == "OPTIX" else "OPENIMAGEDENOISE"
cy.max_bounces = 6
cy.caustics_reflective = cy.caustics_refractive = False
scene.render.use_persistent_data = True
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 640, 480, 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "AgX - Medium High Contrast"
scene.view_settings.exposure = 0.0

import time
for f in range(f0, f1):
    t = time.time()
    set_frame(f)
    for name, co in cams.items():
        scene.camera = co
        out = args.out / name / f"raw_{f:06d}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        scene.render.filepath = str(out)
        bpy.ops.render.render(write_still=True)
    print(f"프레임 {f} ({time.time() - t:.1f}s)", flush=True)
