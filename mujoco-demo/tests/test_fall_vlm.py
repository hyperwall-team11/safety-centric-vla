import pytest

from fall_vlm import fallen_workers, project_point, zone_crop

CAMERA = {"width": 640, "height": 480, "pos": [0, 0, 4], "fovy": 60,
          "mat": [1, 0, 0, 0, 1, 0, 0, 0, 1]}
ZONE = [(-1, -1), (1, -1), (1, 1), (-1, 1)]


def test_upright_person_and_cardboard_do_not_count_as_fallen_workers():
    assert project_point((0, 0, 0), CAMERA) == pytest.approx((320, 240))
    left, top, _, _ = bounds = zone_crop(CAMERA, ZONE)
    lying = [275 - left, 227 - top, 360 - left, 251 - top]
    upright = [309 - left, 188 - top, 332 - left, 271 - top]
    result = fallen_workers({"labels": ["person", "worker", "cardboard box"],
                             "bboxes": [lying, upright, lying]}, CAMERA, ZONE, bounds)
    assert len(result) == 1 and result[0]["bbox_xyxy"] == [275, 227, 360, 251]
