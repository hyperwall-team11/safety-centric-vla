from fastapi.testclient import TestClient


def register_work_zones(client: TestClient, *zone_ids: str) -> None:
    for zone_id in zone_ids:
        response = client.post(
            "/zones",
            json={
                "zone_id": zone_id,
                "zone_type": "WORK",
                "geometry": {"type": "polygon", "points": [[0, 0], [1, 0], [1, 1]]},
                "frame_id": "map",
                "minimum_safety_distance": 0,
            },
        )
        if response.status_code != 201:
            raise AssertionError(f"{zone_id} 등록 실패: {response.text}")
