import unittest

from app.models import NaturalLanguageTaskRequest
from app.task_parser import TaskParseError, parse_task_order


class TaskParserTest(unittest.TestCase):
    ROBOTS = ["robot-01", "robot-02"]

    def parse(
        self,
        instruction: str,
        *,
        target_robot_id: str | None = None,
        known_robot_ids: list[str] | None = None,
        frame_id: str = "map",
    ):
        return parse_task_order(
            NaturalLanguageTaskRequest(
                instruction=instruction,
                target_robot_id=target_robot_id,
                frame_id=frame_id,
            ),
            known_robot_ids or self.ROBOTS,
        )

    def test_extracts_exact_robot_with_two_registered_robots(self) -> None:
        task = self.parse("robot-02 로봇을 A구역으로 이동해")
        self.assertEqual(task.target_robot_id, "robot-02")

    def test_rejects_explicit_and_instruction_robot_conflict(self) -> None:
        with self.assertRaisesRegex(TaskParseError, "다릅니다"):
            self.parse(
                "robot-02 로봇을 A구역으로 이동해",
                target_robot_id="robot-01",
            )

    def test_rejects_multiple_or_unknown_robot_mentions(self) -> None:
        with self.assertRaisesRegex(TaskParseError, "여러 로봇"):
            self.parse("robot-01 로봇과 robot-02 로봇을 A구역으로 이동해")
        with self.assertRaisesRegex(TaskParseError, "등록되지 않은"):
            self.parse(
                "robot-99 로봇을 A구역으로 이동해",
                known_robot_ids=["robot-01"],
            )

    def test_rejects_non_executable_utterances(self) -> None:
        instructions = (
            "robot-01 로봇을 A구역으로 이동하지 마",
            "robot-01 로봇을 A구역으로 이동해서는 안 돼",
            "robot-01 로봇을 A구역으로 이동해선 안 돼",
            "robot-01 로봇을 A구역으로 이동하면 위험해",
            "문이 열리면 robot-01 로봇을 A구역으로 이동해",
            "위험이 없으면 robot-01 로봇을 A구역으로 이동해",
            "robot-01 로봇을 A구역으로 이동할까?",
            "robot-01 로봇을 A구역으로 이동해볼까",
            "robot-01 로봇을 A구역으로 이동해야 하나",
            "robot-01 로봇을 A구역으로 이동해도 괜찮을까",
            "robot-01 로봇을 A구역으로 이동해도 좋을지",
            "robot-01 로봇을 A구역으로 이동했어",
            "robot-01 로봇을 A구역으로 이동해 왔어",
            "robot-01 로봇을 A구역으로 이동해 둔 상태야",
            "robot-01 로봇을 A구역으로 이동해 봤어",
            "robot-01 로봇을 A구역으로 이동해서 점검했다",
            "robot-01 로봇을 A구역으로 보내지 말고 B구역으로 이동해",
            "robot-01 로봇으로 상자3을 B작업대로 옮겨 놨어",
            "robot-01 로봇으로 상자3을 B작업대로 옮겨볼까",
            "robot-01 로봇으로 상자3을 B작업대로 보내면 안 돼",
            "robot-01 로봇으로 상자3을 B작업대로 옮겨줘서 고마워",
        )
        for instruction in instructions:
            with self.subTest(instruction=instruction):
                with self.assertRaises(TaskParseError):
                    self.parse(instruction)

    def test_source_zone_is_not_selected_as_destination(self) -> None:
        task = self.parse(
            "A구역으로부터 출발해 B구역까지 이동해",
            target_robot_id="robot-01",
        )
        self.assertEqual(task.destination.model_dump(), {"type": "ZONE", "zone_id": "B구역"})

    def test_rejects_multiple_positive_destinations(self) -> None:
        with self.assertRaisesRegex(TaskParseError, "목적지가 여러 개"):
            self.parse("robot-01 로봇을 A구역으로 B구역까지 이동해")

    def test_supports_balanced_coordinate_forms_and_frame(self) -> None:
        cases = (
            "robot-01 로봇을 좌표: (1.5, -2)로 이동해",
            "robot-01 로봇을 (1.5, -2, 0) 위치로 이동해",
            "robot-01 로봇을 위치 x=1.5, y=-2, z=0 으로 이동해",
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                task = self.parse(instruction, frame_id="odom")
                self.assertEqual(task.destination.type, "COORDINATE")
                self.assertEqual(task.destination.frame_id, "odom")
                self.assertEqual(task.destination.position.x, 1.5)
                self.assertEqual(task.destination.position.y, -2.0)

    def test_rejects_broken_or_non_finite_coordinates(self) -> None:
        with self.assertRaisesRegex(TaskParseError, "괄호"):
            self.parse("robot-01 로봇을 좌표 (1.5, -2 잘못된 입력으로 이동해")
        huge = "9" * 400
        with self.assertRaisesRegex(TaskParseError, "유한"):
            self.parse(f"robot-01 로봇을 좌표 ({huge}, 2)로 이동해")

    def test_rejects_coordinate_trailing_garbage_or_extra_values(self) -> None:
        instructions = (
            "robot-01 로봇을 위치 1, 2garbage로 이동해",
            "robot-01 로봇을 좌표 1, 2, 3, 4로 이동해",
            "robot-01 로봇을 위치 1, 2미터로 이동해",
            "robot-01 로봇을 위치 (1, 2)garbage로 이동해",
        )
        for instruction in instructions:
            with self.subTest(instruction=instruction):
                with self.assertRaises(TaskParseError):
                    self.parse(instruction)

    def test_zone_name_is_not_misclassified_as_object(self) -> None:
        task = self.parse("robot-01 로봇을 상자3구역으로 이동해")
        self.assertEqual(task.destination.zone_id, "상자3구역")
        self.assertIsNone(task.target_object_id)

    def test_extracts_object_for_manipulation(self) -> None:
        task = self.parse("robot-01 로봇으로 상자 3을 B 작업대로 옮겨")
        self.assertEqual(task.target_object_id, "상자3")
        self.assertEqual(task.destination.zone_id, "B작업대")

    def test_supports_common_object_id_phrasings(self) -> None:
        cases = {
            "robot-01 로봇으로 3번 상자를 B작업대로 옮겨": "상자3",
            "robot-01 로봇으로 상자 번호 3을 B작업대로 옮겨": "상자3",
            "robot-01 로봇으로 박스3을 B작업대로 옮겨": "박스3",
            "robot-01 로봇으로 box-3을 B작업대로 옮겨": "box-3",
        }
        for instruction, expected_object_id in cases.items():
            with self.subTest(instruction=instruction):
                task = self.parse(instruction)
                self.assertEqual(task.target_object_id, expected_object_id)

    def test_object_causative_move_is_manipulation_command(self) -> None:
        task = self.parse("robot-01 로봇으로 상자3을 B작업대로 이동시켜줘")
        self.assertEqual(task.target_object_id, "상자3")

    def test_supports_spaced_polite_command_endings(self) -> None:
        move = self.parse("robot-01 로봇을 A구역으로 이동해 주세요")
        self.assertIsNone(move.target_object_id)
        manipulate = self.parse("robot-01 로봇으로 상자3을 B작업대로 옮겨 주세요")
        self.assertEqual(manipulate.target_object_id, "상자3")

    def test_supports_destination_e_particle_without_matching_source_eseo(self) -> None:
        task = self.parse("robot-01 로봇을 A구역에 가")
        self.assertEqual(task.destination.zone_id, "A구역")
        with_source = self.parse(
            "A구역에서 출발해 robot-01 로봇을 B구역에 이동해"
        )
        self.assertEqual(with_source.destination.zone_id, "B구역")
        coordinate_source = self.parse(
            "위치 1, 2에서 출발해 robot-01 로봇을 B구역에 이동해"
        )
        self.assertEqual(coordinate_source.destination.zone_id, "B구역")

    def test_rejects_glued_or_unquoted_multiword_zone_names(self) -> None:
        invalid = (
            "robot-01 로봇을 robot-01을A구역으로 이동해",
            "robot-01 로봇으로 상자를B구역으로 보내줘",
            "robot-01 로봇으로 상자3을B구역으로 보내줘",
            "robot-01 로봇을 빨리A구역으로 이동해",
            "robot-01 로봇을 조립 라인 구역으로 이동해",
        )
        for instruction in invalid:
            with self.subTest(instruction=instruction):
                with self.assertRaises(TaskParseError):
                    self.parse(instruction)

        quoted = self.parse('robot-01 로봇을 "조립 라인 구역"으로 이동해')
        self.assertEqual(quoted.destination.zone_id, "조립라인구역")

    def test_supports_korean_and_numeric_registered_robot_ids(self) -> None:
        korean = self.parse(
            "가동1 로봇을 A구역으로 이동해",
            known_robot_ids=["가동1", "가동2"],
        )
        self.assertEqual(korean.target_robot_id, "가동1")
        numeric = self.parse(
            "로봇 1을 A구역으로 이동해",
            known_robot_ids=["1", "2"],
        )
        self.assertEqual(numeric.target_robot_id, "1")

    def test_conventional_robot_id_match_is_case_insensitive(self) -> None:
        task = self.parse(
            "ROBOT-01 로봇을 A구역으로 이동해",
            known_robot_ids=["robot-01", "robot-02"],
        )
        self.assertEqual(task.target_robot_id, "robot-01")

    def test_rejects_unsupported_verb_and_control_commands(self) -> None:
        instructions = (
            "robot-01 로봇을 A구역으로 사진 찍어",
            "robot-01 로봇을 정지해",
            "robot-01 로봇 작업을 재개해",
            "robot-01 로봇 작업을 재개하지 마",
        )
        for instruction in instructions:
            with self.subTest(instruction=instruction):
                with self.assertRaises(TaskParseError):
                    self.parse(instruction)

    def test_requires_space_after_robot_particle(self) -> None:
        with self.assertRaisesRegex(TaskParseError, "공백"):
            self.parse(
                "로봇을A구역으로 이동해",
                known_robot_ids=["robot-01"],
            )


if __name__ == "__main__":
    unittest.main()
