# Control (CTL)

ROS 2 제어 어댑터, 안전 경로(HOLD·E-Stop), MuJoCo 시뮬레이션 환경입니다. 기준 문서는 개발계획서 v1.0(실행판) 5-5·5-6장, 부록 A와 `ontology-schema-v1.1.md` 7·9장입니다. 시뮬레이터는 계획서의 Isaac Sim 대신 MuJoCo를 사용합니다.

## 진행 상황

| 순서 | 항목 | 상태 | 확인 명령 |
|---|---|---|---|
| 1~5 | Python·MuJoCo·ROS 2 설치, 작업공간 빌드 | 완료 | `pixi run build` |
| 6~7 | 바퀴 로봇 모델(TIAGo), 전진·회전·정지 | 완료 | `pixi run sim` |
| 8 | ROS 2 브리지 (`/cmd_vel` → MuJoCo → `/odom`) | 완료 | `pixi run cctv-stream` | 카메라 토픽을 웹 콘솔용 MJPEG로 내보냄 (`http://127.0.0.1:8081/stream/cctv_1`). 브리지와 함께 실행. 처음 한 번은 `pixi run build` 후 브리지와 따로 띄운다(동시 빌드 충돌) |
| `pixi run cmd-vel-demo` |
| 9 | Mock 어댑터 (공통 Action, ACK·RESULT, E-Stop·HOLD) | 완료 | `pixi run roundtrip` |
| CT-01 | 브리지 확장: `/joint_states`, 카메라 3대(Image + CameraInfo), TF, 주기 측정 | 완료 | `pixi run measure` |
| CT-02 | 시나리오 장면: 금지구역, A·B 작업대와 상자, CCTV 2대 + 머리 카메라, 작업자 접근 동작 | 완료 | `pixi run snapshot` |
| CT-03 | Backend 승인 명령·RESULT 연동, `proposal_id` 중복 실행 방지 | 완료 | `pixi run test` |
| #54 | MuJoCo ② 디지털 트윈 재구성: 온톨로지 상태(Robot·Worker 스냅샷·Zone)로 장면 생성, 정답 대비 위치 오차 | 완료 | `pixi run test` |

다음 단계는 Backend와 TIAGo 어댑터를 함께 띄운 통합 시나리오 자동화입니다.

## 로봇 모델

| 이름 | 장면 | 용도 |
|---|---|---|
| `scenario` (기본) | `scenario_scene.xml`: TIAGo + 시나리오 장면 (아래 "시나리오 장면") | 시나리오 A·B, CV 영상 입력 |
| `tiago` | `tiago_scene.xml`: TIAGo + 작업자 + CCTV 1대 | TIAGo 단독 시험 |
| `simple` | `diff_drive.xml`: 직접 만든 상자형 바퀴 로봇 | 빠른 시험·디버깅 |

TIAGo(PAL Robotics)는 차동 구동 바퀴 + 7축 팔 + 그리퍼 + 승강 몸통을 가진, 개발계획서 3-3장의 "매니퓰레이터 탑재 모바일 로봇"입니다. `MOVE_TO`·`MANIPULATE`를 한 로봇으로 시연할 수 있습니다.

- TIAGo는 [MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie)의 `pal_tiago`(Apache-2.0, 5.5MB)입니다. 원본 파일은 수정하지 않고 `tiago_scene.xml`에서 불러옵니다.
- 바퀴 반지름 0.0985 m, 바퀴 간격 0.4044 m, 바퀴 속도 제한 ±5 rad/s라서 최대 선속도는 약 0.49 m/s입니다.
- 팔·머리·몸통은 위치 제어로 `home` 자세를 유지합니다.
- 로봇은 `--robot tiago`(시뮬레이션) 또는 `-p robot:=tiago`(브리지)로 바꿉니다.

## 시나리오 장면 (CT-02)

`mujoco/models/scenario_scene.xml` (단위 m, world 좌표, 로봇 시작 위치 = 원점, +x 방향)

