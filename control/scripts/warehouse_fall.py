"""발표 콘솔의 CCTV 04 낙상자 점검과 Backend 위험 등록."""
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.request import urlopen
from uuid import uuid4

from hazard_reporter import BackendClient, BackendHTTPError, observation_id

from warehouse import FALL_ZONE

FALL_ZONE_ID = "후방작업통로"
VIEW = "cctv_4"
FRAME_COUNT = 5
MIN_POSITIVE_FRAMES = 2
VLM = Path(__file__).resolve().parents[2] / "mujoco-demo" / "fall_vlm.py"


class FallInspection:
    def __init__(self, directory, backend, video):
        self.directory = directory
        self.client = BackendClient(backend)
        self.video = video
        self.hazard_id = f"hazard-{uuid4()}"
        self.latest = None

    def _capture(self, directory):
        captures = []
        seen = set()
        deadline = time.monotonic() + 7.0
        while len(captures) < FRAME_COUNT and time.monotonic() < deadline:
            with urlopen(f"{self.video}/snapshot/{VIEW}", timeout=2) as response:
                stamp = response.headers.get("X-ROS-Stamp")
                received = response.headers.get("X-Frame-Received-At")
                if not stamp or not received:
                    raise ValueError("CCTV 04의 ROS 프레임 시각이 없습니다.")
                if time.time() - float(received) > 2.0:
                    raise ValueError("CCTV 04의 최신 영상이 없습니다.")
                if stamp not in seen:
                    seen.add(stamp)
                    path = directory / f"frame-{len(captures) + 1}.jpg"
                    path.write_bytes(response.read())
                    captures.append((path, stamp, float(received)))
            time.sleep(0.10)
        if len(captures) != FRAME_COUNT:
            raise ValueError("CCTV 04에서 서로 다른 영상 5장을 확보하지 못했습니다.")
        return captures

    def run(self):
        directory = self.directory / "fall" / str(uuid4())
        directory.mkdir(parents=True)
        captures = self._capture(directory)
        preview = directory / "preview.jpg"
        try:
            result = subprocess.run(
                [sys.executable, str(VLM), *(str(path) for path, _, _ in captures),
                 "--camera", str(self.directory / "data/scene.json"), "--polygon", json.dumps(FALL_ZONE),
                 "--preview", str(preview)],
                capture_output=True, text=True, timeout=300, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            (directory / "model-error.log").write_text(str(exc.stderr or exc), encoding="utf-8")
            raise ValueError("VLM 실행에 실패했습니다. 시연 로그의 fall 점검 폴더의 model-error.log를 확인하세요.") from exc

        inference = json.loads(result.stdout)
        observations = []
        for (_, stamp, received), boxes in zip(captures, inference["frames"], strict=True):
            sec, nanosec = map(int, stamp.split("."))
            observations.append({"ros_stamp": stamp, "received_at": datetime.fromtimestamp(
                received, timezone.utc).isoformat(), "boxes": [
                    {**box, "observation_id": observation_id(VIEW, sec, nanosec, i)}
                    for i, box in enumerate(boxes)
                ]})
        positive = sum(bool(frame["boxes"]) for frame in observations)
        result = {"model": inference["model"], "sensor_id": VIEW, "positive_frames": positive,
                  "frame_count": FRAME_COUNT, "observations": observations,
                  "detected_at": datetime.now(timezone.utc).isoformat(), "hazard_id": None}
        (directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if positive >= MIN_POSITIVE_FRAMES:
            payload = {"hazard_id": self.hazard_id, "hazard_type": "UNKNOWN", "severity": "WARNING",
                       "source_observation_ids": [box["observation_id"] for frame in observations for box in frame["boxes"]],
                       "related_robot_ids": [], "related_worker_ids": [], "zone_id": FALL_ZONE_ID,
                       "confidence": positive / FRAME_COUNT, "detected_at": result["detected_at"]}
            try:
                self.client.post_hazard(payload)
            except BackendHTTPError as exc:
                raise ValueError(f"작업자 낙상 위험을 등록하지 못했습니다. {exc}") from exc
            result["hazard_id"] = self.hazard_id
            (directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.latest = (result, preview)
        return result
