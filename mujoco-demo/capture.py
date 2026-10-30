"""Save two CCTV views and capture metadata, without running any detector."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import mujoco
from PIL import Image

root = Path(__file__).resolve().parent
output = root / "outputs"
output.mkdir(exist_ok=True)
model = mujoco.MjModel.from_xml_path(str(root / "scene.xml"))
data = mujoco.MjData(model)
camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "cctv_1")
worker_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "worker")
mocap_id = model.body_mocapid[worker_id]
metadata = []

with mujoco.Renderer(model, height=480, width=640) as renderer:
    for label, position in [("outside", (2, 0, 0)), ("inside", (0, 0, 0))]:
        data.mocap_pos[mocap_id] = position
        mujoco.mj_forward(model, data)
        renderer.update_scene(data, camera="cctv_1")
        pixels = renderer.render().copy()
        image_path = output / f"cctv_1_{label}.png"
        Image.fromarray(pixels).save(image_path)
        metadata.append({
            "image": image_path.name,
            "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "sensor_id": "cctv_1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "frame_id": "world",
            "width": 640,
            "height": 480,
            "camera": {
                "position_m": data.cam_xpos[camera_id].tolist(),
                "rotation_cam_to_world": data.cam_xmat[camera_id].reshape(3, 3).tolist(),
                "fovy_deg": float(model.cam_fovy[camera_id]),
                "ground_plane_z_m": 0.0,
            },
            "ground_truth_worker_position_m": list(position),
            "ground_truth_zone_status": label,
        })
        print(f"Saved: {image_path}")

(output / "metadata.json").write_text(
    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
)
