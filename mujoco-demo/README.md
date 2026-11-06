# MuJoCo 가상 CCTV 장면

`scene.xml`에는 바닥, 금지구역의 빨간 표시, 단순 작업자 모형, `cctv_1` 카메라가 있습니다. 빨간 표시는 시각화일 뿐 안전 규칙을 실행하지 않습니다.

## 이미지 생성

MuJoCo 3.15.0과 Pillow를 설치한 로컬 Python 환경에서 실행합니다.

```bash
python -m pip install mujoco==3.15.0 pillow
python capture.py
```

`outputs/`에 작업자가 구역 바깥과 안에 있는 640×480 PNG 두 장 및 `metadata.json`이 생성됩니다. GUI 뷰어에는 `scene.xml`을 드래그하여 장면을 볼 수 있습니다.
`metadata.json`에는 이미지 SHA-256, 촬영 시각, 카메라 위치·회전·시야각도 기록됩니다. `ground_truth_*` 값은 시뮬레이션 평가용이며 추론 입력으로 사용하지 않습니다.

## Florence-2 탐지

CUDA 지원 PyTorch, `transformers==4.49.0`, `timm`, `Pillow`가 있는 GPU 환경에서 실행합니다. 서버 환경에 맞는 PyTorch를 먼저 준비하세요. 모델 파일은 Hugging Face에서 내려받거나 로컬 경로로 전달합니다.

```bash
python florence_detect.py \
  outputs/cctv_1_outside.png outputs/cctv_1_inside.png \
  --model microsoft/Florence-2-base \
  --output vlm_results
```

외부망이 없는 계산 노드에서는 모델 파일을 미리 공유 저장소에 내려받은 뒤 `--model /path/to/Florence-2-base`로 실행할 수 있습니다. 실행 결과는 이미지별 JSON과 바운딩 박스가 그려진 PNG입니다. `examples/`에는 2026-10-09에 만든 예시 이미지가 있습니다. 이 코드는 허깅페이스 모델 구현을 `trust_remote_code=True`로 로드하므로 신뢰할 수 있는 모델 파일을 사용해야 합니다.

현재 Florence-2 탐지 출력은 픽셀 좌표의 `person` 바운딩 박스이며 신뢰도 점수는 없습니다. `HazardEvent` 판정은 아직 구현되지 않았습니다.

## Observation 변환

`capture.py`로 이미지를 다시 만든 뒤 **그 이미지에 대해** `florence_detect.py`를 실행하세요. 캡처 메타데이터와 탐지 JSON의 이미지 SHA-256이 같아야 합니다. 변환에는 Python 표준 라이브러리만 필요하며 GPU가 없어도 됩니다.

```bash
python observation_export.py \
  vlm_results/cctv_1_outside.json vlm_results/cctv_1_inside.json \
  --metadata outputs/metadata.json \
  --output vlm_results/observations.json
```

