"""Check offline render scoring with a synthetic render folder, without models or a GPU."""

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from offline_eval import (_stats, camera_from_render, consecutive_alarms, frame_numbers, iou, load_render,
                          match, percentile, rect_ring, score_frame, summarize, visible_workers)

FOCAL = 240 / math.tan(math.radians(30))
# 두 카메라 모두 (1, 2, 3)에서 바로 아래를 본다. 화면 중앙 아래 가장자리 = 바닥 (1, 2).
CAMERA = {"pos": [1, 2, 3], "mat": [1, 0, 0, 0, 1, 0, 0, 0, 1], "fovy": 60, "width": 640, "height": 480,
          "fx": FOCAL, "fy": FOCAL, "cx": 320, "cy": 240}
AT_CENTRE = [300, 160, 340, 240]  # 하단 중앙이 화면 중앙 → 바닥 (1, 2)


def gt(worker, box, pixels=1000):
    x0, y0, x1, y1 = box
    return {"worker_id": worker, "x0": x0, "y0": y0, "x1": x1 - 1, "y1": y1 - 1, "pixels": pixels}


def write_render(directory, inside_frames=(1,), frames=3, rendered=None):
    directory = Path(directory)
    (directory / "cameras.json").write_text(json.dumps({
        "fps": 10, "cameras": {"cctv_1": CAMERA, "cctv_2": CAMERA},
        "zone_rects": {"keep_out": [1.0, 2.0, 0.5, 0.5]}}))
    rows = ["frame,sim_time,worker_id,x,y,yaw,in_keep_out,dist_to_robot,robot_x,robot_y"]
    boxes = []
    for frame in range(frames):
        inside = frame in inside_frames
        rows.append(f"{frame},{frame / 10:.3f},worker-01,1.0,{2.0 if inside else 5.0},0,{int(inside)},1,0,0")
        rows.append(f"{frame},{frame / 10:.3f},worker-02,9.0,9.0,0,0,1,0,0")
        for camera in ("cctv_1", "cctv_2") if rendered is None or frame in rendered else ():
            boxes.append({"frame": frame, "camera": camera,
                          "workers": [gt("worker-01", AT_CENTRE), {"worker_id": "worker-02", "pixels": 0}]})
    (directory / "frames.csv").write_text("\n".join(rows) + "\n")
    (directory / "boxes.json").write_text(json.dumps(boxes))
    return load_render(directory)


