"""Check the fall cues and the analysis record without models or a GPU."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_analysis import analysis_result, caption_fall_terms, lying_boxes

STANDING = {"bbox_xyxy": [100.0, 100.0, 140.0, 220.0], "confidence": 0.9}
LYING = {"bbox_xyxy": [200.0, 300.0, 330.0, 350.0], "confidence": 0.8}


class CaptionCueTests(unittest.TestCase):
    def test_fall_wording_is_found_in_order(self):
        caption = "A worker has collapsed and is lying motionless on the floor near a forklift."
        self.assertEqual(caption_fall_terms(caption), ["collapsed", "lying motionless on the floor"])

    def test_ordinary_factory_captions_are_not_flagged(self):
        for caption in ("Two workers are standing on the factory floor next to a robot.",
                        "A yellow forklift is parked on the ground beside a tank.",
                        "A box is laying around."):
            with self.subTest(caption=caption):
                self.assertEqual(caption_fall_terms(caption), [])

    def test_posture_wording_and_case(self):
        self.assertEqual(caption_fall_terms("A man is FACE DOWN by the conveyor."), ["face down"])
        self.assertEqual(caption_fall_terms("A person lies on the ground."), ["lies on the ground"])


class BoxCueTests(unittest.TestCase):
    def test_only_boxes_wider_than_tall_are_lying_candidates(self):
        boxes = [STANDING["bbox_xyxy"], LYING["bbox_xyxy"], [0.0, 0.0, 50.0, 50.0]]
        self.assertEqual(lying_boxes(boxes), [1])

    def test_ratio_boundary_and_degenerate_boxes(self):
        self.assertEqual(lying_boxes([[0.0, 0.0, 120.0, 100.0]]), [0])
        self.assertEqual(lying_boxes([[0.0, 0.0, 119.0, 100.0]]), [])
        self.assertEqual(lying_boxes([[5.0, 5.0, 5.0, 9.0], [5.0, 5.0, 9.0, 5.0]]), [])


class AnalysisResultTests(unittest.TestCase):
    def result(self, caption, people):
        return analysis_result("cctv_1", "cctv_1-0001.png", caption, people, "model-x", 12.5,
                               run_id="run-a", hazard_id="hazard-1",
                               now=datetime(2026, 11, 25, tzinfo=timezone.utc))

    def test_either_cue_flags_a_fall_and_both_are_recorded(self):
        by_caption = self.result("A worker is lying on the floor.", [STANDING])
        by_box = self.result("Workers near a robot.", [STANDING, LYING])
        neither = self.result("Workers near a robot.", [STANDING])
        self.assertTrue(by_caption["fall_suspected"])
        self.assertEqual(by_caption["lying_boxes"], [])
        self.assertTrue(by_box["fall_suspected"])
        self.assertEqual((by_box["caption_terms"], by_box["lying_boxes"]), ([], [1]))
        self.assertFalse(neither["fall_suspected"])

    def test_record_links_back_to_the_run_and_hazard(self):
        record = self.result("Workers near a robot.", [STANDING])
        self.assertEqual((record["run_id"], record["hazard_id"], record["sensor_id"]),
                         ("run-a", "hazard-1", "cctv_1"))
        self.assertEqual(record["analyzed_at"], "2026-11-25T00:00:00+00:00")
        self.assertTrue(record["analysis_id"].startswith("analysis-"))
        self.assertNotEqual(record["analysis_id"], self.result("x", [])["analysis_id"])


if __name__ == "__main__":
    unittest.main()