변환기는 `person`을 `WORKER`로 매핑하고 상자의 아래쪽 중앙 픽셀에서 바닥 평면(`z=0`)으로 광선을 투영해 `world` 좌표를 추정합니다. 상자 아래쪽 중앙이 실제 바닥 접점이라고 가정합니다. 이 가정에는 원근 편향이 있습니다. 화면 가장자리로 갈수록 몸 위쪽이 바깥으로 기울어 보여 상자 중앙이 발보다 바깥쪽에 놓이므로, 분할 렌더로 만든 정확한 상자에서도 x 오차가 최대 약 0.22m 생기고 화면 중앙에서는 0입니다([측정](../docs/cv-review-2026-10-30.md#2026-11-02-pr-12-리뷰-반영)). 발이 가려졌거나 탐지 상자가 부정확하면 오차는 더 커질 수 있고, 실제 Florence-2 상자의 오차는 측정하지 않았습니다. 금지구역 경계 판정에 그대로 쓰지 마세요. 출력의 `observation_details`에는 상자 `[x, y, w, h]`와 계산 방식을 보관합니다. `ground_truth_*`는 위치 계산에 사용하지 않습니다.

메타데이터의 회전은 `rotation_convention: "mujoco_camera"`(x 오른쪽, y 위, 시선 -z)입니다. 이 표시가 없거나 다르면 변환을 거부하므로 예전 `metadata.json`은 `capture.py`로 다시 만들어야 합니다. CTL 브리지의 TF·`CameraInfo`는 ROS 광학 좌표계(y 아래, 시선 +z)라서 변환 없이 넣을 수 없습니다. 또한 `scene.xml`의 `cctv_1`(위치 0, -6, 5)은 CTL `scenario_scene.xml`의 `cctv_1`과 다른 카메라이므로, 이 변환 결과를 CTL 장면 좌표와 같다고 보면 안 됩니다.

현재 Florence-2 `<OD>` 출력에는 신뢰도 점수가 없습니다. 따라서 실제 출력은 `pending_confidence`에 보류되고, 필수 `confidence`가 없는 값을 완전한 `Observation`이나 안전 판단 입력으로 사용하지 않습니다. 향후 **검증된 탐지기**가 `<OD>` 결과에 상자별 `scores` 배열을 제공하면 `observations`에 스키마 v1.1의 필수 필드를 갖춘 레코드가 나옵니다. 임의의 점수나 고정값을 넣지 마세요. 현재 백엔드 `Observation` 모델은 v1.1의 선택 필드(`bbox`, `position_source`, `ingested_at`)를 받지 않고 등록 API도 없으므로, 서버 연결은 ONT 담당과 인터페이스를 정한 뒤 진행합니다.

## 민서님 MuJoCo 카메라 토픽 연결

`ros_camera_detect.py`는 CTL 브리지의 `/cctv_1/image_raw`를 구독해 최신 RGB 프레임을 Florence-2 `<OD>`에 입력합니다. 결과는 프레임 시각·카메라 ID·작업자 바운딩 박스가 담긴 JSONL로 저장합니다. 모델 추론이 카메라보다 느리면 오래된 프레임을 쌓지 않고 최신 프레임으로 넘어갑니다.

CTL #7·#15가 병합된 `develop`(`bd2a543`)을 CV 브랜치에 반영했습니다. 이 체크아웃의 `control/`에서 브리지를, `mujoco-demo/`에서 CV 노드를 서로 다른 터미널로 실행할 수 있습니다. 카메라 영상이 필요할 때는 뷰어가 아닌 `pixi run bridge`를 사용합니다.

```bash
cd control
pixi run bridge
```

같은 ROS 2 네트워크의 다른 터미널에서, **ROS 2 Humble의 `rclpy`·`sensor_msgs`와 CUDA PyTorch·Transformers가 같은 Python 환경에 있는 상태**로 실행합니다. CTL의 RoboStack 환경은 Python 3.12이고 기존 Florence 서버 기록은 Python 3.13이므로, 두 환경을 그대로 함께 source하면 호환된다고 가정할 수 없습니다. GPU 서버에서 사용할 공통 Python·ROS 환경과 토픽 접근을 먼저 확인합니다.

```bash
cd mujoco-demo
python check_camera_env.py
python ros_camera_detect.py \
  --topic /cctv_1/image_raw \
  --model /path/to/florence-2-base \
  --output vlm_results/ros_camera.jsonl \
  --max-frames 30
```

환경 점검은 모듈 import와 CUDA만 확인합니다. 별도로 `ros2 topic info /cctv_1/image_raw -v`와 `ros2 topic hz /cctv_1/image_raw`로 메시지 타입·발행자·주기를 확인하세요. Mac 브리지는 `ROS_LOCALHOST_ONLY=1`이므로 학교 GPU 서버로의 전송은 [#16](https://github.com/hyperwall-team11/safety-centric-vla/issues/16)에서 실행 위치·연결 방식을 확정해야 합니다.

수신과 추론은 별도 callback group 및 두 executor 스레드에서 실행합니다. 최신 프레임 하나만 보관하며 수신 후 로컬 대기가 500ms를 넘으면 버립니다. 15초 동안 입력이 없으면 요약을 쓰고 종료 코드 2로 끝납니다. 이 기준은 `--max-frame-age-ms`, `--idle-timeout-seconds`로 바꿀 수 있습니다.

30프레임 확인 후 `--max-frames 30` 대신 `--duration-seconds 60`으로 실행하면 첫 수신부터 최소 60초 구간을 확인할 수 있습니다. 결과 JSONL과 함께 `ros_camera.summary.json` 같은 요약 파일에 처리 건수, 로컬 처리율, 처리 지연 p95, 대기 지연 p95, 교체·폐기 건수가 기록됩니다. `received_callbacks`는 Python 콜백에 도착한 건수여서 DDS에서 이미 버린 프레임까지 세지는 못합니다. 원래 ROS 시각은 `stamp`에, 수신 UTC는 `received_at_utc`에 별도 기록합니다. 두 시계가 같다고 가정하지 않으며 로컬 대기 시간은 네트워크·촬영부터의 전체 지연이 아닙니다.

다른 영상 토픽은 `/cctv_2/image_raw`, `/robot/head_camera/image_raw`입니다. 각 카메라의 `.../camera_info`와 TF, 정답 비교용 `/sim/worker_pose`·`/sim/worker_in_keep_out`도 CTL 브리지가 발행합니다. `--topic`을 여러 번 주면 여러 카메라를 한 탐지기로 번갈아 처리합니다(아래 "여러 카메라와 미탐지 프레임 저장"). Florence-2 `<OD>`가 신뢰도 점수를 내지 않으면 `pending_confidence=true`로 표시하며, 안전 판정용 `Observation`·`HazardEvent`를 발행하지 않습니다. ROS 2 카메라 입력부터 모델 출력까지의 실제 동작·처리율은 아직 측정하지 않았습니다.

### 탐지기 선택

`--detector rtdetr`로 RT-DETR(`PekingU/rtdetr_r50vd`, COCO 학습)을 쓸 수 있습니다. 기본값은 기존과 같은 `florence2`입니다. 2026-11-02 측정에서 Florence-2의 1분 처리율이 7 Hz 미만이었으므로, 개발계획서 5-2의 경량 탐지기 전환 규칙에 따라 추가했습니다. YOLO 계열 대신 RT-DETR을 고른 이유는 Apache-2.0 라이선스이고, 현재 환경의 Transformers로 추가 설치 없이 쓸 수 있기 때문입니다. 모델 가중치의 라이선스와 사용 조건은 모델 카드에서 따로 확인하세요.

```bash
python ros_camera_detect.py --detector rtdetr --model /path/to/rtdetr_r50vd \
  --topic /cctv_1/image_raw --output vlm_results/ros_camera_rtdetr.jsonl --duration-seconds 60
```

- RT-DETR은 상자별 점수를 줍니다. `person`이면서 점수가 0.5(계획서의 WORKER 임계) 이상인 상자만 `people`에 넣고 `confidence`를 채웁니다.
- 두 탐지기 모두 모델이 낸 라벨 전체를 `all_detections`에 기록합니다. RT-DETR은 점수 0.3 이상만 남깁니다. 미탐지가 "못 찾음"인지 "다른 라벨"인지 구분하는 분석용이며 작업자 판정에는 쓰지 않습니다.
- `generation_ms`는 Florence-2에서는 생성 시간, RT-DETR에서는 모델 순전파 시간입니다. 둘 다 전처리·후처리를 제외합니다.
- 2026-11-03 서버 측정에서 RT-DETR은 13.71 Hz였지만 현재 CTL 마네킹을 작업자로 한 번도 잡지 못했습니다(0%). 상세는 `benchmarks/2026-11-02-dis01-ros-camera.md`에 있습니다.

### 여러 카메라와 미탐지 프레임 저장

```bash
python ros_camera_detect.py --topic /cctv_1/image_raw --topic /cctv_2/image_raw \
  --model /path/to/florence-2-base --output vlm_results/ros_camera_2cam.jsonl \
  --duration-seconds 60 --save-misses-dir vlm_results/misses_2cam
```

- 탐지기 하나가 카메라마다 최신 프레임 하나를 보관하고 차례대로 처리합니다. 한 카메라의 프레임만 들어와 있으면 그 카메라를 처리합니다.
- 요약의 `per_topic`에 카메라별 처리 건수·처리율·지연 p95가, 최상위에 전체 합계가 들어갑니다. 카메라별 처리율은 같은 탐지기를 나눠 쓴 값입니다.
- 어느 한 카메라라도 `--idle-timeout-seconds` 동안 입력이 없으면 종료 코드 2로 끝납니다.
- `--batch-cameras`를 주면 카메라마다 들어와 있는 최신 프레임을 모아 모델을 한 번만 호출합니다. 이때 `generation_ms`와 `processing_ms`는 묶음 전체 시간이며 기록의 `batch_size`에 묶음 크기가 남습니다.
- `--num-beams`는 Florence-2 생성의 빔 수입니다(기본 3, 기존과 같음). 줄이면 빨라질 수 있지만 탐지 결과가 달라질 수 있어 정답 비교로 확인한 뒤 바꿉니다.
- `--save-misses-dir`를 주면 작업자(`people`)가 없는 입력 프레임을 `<카메라>_<sec>_<nanosec>.png`로 저장하고 기록의 `saved_frame`에 경로를 남깁니다. 저장은 기본 300장까지(`--max-saved-misses`)이며 저장 시간은 `processing_ms`에 넣지 않지만 다음 프레임 처리를 늦출 수 있습니다. 처리율을 잴 때는 저장 없이 따로 실행하세요.

### 카메라 보정 파일

바닥 위치 투영(센서·작업자 스냅샷, HazardEvent)에는 고정 CCTV의 위치·방향·렌즈 정보가 필요합니다. `--camera-calib <파일>`을 주면 MuJoCo를 읽지 않고 이 파일만 쓰므로, MuJoCo 렌더링·Three.js 렌더링·실제 CCTV 어느 영상에도 같은 CV 노드를 쓸 수 있습니다. 파일이 없으면 기존처럼 `--scene`의 MuJoCo 장면에서 읽고 640×480으로 가정합니다.

```bash
# 시나리오 장면의 CCTV 2대 보정 파일 (저장소에 cameras/scenario_cctv.json으로 포함)
python camera_calib.py --scene ../control/mujoco/models/scenario_scene.xml \
  --camera cctv_1 --camera cctv_2 --output cameras/scenario_cctv.json
```

```json
{"version": 1, "cameras": [{
  "sensor_id": "cctv_1", "topic": "/cctv_1/image_raw", "width": 640, "height": 480,
  "position_m": [1.2, -4.2, 3.2],
  "rotation_cam_to_world": [[1, 0, 0], [0, 0.568, -0.823], [0, 0.823, 0.568]],
  "rotation_convention": "mujoco_camera", "world_frame": "mujoco_z_up",
  "fovy_deg": 60, "ground_plane_z_m": 0.0}]}
```

- 위치·방향은 백엔드 Zone과 같은 월드 좌표(z 위)입니다. Three.js 좌표(y 위)로 적었다면 `"world_frame": "threejs_y_up"`을 주면 변환합니다(MuJoCo `(x, y, z)` = Three.js `(x, z, -y)`).
- 방향은 `rotation_cam_to_world`(3×3, 카메라 축을 월드에서 본 열) 또는 `quaternion_wxyz` 중 하나입니다. 축 규약은 `mujoco_camera`(x 오른쪽, y 위, -z 앞, Three.js 카메라와 같음) 또는 `ros_optical`(x 오른쪽, y 아래, z 앞)입니다.
- 렌즈는 `fovy_deg`(정사각 픽셀, 중심 주점) 또는 보정값 `intrinsics: {fx, fy, cx, cy}`(픽셀) 중 하나입니다. 렌즈 왜곡은 다루지 않으므로 필요하면 영상을 먼저 보정합니다.
- 들어온 영상 크기가 파일의 `width`·`height`와 다르면 그 프레임은 백엔드에 보고하지 않습니다(작업자 없음으로 보내지 않음). 계속 다르면 센서가 `DEGRADED`가 되어 관측 공백 규칙이 실행을 막고, 요약의 `calibration_mismatch_frames`에 개수가 남습니다.

### 백엔드 연결: 센서·작업자 스냅샷과 HazardEvent

```bash
python ros_camera_detect.py --topic /cctv_1/image_raw --topic /cctv_2/image_raw \
  --model /path/to/florence-2-base --batch-cameras --num-beams 1 \
  --backend-url http://127.0.0.1:8000 --covered-zone-id <감시 구역 ID> \
  --hazard-zone-id <KEEP_OUT 구역 ID> --robot-id robot-01
```

**센서·작업자 스냅샷** (`--backend-url`, `--covered-zone-id`): 백엔드의 관측 공백·안전거리 검사(`BACKEND_PERCEPTION_SAFETY_ENABLED=1`, API 명세 3.8절)에 쓰입니다.

- 0.5초마다 카메라별 Sensor를 `PUT /sensors/{sensor_id}`로 보냅니다. `last_frame_at`은 마지막으로 처리한 프레임의 수신 시각이고, 2초 넘게 처리한 프레임이 없으면 `DEGRADED`, 노드를 끝내면 `OFFLINE`으로 보냅니다. `covered_zone_ids`에는 `--covered-zone-id`를 넣으며 백엔드에 등록된 구역이어야 합니다.
- 같은 주기로 `PUT /workers/snapshot`에 작업자 목록 전체를 보냅니다. 1초 이내 프레임이 있는 카메라만 쓰고, 서로 다른 카메라가 0.5m 안에서 본 사람은 한 명으로 합칩니다. `observed_at`은 포함한 카메라 프레임 중 가장 오래된 시각이며, 더 새 프레임이 없으면 보내지 않습니다. 아무도 안 보이면 빈 목록을 보냅니다.
- 작업자 `worker_id`는 스냅샷마다 `worker-1`부터 매기는 번호이며 추적 ID가 아닙니다. 좌표계는 `world`, `current_zone_id`는 위치가 속한 구역(없으면 빈 문자열), `safety_status`는 KEEP_OUT 안이면 `DANGER`, 그 밖은 `UNKNOWN`입니다(거리 판정은 백엔드가 합니다). `confidence`는 모델 점수 대신 해당 카메라의 최근 5프레임 중 사람을 본 비율입니다.
- 전송은 별도 스레드에서 최신 값만 보내므로 백엔드가 느려도 추론을 막지 않습니다. 요약에 `backend_feed_sent`·`backend_feed_failures`가 남습니다.

**금지구역 HazardEvent** (`--hazard-zone-id`, `--robot-id`, 둘은 함께 지정):

- 시작할 때 백엔드 `GET /zones`에서 `--hazard-zone-id` 구역을 읽습니다. `KEEP_OUT`, `frame_id=world`, 고리 하나짜리 GeoJSON `Polygon`이어야 합니다. CCTV 위치·방향은 `--scene`(기본 `control/mujoco/models/scenario_scene.xml`)에서 읽으므로 `mujoco` 패키지가 필요하고, 월드에 고정된 카메라만 쓸 수 있습니다.
- 사람 상자의 아래 중앙을 바닥(z=0)에 투영해 금지구역 안인지 판정합니다. **카메라 1대라도 연속 2프레임**에서 금지구역 안 작업자를 보면 `CRITICAL` `WORKER_INTRUSION` HazardEvent를 `POST /hazards`로 한 번 등록합니다(팀 결정 2026-11-05). 진입은 **모든 카메라가 마지막 목격 뒤에도 계속 결과를 내면서** 2초 동안 금지구역 안 작업자를 못 볼 때만 끝난 것으로 봅니다. 카메라가 끊기거나 결과가 늦으면 작업자 부재로 보지 않고 진입을 유지합니다. 진입이 끝난 뒤 다시 들어오면 새 이벤트를 등록합니다(이전 이벤트가 아직 OPEN이면 같은 구역에 여러 건이 쌓일 수 있으며, 이벤트 재사용은 백엔드 조회 API가 필요해 PR #41에서 논의 중입니다).
- `related_robot_ids`에 `--robot-id`를 넣습니다. 스키마 v1.1에 따라 빈 목록은 모든 로봇을 뜻하지 않으므로 막을 로봇을 꼭 지정합니다.
- `CRITICAL`은 센서가 해제할 수 없어서, 작업자가 나가도 운영자가 `POST /hazards/{hazard_id}/resolve`로 해제해야 합니다. 이 노드는 이벤트를 열기만 하고 해제하거나 로봇에 직접 명령하지 않습니다.
- Florence-2 `<OD>`는 점수가 없어 `confidence`에 모델 점수 대신 트리거한 카메라의 최근 5프레임 중 금지구역 안 탐지 비율(탐지 지속 비율)을 넣습니다. 모델 confidence와 의미가 다르므로 해석은 ONT와 정합니다. `source_observation_ids`는 이 프레임들의 결정적 ID이며, 같은 ID가 JSONL 기록의 `hazard.observation_ids`에 남아 원본 탐지로 추적할 수 있습니다. Observation 객체는 아직 백엔드에 등록하지 않습니다.
- 진입마다 고정 `hazard_id`를 정해 보내므로, 응답 타임아웃 뒤 다시 보내도 같은 진입이 두 번 등록되지 않습니다(백엔드의 `409` 중복 ID 응답을 등록 완료로 처리). 등록에 실패하면 로그를 남기고 다음 프레임에서 다시 시도합니다. 기록의 `hazard`에 투영 위치·금지구역 판정·등록된 `hazard_id`가, 요약에 `hazard_ids`·`hazard_post_failures`가 남습니다.
- 미구현: 실행 중 로봇의 즉시 정지. 지금 백엔드는 열린 `CRITICAL` 이벤트로 이후 평가와 실행 직전 재검증을 막지만, 이벤트 등록만으로 이미 실행 중인 행동을 멈추지는 않습니다.

## 가상 CCTV 오프라인 탐지 평가

`control/scripts/threejs_cctv`(PR #45)가 만든 렌더 폴더(`cctv_N/000000.png`, `frames.csv`, `boxes.json`, `cameras.json`)로 탐지율·금지구역 경보·바닥 위치 오차를 잽니다. 탐지기는 PNG와 `cameras.json`의 카메라 정보만 보고, 정답(`frames.csv`, `boxes.json`)은 채점에만 씁니다.

```bash
# 1) 모델 없이 카메라 변환·금지구역 위치 점검 (정답 상자를 탐지 결과로 넣음, CPU)
python offline_eval.py --render-dir ../control/outputs/threejs_cctv --detector truth --output-dir eval/truth
# 2) GPU 노드에서 실제 측정
python offline_eval.py --render-dir <렌더 폴더> --detector florence2 \
  --model-id /abr/coss38/florence-2-base --output-dir eval/florence2 [--batch] [--num-beams 1]
python offline_eval.py --render-dir <렌더 폴더> --detector rtdetr \
  --model-id PekingU/rtdetr_r50vd --output-dir eval/rtdetr
```

- 채점: 카메라마다 보이는 작업자(마스크 픽셀 400 이상)의 정답 상자와 예측 `person` 상자를 IoU 0.5 이상으로 1:1 대응합니다. 정답 상자는 가려진 부분을 뺀 보이는 영역입니다.
- `summary.json`
  - `cameras.<id>.recall`, `recall_in_keep_out`, `precision`, `position_error_m`(대응된 상자의 투영 위치와 정답 위치 거리)
  - `combined_recall`: 어느 카메라든 보이는 작업자를 찾은 비율
  - `keep_out_alarm`: 정답상 금지구역 안에 작업자가 있는 프레임에서 어느 카메라든 투영 위치가 구역 안인 비율(`hit_when_inside`), 아무도 없는 프레임의 오경보 비율(`false_alarm_when_clear`). 연속 2프레임 규칙은 적용하지 않은 프레임 단위 값입니다.
  - `model_call_ms`, `per_camera_hz_model_only`, `per_camera_hz_wall`(PNG 읽기 포함)
- `--detector truth`로 `hit_when_inside`가 1.0에 가깝지 않으면 카메라 정보나 구역 좌표가 어긋난 것이니 모델 측정 전에 확인합니다. 가림으로 발이 안 보이면 정답 상자로도 위치 오차가 생깁니다.
- 미구현: ROS 토픽 실시간 입력. 이 스크립트는 저장된 PNG만 처리하므로 처리율은 카메라 수신·전송 지연을 포함하지 않습니다.

## 연속 추론 속도 측정 (CV-03)

GPU 할당을 받은 터미널에서 아래처럼 실행하면 모델 로드와 워밍업 3회를 제외하고 두 정지 이미지를 순서대로 **60초 이상 반복 추론**합니다. 출력은 `vlm_results/benchmark.json`에 저장됩니다.

```bash
python florence_detect.py \
  outputs/cctv_1_outside.png outputs/cctv_1_inside.png \
  --model /abr/coss38/florence-2-base \
  --output vlm_results \
  --benchmark-seconds 60
```

보고할 값은 `throughput_hz`, `p95_latency_ms`, `completed_iterations`, `gpu`입니다. `below_7hz_fallback_threshold`는 경량 탐지기 전환 검토 신호이며 자동 전환 기능은 아닙니다. 각 반복의 지연도 `latencies_ms`에 남습니다. 측정에는 전처리·GPU 전송·생성·후처리가 포함되지만 **카메라 캡처, 네트워크 전송, 모델 로드, 결과 파일 저장은 포함되지 않습니다.** 정지 이미지 순차 재생이므로 다중 카메라 처리율이나 위험 감지부터 E-Stop까지의 지연을 증명하지 않습니다.

2026-10-16 DIS01에서 수행한 첫 실측 결과는 [`benchmarks/2026-10-16-dis01.md`](benchmarks/2026-10-16-dis01.md)에 기록했습니다. 같은 조건에서 재측정하려면 위 명령의 이미지 경로를 `~/cctv_1_outside.png ~/cctv_1_inside.png`로, 출력 경로를 `~/vlm_results_cv03`으로 바꾸어 실행합니다.

## CPU 검증

```bash
# 저장소 루트에서 실행. 모델과 GPU를 내려받지 않습니다.
python3 -m unittest discover -s mujoco-demo/tests -v
```

검증 범위는 알려진 카메라 위치·회전에서의 바닥 투영, 이미지 해시 불일치 거부, 시간대 검사, 신뢰도 없는 탐지 보류 및 잘못된 신뢰도 거부입니다. 테스트의 점수는 출력 계약을 확인하는 인공 값이며 실제 탐지 신뢰도나 재현율의 근거가 아닙니다. MuJoCo 캡처와 CPU 검증 기록은 [2026-10-30 검토](../docs/cv-review-2026-10-30.md)를 참고하세요.