| 요소 | 위치·크기 | 비고 |
|---|---|---|
| TIAGo | (0, 0) | `home` 자세 |
| 금지구역 `keep_out_zone` | 중심 (1.2, 0), 1.4 × 1.2 | 빨간 반투명, 충돌 없음. 판정은 하지 않음 |
| A 작업대 + 부품 상자 | (1.2, 1.0), 윗면 높이 0.6 | 상자 6 × 6 × 8 cm, 0.2 kg, freejoint (그리퍼 최대 벌림 9 cm) |
| B 작업대 | (1.2, -1.0), 윗면 높이 0.6 | 상자를 옮겨 놓을 곳 |
| CCTV `cctv_1` | (1.2, -4.2, 3.2) | 작업 셀 중심을 바라봄 (`targetbody`) |
| CCTV `cctv_2` | (4.6, 2.6, 3.0) | 다른 방향에서 작업 셀 중심을 바라봄 |
| 로봇 카메라 `head_camera` | TIAGo 머리 (원본 모델의 카메라) | 머리 관절을 따라 움직임 |
| 작업자 `worker` (mocap) | 경로 (3.4, 1.8) → (1.6, 0.15) 2초 정지 → (3.4, -1.8) → 반복 | 0.6 m/s, 한 주기 약 15초. 금지구역에 들어갔다 나옴 |

작업자는 실사형 사람 모델입니다(MakeHuman CC0 에셋의 피부·옷·머리를 MuJoCo `<skin>`으로 변환, `mujoco/models/worker/`). 부위별 mocap 바디(`worker_pelvis` 등 14개)에 skin이 붙어 있고, `WorkerScript.apply`가 걷기 자세(`worker/walk.npy`)를 이동 거리에 맞춰 재생해 발이 미끄러지지 않습니다. 정지 구간에서는 선 자세로 0.3초 동안 바뀝니다. 모두 mocap이라 `qpos`·keyframe 길이는 바뀌지 않고, 충돌 계산에도 참여하지 않습니다. 기준점 `worker` 바디(발 아래)와 `/sim/worker_pose`는 이전과 같습니다. 스킨을 다시 만드는 방법은 `mujoco/models/worker/README.md`에 있습니다.

작업자 동작은 `scenario.WorkerScript`가 시뮬 시각 기준으로 재생합니다. 경로점·속도는 코드에서 바꿀 수 있고, 브리지에서는 `-p worker_motion:=false`로 끕니다. 실제 위치는 `/sim/worker_pose`, 금지구역 안 여부는 `/sim/worker_in_keep_out`으로 나가므로 CV 탐지 결과와 비교할 수 있습니다.

`pixi run snapshot`은 작업자가 금지구역 안에 있는 시점의 카메라 3대 이미지를 `outputs/snapshots/`에 저장합니다.

## 환경

| 항목 | 버전 |
|---|---|
| ROS 2 | Humble (RoboStack, `ros-base`) |
| MuJoCo | 3.15.0 |
| Python | 3.12 |
| 지원 플랫폼 | macOS Apple Silicon, Ubuntu 22.04 (linux-64) |