class OfflineEvalTests(unittest.TestCase):
    def test_render_camera_projects_like_the_scene(self):
        camera = camera_from_render("cctv_1", CAMERA)
        self.assertEqual(camera["intrinsics"]["cx"], 320)
        with self.assertRaises(ValueError):
            camera_from_render("cctv_1", CAMERA | {"mat": [1, 0, 0]})

    def test_rect_ring_is_closed_and_uses_half_sizes(self):
        ring = rect_ring([1.2, 0.0, 0.7, 0.6])
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(ring[0], (0.5, -0.6))
        self.assertEqual(ring[2], (1.9, 0.6))
        with self.assertRaises(ValueError):
            rect_ring([0, 0, 0, 1])

    def test_matching_is_one_to_one_by_best_overlap(self):
        self.assertAlmostEqual(iou([0, 0, 10, 10], [5, 0, 15, 10]), 1 / 3)
        self.assertEqual(iou([0, 0, 1, 1], [2, 2, 3, 3]), 0.0)
        expected = [[0, 0, 10, 10]]
        pairs = match([[1, 0, 11, 10], [0, 0, 10, 10]], expected)
        self.assertEqual([(i, j) for i, j, _ in pairs], [(1, 0)])
        self.assertEqual(match([[0, 0, 10, 10]], [[6, 0, 16, 10]]), [])

    def test_hidden_and_tiny_workers_are_not_targets(self):
        visible, small = visible_workers([gt("a", [0, 0, 40, 40]), gt("b", [0, 0, 5, 5], pixels=20),
                                          {"worker_id": "c", "pixels": 0}])
        self.assertEqual(visible, {"a": [0.0, 0.0, 40.0, 40.0]})
        self.assertEqual(small, {"b": [0.0, 0.0, 5.0, 5.0]})

    def test_prediction_on_a_too_small_worker_is_ignored_not_false(self):
        with tempfile.TemporaryDirectory() as directory:
            render = write_render(directory)
        tiny = [600, 400, 620, 420]
        render["boxes"][(0, "cctv_1")].append(gt("worker-03", tiny, pixels=100))
        record = score_frame(0, {"cctv_1": [{"bbox_xyxy": tiny}, {"bbox_xyxy": [0, 0, 20, 30]}],
                                 "cctv_2": []}, render)["cameras"]["cctv_1"]
        self.assertEqual((record["too_small"], record["ignored_predictions"], record["false_positives"]),
                         (["worker-03"], 1, 1))

    def test_p95_is_nearest_rank(self):
        for n, want in ((1, 1), (19, 19), (20, 19), (21, 20), (40, 38), (100, 95)):
            with self.subTest(n=n):
                self.assertEqual(percentile(list(range(1, n + 1)), 95), want)
        self.assertEqual(_stats(list(range(1, 21)))["p95"], 19)

    def test_scoring_counts_hits_misses_alarms_and_position_error(self):
        with tempfile.TemporaryDirectory() as directory:
            render = write_render(directory)
            person = {"bbox_xyxy": AT_CENTRE, "confidence": None}
            stray = {"bbox_xyxy": [0, 0, 20, 30], "confidence": None}
            records = [
                score_frame(0, {"cctv_1": [person], "cctv_2": []}, render),          # 밖, 오경보
                score_frame(1, {"cctv_1": [], "cctv_2": [person, stray]}, render),   # 안, cctv_2 적중
                score_frame(2, {"cctv_1": [], "cctv_2": []}, render),                # 밖, 둘 다 놓침
            ]
        self.assertEqual(records[1]["inside_truth"], ["worker-01"])
        self.assertEqual(records[1]["cameras"]["cctv_2"]["false_positives"], 1)
        self.assertAlmostEqual(records[1]["cameras"]["cctv_2"]["position_errors_m"][0], 0.0)
        self.assertAlmostEqual(records[0]["cameras"]["cctv_1"]["position_errors_m"][0], 3.0)
        summary = summarize(records)
        self.assertEqual(summary["cameras"]["cctv_1"]["recall"], {"hit": 1, "total": 3, "rate": 0.3333})
        self.assertEqual(summary["cameras"]["cctv_2"]["recall_in_keep_out"]["rate"], 1.0)
        self.assertEqual(summary["cameras"]["cctv_2"]["precision"], {"hit": 1, "total": 2, "rate": 0.5})
        self.assertEqual(summary["combined_recall"], {"hit": 2, "total": 3, "rate": 0.6667})
        alarm = summary["keep_out_alarm"]
        self.assertEqual(alarm["hit_when_inside"]["rate"], 1.0)
        self.assertEqual(alarm["false_alarm_when_clear"], {"hit": 1, "total": 2, "rate": 0.5})
        self.assertEqual(alarm["per_camera_hit_when_inside"]["cctv_1"]["rate"], 0.0)
        # 연속 2프레임 규칙: 같은 카메라가 연속으로 본 적이 없어 경보가 없다.
        event = summary["keep_out_event"]
        self.assertEqual(event["hit_when_inside"]["rate"], 0.0)
        self.assertEqual((event["entries"], event["entries_never_alarmed"]), (1, 1))

    def test_event_alarm_needs_two_adjacent_frames_on_one_camera(self):
        # 정답상 프레임 1~5 진입. cctv_1은 1, 3(띄엄띄엄), cctv_2는 3·4 연속으로 봄 → 4부터 경보.
        with tempfile.TemporaryDirectory() as directory:
            render = write_render(directory, inside_frames=range(1, 6), frames=8)
        person = {"bbox_xyxy": AT_CENTRE}
        seen = {"cctv_1": {1, 3}, "cctv_2": {3, 4}}
        records = [score_frame(f, {s: [person] if f in seen[s] else [] for s in seen}, render) for f in range(8)]
        self.assertEqual(consecutive_alarms(records), {4})
        summary = summarize(records)
        event, frame = summary["keep_out_event"], summary["keep_out_alarm"]
        self.assertEqual(event["hit_when_inside"], {"hit": 1, "total": 5, "rate": 0.2})
        self.assertEqual(event["false_alarm_when_clear"]["hit"], 0)
        self.assertEqual(event["first_alarm_delay_s"]["max"], 0.3)  # 진입 0.1 s, 경보 0.4 s
        self.assertEqual(event["longest_unalarmed_inside"], {"frames": 3, "seconds": 0.3})
        self.assertEqual(frame["hit_when_inside"]["hit"], 3)  # 프레임 1, 3, 4
        self.assertEqual(frame["longest_unalarmed_inside"]["frames"], 1)
        self.assertEqual(event["stride"], 1)

    def test_gap_in_evaluated_frames_breaks_a_run(self):
        with tempfile.TemporaryDirectory() as directory:
            render = write_render(directory, inside_frames=range(8), frames=8, rendered={0, 1, 3, 4})
        person = {"bbox_xyxy": AT_CENTRE}
        records = [score_frame(f, {"cctv_1": [person] if f in (1, 3) else [], "cctv_2": []}, render)
                   for f in frame_numbers(render)]
        self.assertEqual(consecutive_alarms(records), set())  # 1과 3 사이에 2가 없다
        self.assertEqual(summarize(records)["keep_out_event"]["entries"], 2)

    def test_frame_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            render = write_render(directory)
        self.assertEqual(frame_numbers(render), [0, 1, 2])
        self.assertEqual(frame_numbers(render, stride=2), [0, 2])
        self.assertEqual(frame_numbers(render, limit=1), [0])
        for bad in ({"stride": 0}, {"stride": -1}, {"limit": 0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                frame_numbers(render, **bad)

    def test_only_rendered_frames_are_scored(self):
        # 부분 렌더(--frames 2:4): frames.csv에는 모든 프레임이 있지만 boxes.json에는 2, 3만 있다.
        with tempfile.TemporaryDirectory() as directory:
            render = write_render(directory, frames=6, rendered={2, 3})
        self.assertEqual(frame_numbers(render), [2, 3])
        self.assertEqual(frame_numbers(render, stride=2), [2])

    def test_truth_mode_runs_end_to_end_without_a_model(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_render(root)
            for camera in ("cctv_1", "cctv_2"):
                (root / camera).mkdir()
                for frame in range(3):
                    Image.new("RGB", (640, 480)).save(root / camera / f"{frame:06d}.png")
            script = Path(__file__).resolve().parents[1] / "offline_eval.py"
            subprocess.run([sys.executable, str(script), "--render-dir", str(root), "--detector", "truth",
                            "--output-dir", str(root / "eval")], check=True, capture_output=True)
            summary = json.loads((root / "eval" / "summary.json").read_text())
            lines = (root / "eval" / "frames.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(summary["combined_recall"]["rate"], 1.0)
        self.assertEqual(summary["keep_out_alarm"]["hit_when_inside"]["rate"], 1.0)
        self.assertIsNone(summary["model_call_ms"].get("median"))
        self.assertIn("keep_out_event", summary)

    def test_partial_render_replay_and_cli_validation(self):
        from PIL import Image

        script = Path(__file__).resolve().parents[1] / "offline_eval.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_render(root, frames=5, rendered={1, 2})
            for camera in ("cctv_1", "cctv_2"):
                (root / camera).mkdir()
                for frame in (1, 2):
                    Image.new("RGB", (640, 480)).save(root / camera / f"{frame:06d}.png")
            run = lambda *extra: subprocess.run([sys.executable, str(script), "--render-dir", str(root),
                                                 "--detector", "truth", *extra], capture_output=True, text=True)
            self.assertEqual(run("--output-dir", str(root / "a")).returncode, 0)
            first = json.loads((root / "a" / "summary.json").read_text())
            self.assertEqual(first["frames"], 2)
            replay = run("--output-dir", str(root / "b"), "--replay-frames", str(root / "a" / "frames.jsonl"))
            self.assertEqual(replay.returncode, 0, replay.stderr)
            second = json.loads((root / "b" / "summary.json").read_text())
            self.assertEqual(second["combined_recall"], first["combined_recall"])
            for bad in (["--stride", "0"], ["--limit", "0"], ["--min-visible-pixels", "-1"]):
                with self.subTest(bad=bad):
                    self.assertEqual(run("--output-dir", str(root / "c"), *bad).returncode, 2)


if __name__ == "__main__":
    unittest.main()
