# CV 통합 준비와 남은 작업

확인일: 2026-10-30 / 담당: 강영한.

## PR 진행 순서

| PR | 내용 | 검토할 범위 |
|---|---|---|
| [#12](https://github.com/hyperwall-team11/safety-centric-vla/pull/12) | 이미지 해시·카메라 메타데이터·Observation 내보내기 | ONT: 필수 필드와 신뢰도 보류 / CTL: 카메라 행렬 |
| [#13](https://github.com/hyperwall-team11/safety-centric-vla/pull/13) | 정지 이미지 연속 추론 벤치마크 | 처리율·지연의 측정 범위와 GPU 실측 기록 |
| [#19](https://github.com/hyperwall-team11/safety-centric-vla/pull/19) | ROS 2 CCTV 입력과 탐지 JSONL·요약 | 실제 카메라 입력·CUDA 실행 확인 후 리뷰 |

#13에 #12 기반을 반영하면서 README 충돌을 해결했고, #19에도 갱신된 기반을 반영했습니다. 리뷰 차이가 섞이지 않도록 #13은 #12 작업 브랜치, #19는 #13 작업 브랜치를 대상으로 쌓습니다. #12를 `develop`에 Squash and merge한 뒤 #13의 대상을 `develop`으로 변경하고 diff를 확인합니다. #13 병합 뒤 #19도 같은 절차를 따릅니다. `develop`/`main` 직접 push와 작성자 단독 승인은 사용하지 않습니다.

## 이번 확인 결과

- Observation 변환 CPU 테스트 11개 통과. MuJoCo 3.15.0의 실제 캡처 두 장 생성·해시 확인. [세부 기록](cv-review-2026-10-30.md).
- RGB 행 패딩·잘못된 입력·신뢰도·동시 수신 중 최신 프레임 유지 테스트 8개 통과. 전체 CV CPU 테스트 19개 통과, Python 구문 검사 통과.
- 수신과 추론을 서로 다른 callback group에서 처리하도록 수정했습니다. [ROS 2 Humble executor 구현](https://github.com/ros2/rclpy/blob/humble/rclpy/rclpy/executors.py)을 기준으로 2개 실행 스레드와 종료 대기를 사용합니다. 실제 ROS 스케줄링·GPU 동작은 아직 실행하지 않았습니다.
- `check_camera_env.py` 실행 결과 이 Mac의 현재 Python에는 ROS 2와 CUDA가 없어 실제 카메라 탐지를 실행할 조건이 충족되지 않았습니다. CTL RoboStack 기록은 Python 3.12, 기존 Florence 서버 기록은 Python 3.13입니다. 한 실행 환경의 Python·ROS 바이너리·CUDA PyTorch 호환성을 먼저 맞춰야 합니다.
- 리뷰 요청된 NLP 브랜치도 각 head에서 기존 테스트를 실행했습니다: #17 55개, #18 56개, #20 56개 통과. 테스트 통과가 통합 병합이나 리뷰 승인 완료를 의미하지는 않습니다.

## 실제 영상 확인 순서

1. CTL과 [#16](https://github.com/hyperwall-team11/safety-centric-vla/issues/16)에서 실행 위치를 정합니다. 같은 GPU 호스트에서 브리지와 CV를 함께 실행 가능한지 먼저 확인합니다. Mac 토픽의 GPU 전달 방식은 아직 미정입니다.
2. CTL 체크아웃에서 `cd control && pixi run bridge`. 현재 CV 체크아웃에는 `control/` 폴더가 없습니다.
3. CV 체크아웃의 `mujoco-demo/`에서 `python check_camera_env.py`. 모든 import와 CUDA가 통과해야 다음으로 진행합니다.
4. ROS 환경에서 `ros2 topic info /cctv_1/image_raw -v`와 `ros2 topic hz /cctv_1/image_raw`로 `sensor_msgs/Image`, rgb8 640×480, 발행자를 확인합니다. 서로 같은 ROS domain을 사용해야 합니다.
5. [실행 안내](../mujoco-demo/README.md)의 `--max-frames 30`으로 JSONL·요약 생성을 확인한 뒤 `--duration-seconds 60`으로 연속 측정합니다. 종료 이유가 `input_timeout`/`error`라면 성공으로 보고하지 않습니다.
6. `processed`, `local_processing_hz`, `p95_processing_ms`, `p95_queue_age_ms`, `replaced_pending`, `stale_pending`, `invalid_frames`와 실행 환경을 #19에 남깁니다. JSONL에서 원래 ROS stamp, 수신 UTC, frame_id와 sensor_id도 확인합니다.

로컬 대기 시간은 촬영·네트워크부터의 전체 지연이 아닙니다. 발행자의 ROS 시각이 UTC인지 시뮬레이션 시각인지 확인하기 전에는 UTC와 빼서 전체 지연을 계산하지 않습니다. 토픽 15Hz, 정지 이미지 12.46Hz, 실제 영상 처리율은 서로 다른 측정입니다.

## 통합을 위해 남은 인터페이스

- **CV + ONT**: confidence를 제공하는 탐지기 또는 검증 방법, Observation 등록 경로·선택 필드, Worker/HazardEvent 갱신 계약. 현재 서버에 Observation 등록 API가 없습니다.
- **CV + CTL**: camera_info·TF에 따른 world 투영과 정답 대비 위치 오차 검증. 현재 ROS 구독기는 이미지 좌표만 기록하며 오프라인 내보내기와 자동 연결하지 않습니다.
- **CV**: 공개 실제 사람 영상의 라벨·평가 구간·버전을 고정한 재현율 평가. MuJoCo 도형 작업자만으로 실제 작업자 성능을 입증하지 않습니다.
- **ONT + NLP + CTL**: 별도 ONT 브랜치의 안전검사 서비스는 backend route 및 실제 어댑터에 아직 연결되지 않았습니다. 현재 mock 실행 로그를 실제 로봇 실행·정지 완료로 보고하지 않습니다.

## 팀 PR 검토 중 확인한 점

#17과 #18을 현재 head끼리 합치면 `backend/app/main.py`에 충돌이 발생합니다. 각각 `develop`과 충돌이 없다는 표시가 떠도, 앞 PR 병합 뒤 다음 PR은 갱신과 재검증이 필요합니다.

#20은 `POST /task-orders/preview`에 `{}`를 보내면 422 `detail` **목록**을 반환하지만 OpenAPI는 `TaskParseErrorResponse`의 `detail` **객체**만 선언합니다. 실제 응답을 해당 모델로 검증하면 실패합니다. 요청 본문 검증 오류와 자연어 파싱 오류 두 형태를 문서와 테스트에 반영해야 합니다.