[pixi](https://pixi.sh)가 ROS 2, MuJoCo, 빌드 도구를 이 폴더 안(`.pixi/`)에만 설치합니다. 시스템 Python과 ROS 설치에 영향을 주지 않습니다. `requirements.txt`는 pip로 MuJoCo 부분만 설치할 때 씁니다(ROS 2는 pip로 설치되지 않음).

```bash
brew install pixi            # Ubuntu: curl -fsSL https://pixi.sh/install.sh | bash
cd control
pixi install                 # 최초 1회, 약 1GB
```

macOS에서는 같은 Mac 안의 노드끼리도 서로 못 찾는 문제가 있어 `ROS_LOCALHOST_ONLY=1`을 자동으로 설정합니다. 다른 컴퓨터의 노드와 통신하려면 이 값을 해제해야 합니다.

## 실행

| 명령 | 내용 |
|---|---|
| `pixi run test` | 단위 테스트 (어댑터, 안전 게이트, 바퀴 로봇, 시나리오 장면) |
| `pixi run build` | `ros2_ws`를 colcon으로 빌드 |
| `pixi run sim` / `sim-view` | 바퀴 로봇 전진·회전·정지 시험 / 뷰어로 보기 |
| `pixi run bridge` / `bridge-view` | MuJoCo 브리지 실행 (카메라 포함) / 뷰어와 함께 실행 (카메라 끔) |
| `pixi run cmd-vel-demo` | 브리지를 띄우고 `/cmd_vel`로 전진·회전·정지 후 `/odom` 출력 |
| `pixi run measure` | 브리지(물리 + 카메라 3대)를 띄우고 토픽 주기 1분 측정. 인자: `-- 초 카메라Hz 가로 세로` |
| `pixi run snapshot` | 카메라 3대 이미지를 `outputs/snapshots/`에 PNG로 저장 |
| `pixi run mock-adapter` | Mock 어댑터만 실행 |
| `pixi run tiago-adapter` | 공통 Action을 `/cmd_vel`·`/arm/command`로 바꾸는 TIAGo 어댑터 실행 |
| `pixi run roundtrip` | Mock 어댑터 + 하네스 왕복·적합성 시험 (13개 항목) |
| `pixi run arm` / `arm-view` | Panda 로봇 팔 집기·놓기 / 뷰어로 보기 (처음 실행 시 모델 33MB를 내려받음) |
| `pixi run fetch-models` | 저장소에 올리지 않는 모델(Panda)만 내려받기 |

`sim-view`, `bridge-view`, `arm-view`는 macOS에서 `mjpython`으로 실행됩니다.

### 터미널에서 직접 로봇 움직이기

터미널 1 (브리지 + 뷰어):

```bash
pixi run bridge-view
```

터미널 2:

```bash
pixi shell
source ros2_ws/install/setup.bash
ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.3}, angular: {z: 0.5}}"
```

멈추려면 `Ctrl+C`를 누릅니다. 브리지는 `/cmd_vel`이 0.5초 동안 들어오지 않으면 로봇을 세웁니다. 위치는 `ros2 run mujoco_bridge odom_echo` 또는 `ros2 topic echo /odom`으로 확인합니다.

## 토픽

| 토픽 | 방향 | QoS | 내용 |
|---|---|---|---|
| `/cmd_vel` | 외부 → 브리지 | 기본 | `geometry_msgs/Twist` (linear.x m/s, angular.z rad/s) |
| `/odom` | 브리지 → 외부 | 기본, 50Hz | `nav_msgs/Odometry` (`odom` → `base_link`, TF 함께 발행) |
| `/joint_states` | 브리지 → 외부 | 기본, 50Hz | `sensor_msgs/JointState`: 팔 7, 몸통, 머리 2, 그리퍼 2, 바퀴 2 (위치·속도) |
| `/cctv_1/image_raw`, `/cctv_2/image_raw` | 브리지 → 외부 | sensor data, 15Hz | `sensor_msgs/Image` rgb8 640×480, frame_id `camera_cctv_1`·`camera_cctv_2` |
| `/robot/head_camera/image_raw` | 브리지 → 외부 | sensor data, 15Hz | 로봇 머리 카메라, frame_id `camera_head_camera` |
| `.../camera_info` | 브리지 → 외부 | sensor data, 15Hz | 각 카메라의 `CameraInfo` (fovy로 계산한 K·P, 왜곡 없음) |
| `/sim/worker_pose` | 브리지 → 외부 | 기본, 50Hz | `geometry_msgs/PoseStamped` 작업자 실제 위치 (`world`) |
| `/sim/worker_in_keep_out` | 브리지 → 외부 | 기본, 50Hz | `std_msgs/Bool` 작업자가 금지구역 안에 있는지 (실제값) |
| `/safety/approved_action` | 검증 서버 → 어댑터 | Reliable | 승인된 ActionProposal (JSON) |
| `/safety/estop` | 검증 서버·운영자 → 어댑터 | Reliable, latched | `{"active", "triggered_by", "issued_at"}` |
| `/safety/heartbeat` | 검증 서버 → 어댑터 | Reliable, 5Hz | `{"timestamp"}` |
| `/adapter/{robot_id}/result` | 어댑터 → 검증 서버 | Reliable | ACK, RESULT, ESTOP, HOLD 이벤트 (JSON) |
| `/adapter/{robot_id}/state` | 어댑터 → 검증 서버 | Reliable, 10Hz | Robot 객체 상태 (제안 토픽) |

`/safety/*`, `/adapter/*` 메시지 본문은 OpenAPI 확정 전까지 `std_msgs/String`에 JSON으로 담습니다.

### TF

```text
world ─┬─ odom ── base_link ── camera_head_camera   (동적, 머리 관절 반영)
       ├─ camera_cctv_1                             (고정)
       └─ camera_cctv_2                             (고정)
```

카메라 frame은 ROS 광학 좌표계(+z 앞, +x 오른쪽, +y 아래)이고, 이름은 스키마 v1.1 규칙 `camera_<sensor_id>`를 따릅니다. 시뮬레이션에서는 `odom`이 `world`와 같은 위치(로봇 시작점)입니다.

## 브리지 구조와 성능 (CT-01)

- 모든 콜백은 `rclpy.spin` 한 스레드에서 돕니다. MuJoCo 렌더러(OpenGL 컨텍스트)는 이 스레드에서만 만들고 씁니다.
- **물리**: 100Hz 타이머가 시뮬 시각이 실제 시각을 따라가도록 필요한 만큼 스텝합니다(timestep 0.002 s = 500Hz). 10초마다 실시간 대비 배율을 로그로 남깁니다.
- **렌더**: `camera_hz`(기본 15) × 카메라 수의 주기로 한 번에 카메라 한 대씩 돌아가며 렌더합니다. 한 번에 세 대를 그리면 약 30 ms 동안 다른 콜백이 막혀 상태 토픽이 35Hz로 떨어졌습니다.
- `Image.data`에 `bytes`를 넣으면 rclpy가 원소 단위로 변환해 장당 약 70 ms가 걸립니다. `array.array("B", ...)`로 넣어야 합니다 (0.05 ms).
- **뷰어 모드**: 뷰어 창과 카메라 렌더가 같은 OpenGL 자원을 다퉈, 둘 다 켜면 카메라 약 5Hz·`/joint_states` 약 13Hz로 떨어집니다. 그래서 `bridge-view`는 카메라를 끄고(`/joint_states` 약 40Hz), 영상이 필요하면 화면 없는 `bridge`를 씁니다. 뷰어 화면 갱신은 30Hz로 따로 돌립니다.
- 주요 파라미터: `camera_hz`, `image_width`, `image_height`, `cameras`, `worker_motion`, `physics_hz`, `state_hz`, `viewer`.

### 측정 결과 (MacBook Apple Silicon, 2026-10-17, `pixi run measure`, 60초)

| 토픽 | 평균 Hz | p95 Hz | 최대 간격 ms | 판정 (평균 ≥ 10, p95 ≥ 7) |
|---|---:|---:|---:|---|
| `/cctv_1/image_raw` | 14.95 | 14.59 | 125.4 | PASS |
| `/cctv_2/image_raw` | 14.95 | 14.57 | 133.2 | PASS |
| `/robot/head_camera/image_raw` | 14.95 | 14.56 | 127.0 | PASS |
| `/joint_states` | 49.58 | 42.41 | 108.3 | - |
| `/odom` | 49.58 | 42.44 | 108.0 | - |

- 측정 중 시뮬 시각은 실제 시각과 같았습니다 (실시간 대비 1.00배).
- `ros2 topic hz`로도 카메라 3개 15.0Hz, `/joint_states`·`/odom` 49.9Hz를 확인했습니다.
- p95(Hz)는 메시지 간격의 95번째 백분위를 주기로 바꾼 값입니다.
- `camera_hz:=30`으로 올리면 카메라는 약 25Hz까지 나오지만 `/joint_states`가 33Hz로 떨어집니다. 기본값 15Hz를 권장합니다.
- 주기가 부족한 환경(예: 저사양 PC, GPU 서버의 소프트웨어 렌더링)에서는 `image_width:=320 image_height:=240` 또는 `camera_hz:=10`으로 낮추고, CV 쪽에서 쓰지 않는 카메라는 줄입니다.

### ros2_control 계열 패키지 검토

RoboStack Humble에 `ros-humble-mujoco-ros2-control` 0.0.3(osx-arm64·linux-64)과 `ros2_control`·`diff_drive_controller`·`joint_state_broadcaster`가 있습니다. 다만 0.0.3은 초기 버전이고, 쓰려면 URDF에 `ros2_control` 태그와 컨트롤러 설정을 새로 만들어야 하며 MuJoCo 3.15.0과의 호환도 확인되지 않았습니다. 카메라 발행·작업자 스크립트·안전 경로는 어차피 직접 구현해야 하므로 **직접 구현한 브리지를 유지**합니다. MoveIt 연동이 필요해지면 다시 검토합니다.

## MuJoCo ② 디지털 트윈 (#54)

`mujoco/twin.py`는 온톨로지 상태만으로 사전 시뮬레이션용 MuJoCo 장면을 만듭니다. MuJoCo ①(`scenario_scene.xml`)의 정답 좌표는 읽지 않습니다.

```bash
python mujoco/twin.py --state state.json --xml outputs/twin.xml          # 제어 계약 13.1 state 형식
python mujoco/twin.py --backend http://127.0.0.1:8000 --workers snapshot.json   # GET /robots, GET /zones + Worker 스냅샷
```

- 로봇: 종류별 원기둥(`MOBILE`은 반경 0.30 m, 높이 1.10 m)과 평면 이동 관절(`robot:<id>:x`, `:y`, `:yaw`). 사전 시뮬레이션(#53)이 이 관절을 움직입니다.
- 작업자: 반경 0.25 m + CV 위치 오차 여유 0.20 m 캡슐(mocap).
- Zone: 볼록 껍질 바닥 판, 충돌 없음. GeoJSON `Polygon/coordinates`와 `polygon/points`를 모두 받습니다.
- `frame_id`는 `world`만 받습니다. Worker 스냅샷이 없으면 빈 현장으로 가정하지 않고 `TwinError`로 거부합니다(Fail-Closed). 빈 목록(`items: []`)은 "작업자 없음"으로 받습니다.
- `position_errors()`로 트윈 위치와 MuJoCo ① 정답 위치의 거리, 미탐(`missing`)·오탐(`extra`)을 잽니다.

적용 범위와 한계(ONT 리뷰 반영):

- **작업자 여유 반경 0.20 m**: CV 바닥 투영 오차의 **중앙값**(2026-11-05 측정 0.15~0.20 m)을 바탕으로 정한 MVP 초기값입니다. 오차가 이 값을 넘는 경우가 있으므로 안전이 보장되는 오차 상한으로 해석하지 않습니다.
- **로봇 충돌 형상**: 종류별 원기둥은 사전 시뮬레이션용으로 단순화한 모델입니다. TIAGo의 팔·관절 자세를 반영하지 않으므로 팔이 뻗은 상태의 정밀 충돌 검사로 쓰지 않습니다.
- **접촉 감지와 `SAFETY_DISTANCE`**: 트윈 장면의 접촉은 사전 시뮬레이션의 단순 접촉 감지일 뿐이며, Safety Gate의 `SAFETY_DISTANCE` 판정을 대신하지 않습니다. COLLISION 판정 기준과 시간 제한은 #50에서 정하고, 안전 판정 연결은 #53에서 합니다.
- **Worker 스냅샷 최신성**: 이 모듈은 스냅샷이 없을 때만 거부하고 `observed_at`이 오래되었는지는 검사하지 않습니다. 최신성 검사와 Fail-Closed 처리는 #53 사전 시뮬레이션 판정에서 합니다.
- 미구현: Backend에 Worker 조회 API(`GET /workers/snapshot`)가 없어 `--backend` 모드에서는 Worker 스냅샷을 파일로 넘깁니다. 작업자 이동 예측, 작업대·상자 등 PhysicalObject 반영도 아직 없습니다.

## 어댑터 동작 규칙

- `status=APPROVED`가 아닌 제안은 ACK 없이 `NOT_APPROVED`로 거부합니다.
- 수신 즉시 ACK를 보내고, 완료·실패 시 RESULT를 보냅니다.
- 같은 `proposal_id`와 같은 명령을 다시 받으면 동작을 반복하지 않고 기존 ACK 또는 최종 RESULT를 재전송합니다.
- 지원하지 않는 Action(Mock은 `MANIPULATE`)은 `UNSUPPORTED`로 응답합니다.
- 속도는 `min(로봇 max_speed, proposal.speed_limit, parameters.speed_limit)`로 제한합니다. 기본값은 SR-01의 0.4 m/s입니다.
- `/safety/estop`의 `active=true`를 받으면 즉시 `ESTOP`으로 바꾸고 실행 중인 제안을 중단합니다. 해석할 수 없는 E-Stop 메시지도 정지로 처리합니다.
- 하트비트가 1초(5주기) 동안 없으면 스스로 `HOLD`에 들어갑니다(`triggered_by=HEARTBEAT_LOSS`).
- 정지 후 복귀는 E-Stop 해제와 하트비트 정상 상태에서 승인된 `RESUME`으로만 가능합니다.

## ONT 담당과 협의할 점

1. **토픽 이름의 robot_id**: ROS 2 토픽 이름에는 `-`를 쓸 수 없어 `robot-01`은 `/adapter/robot_01/result`가 됩니다. robot_id 규칙을 영숫자와 `_`로 정하거나, 이 변환 규칙을 인터페이스 명세에 넣어야 합니다.
2. **사유 코드 추가**: 스키마 v1.1의 `UNSUPPORTED`·`TIMEOUT`·`HW_ERROR` 외에 어댑터에서 `NOT_APPROVED`, `INVALID`, `ROBOT_BUSY`, `ROBOT_HELD`, `NO_HEARTBEAT`, `CANCELLED`, `ESTOP`을 쓰고 있습니다.
3. **상태 토픽**: `/adapter/{robot_id}/state`는 부록 A에 없는 토픽입니다. Robot 객체 갱신 경로로 쓸지 정해야 합니다.

## 폴더 구조

```text
control/
├── mujoco/
│   ├── models/
│   │   ├── scenario_scene.xml      # 시나리오 장면 (기본): TIAGo, 금지구역, A·B 작업대, 상자, CCTV 2대, 작업자
│   │   ├── tiago_scene.xml         # TIAGo + 작업자 + CCTV 1대
│   │   ├── pal_tiago/              # MuJoCo Menagerie TIAGo 모델 (Apache-2.0, 5.5MB)
│   │   ├── diff_drive.xml          # 단순 시험용 바퀴 로봇
│   │   ├── panda_scene.xml         # Panda 로봇 팔 + 작업자 + CCTV 장면
│   │   └── franka_emika_panda/     # Panda 모델 (33MB, 저장소 제외 → fetch_models.sh로 받음)
│   ├── simulation.py               # 차동 구동 시뮬레이션 (DiffDriveSim, 로봇 목록 ROBOTS)
│   ├── scenario.py                 # 작업자 접근 동작, 카메라 렌더·내부 파라미터, 관절 목록, 스냅샷
│   ├── twin.py                     # MuJoCo ② 디지털 트윈: 온톨로지 상태 → 장면, 정답 대비 위치 오차
│   └── manipulator.py              # Panda move_to·그리퍼·pick_and_place
├── ros2_ws/src/
│   ├── mujoco_bridge/              # ROS 2 ↔ MuJoCo 브리지, odom_echo, rate_monitor, cctv_stream(웹 MJPEG)
│   ├── robot_adapter/              # 공통 Action → 로봇 명령 (Mock 어댑터)
│   └── safety_control/             # HOLD·E-Stop 판단(gate), 토픽·QoS, 시험 하네스
├── tests/                          # pytest 단위 테스트
├── scripts/                        # cmd_vel_demo.sh, roundtrip.sh, measure_rates.sh, fetch_models.sh
├── pixi.toml, pixi.lock            # 환경 정의 (macOS·Ubuntu 공용)
├── requirements.txt
└── README.md
```
