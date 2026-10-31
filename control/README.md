# Control (CTL)

ROS 2 제어 어댑터, 안전 경로(HOLD·E-Stop), MuJoCo 시뮬레이션 환경입니다. 기준 문서는 개발계획서 v1.0(실행판) 5-5·5-6장, 부록 A와 `ontology-schema-v1.1.md` 7·9장입니다. 시뮬레이터는 계획서의 Isaac Sim 대신 MuJoCo를 사용합니다.

## 진행 상황

| 순서 | 항목 | 상태 | 확인 명령 |
|---|---|---|---|
| 1~5 | Python·MuJoCo·ROS 2 설치, 작업공간 빌드 | 완료 | `pixi run build` |
| 6~7 | 바퀴 로봇 모델(TIAGo), 전진·회전·정지 | 완료 | `pixi run sim` |
| 8 | ROS 2 브리지 (`/cmd_vel` → MuJoCo → `/odom`) | 완료 | `pixi run cmd-vel-demo` |
| 9 | Mock 어댑터 (공통 Action, ACK·RESULT, E-Stop·HOLD) | 완료 | `pixi run roundtrip` |
| CT-01 | 브리지 확장: `/joint_states`, 카메라 3대(Image + CameraInfo), TF, 주기 측정 | 완료 | `pixi run measure` |
| CT-02 | 시나리오 장면: 금지구역, A·B 작업대와 상자, CCTV 2대 + 머리 카메라, 작업자 접근 동작 | 완료 | `pixi run snapshot` |
| CT-03·07 | TIAGo 어댑터: 승인된 `MOVE_TO`로 실제 이동, E-Stop·HOLD 시 실제 정지 | 완료 | `pixi run roundtrip-tiago`, `pixi run stop-test` |
| CT-07 조작 | TIAGo 집기·놓기 (`MANIPULATE` PICK·PLACE), 시나리오 A (상자 A → B) | 완료 | `pixi run pick-place`, `pixi run scenario-a` |

다음 단계는 로봇 상태의 온톨로지 동기화(CT-04)와 시나리오 반복 시험 하네스(CT-05)입니다.

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
| A 작업대 + 부품 상자 | 작업대 (1.2, 1.0), 윗면 높이 0.6 / 상자 (1.2, 0.82) | 상자 2.5 × 2.5 × 8 cm, 0.1 kg, freejoint. 로봇 쪽 모서리에서 7 cm 안쪽 (그리퍼 최대 벌림 4.2 cm) |
| B 작업대 | (1.2, -1.0), 윗면 높이 0.6 | 상자를 옮겨 놓을 곳 |
| CCTV `cctv_1` | (1.2, -4.2, 3.2) | 작업 셀 중심을 바라봄 (`targetbody`) |
| CCTV `cctv_2` | (4.6, 2.6, 3.0) | 다른 방향에서 작업 셀 중심을 바라봄 |
| 로봇 카메라 `head_camera` | TIAGo 머리 (원본 모델의 카메라) | 머리 관절을 따라 움직임 |
| 작업자 `worker` (mocap) | 경로 (3.4, 1.8) → (1.6, 0.15) 2초 정지 → (3.4, -1.8) → 반복 | 0.6 m/s, 한 주기 약 15초. 금지구역에 들어갔다 나옴 |

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
| `pixi run roundtrip` | Mock 어댑터 + 하네스 왕복·적합성 시험 (13개 항목) |
| `pixi run roundtrip-tiago` | 같은 하네스를 MuJoCo 브리지 + TIAGo 어댑터로 실행 (하네스 코드는 그대로) |
| `pixi run stop-test` | TIAGo 실제 정지 지연 시험: E-Stop·하트비트 손실을 번갈아 6회, `/odom`으로 측정 |
| `pixi run tiago-adapter` | TIAGo 어댑터만 실행 (브리지가 먼저 떠 있어야 함) |
| `pixi run pick-place` / `pick-place-view` | 시나리오 A 집기·놓기 (ROS 없이) / 뷰어로 보기 |
| `pixi run scenario-a` | 시나리오 A를 ROS 2 전체 경로로 시험: PICK 중 E-Stop → 복귀 → PICK → PLACE (7개 항목) |
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
| `/arm/command` | 어댑터 → 브리지 | 기본 | 집기·놓기 명령 (JSON): `command_id`, `manipulation`(PICK·PLACE·CANCEL), `object_id`, `target_pose`, `approach_yaw` |
| `/arm/result` | 브리지 → 어댑터 | 기본 | 집기·놓기 결과 (JSON): `command_id`, `success`, `reason_code`, `message`, `object_position` |
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

## 어댑터 동작 규칙

- `status=APPROVED`가 아닌 제안은 ACK 없이 `NOT_APPROVED`로 거부합니다.
- 수신 즉시 ACK를 보내고, 완료·실패 시 RESULT를 보냅니다.
- 지원하지 않는 Action(Mock은 `MANIPULATE`)은 `UNSUPPORTED`로 응답합니다.
- 속도는 `min(로봇 max_speed, proposal.speed_limit, parameters.speed_limit)`로 제한합니다. 기본값은 SR-01의 0.4 m/s입니다.
- `/safety/estop`의 `active=true`를 받으면 즉시 `ESTOP`으로 바꾸고 실행 중인 제안을 중단합니다. 해석할 수 없는 E-Stop 메시지도 정지로 처리합니다.
- 하트비트가 1초(5주기) 동안 없으면 스스로 `HOLD`에 들어갑니다(`triggered_by=HEARTBEAT_LOSS`).
- 정지 후 복귀는 E-Stop 해제와 하트비트 정상 상태에서 승인된 `RESUME`으로만 가능합니다.

## TIAGo 어댑터 (CT-03·07 이동)

`robot_adapter/tiago_adapter_node.py` + `diff_drive_core.py`. Mock 어댑터와 같은 토픽·규약(APPROVED만 실행, ACK·RESULT, E-Stop·HOLD, 2단계 복귀)을 그대로 쓰고, 브리지와는 `/odom`(위치 입력)·`/cmd_vel`(이동 명령)으로 연결됩니다.

```text
검증 서버 ─ /safety/approved_action·/safety/estop·/safety/heartbeat ─→ tiago_adapter ─ /cmd_vel ─→ 브리지 ─→ MuJoCo TIAGo
                                                                      ↑──────────── /odom ─────────────┘
```

- **MOVE_TO 제어기**: 목표 방향과 50° 이상 어긋나면 제자리 회전, 그 안이면 전진하며 방향 보정, 목표 5 cm 안에서 정지·성공 보고. 속도는 `min(로봇 max_speed, proposal.speed_limit, parameters.speed_limit)`, 회전은 0.8 rad/s 이하. 직선 경로만 따라가며 장애물 회피는 하지 않습니다.
- **TIMEOUT**: 예상 이동 시간의 2배 + 10초 안에 도착하지 못하면 `TIMEOUT`.
- **HW_ERROR**: `/odom`이 0.5초 넘게 끊기면 이동을 거부하거나 멈추고 `ERROR`. `/odom`이 돌아오면 RESUME으로 복귀.
- **정지 유지**: `ESTOP`·`HOLD`·`ERROR` 동안 매 틱(50Hz) 0 속도를 보내 다른 `/cmd_vel`을 덮어씁니다. E-Stop 수신 콜백에서도 즉시 0을 보냅니다.
- `MANIPULATE`(PICK·PLACE)는 브리지의 `/arm/command`로 넘기고 `/arm/result`를 RESULT로 보고합니다 (아래 "TIAGo 집기·놓기"). PICK·PLACE가 아닌 조작 종류는 `UNSUPPORTED`입니다.

### 시험 결과 (2026-10-30, MacBook)

- `pixi run roundtrip-tiago`: Mock과 **같은 하네스 코드**로 13/13 통과. MOVE_TO는 실제로 0.4 m 이동해 오차 5 cm 안에 도착했습니다. (시나리오 C의 "어댑터만 교체" 방식을 미리 확인한 셈입니다.)
- `pixi run stop-test` (TIAGo가 약 0.3 m/s로 달리는 중에 정지 신호):

| 신호 | 어댑터 반응 | 실제 정지까지 (평균 / 최대) | 신호 후 밀린 거리 |
|---|---:|---:|---:|
| E-Stop (`/safety/estop`) | 0.3 ms | 107 ms / 141 ms | 최대 0.9 cm |
| 하트비트 손실 | 1초 대기 후 HOLD | 1087 ms / 1181 ms | 최대 41.5 cm |

정지 판정은 `/odom` 속도 < 0.02 m/s이며, `/odom`이 50Hz라 측정값에 최대 20 ms 오차가 있습니다. KPI 2(CRITICAL → E-Stop 발행 ≤ 100 ms)는 발행까지의 지연이므로 어댑터 반응(0.3 ms)이 해당하고, 실제 정지 시간은 로봇 감속을 포함한 참고값입니다.

## TIAGo 집기·놓기 (CT-07 조작)

`mujoco/tiago_manipulation.py` (ROS 없이 동작) + 브리지의 `/arm/command`·`/arm/result`.

```text
검증 서버 ─ MANIPULATE(PICK, box-01) ─→ tiago_adapter ─ /arm/command ─→ 브리지 ─→ TiagoManipulator
                                          ↑ RESULT ←─ /arm/result ←────────┘   (베이스 이동 + IK + 그리퍼)
```

**동작 순서**
- PICK: 접근 방향 선택 → 베이스 이동(물체에서 0.62 m) → 그리퍼 열기 → 물체 12 cm 앞 → 잡는 위치 → 닫기 → 10 cm 들기 → 운반 자세
- PLACE: 베이스 이동 → 놓을 위치 10 cm 위 → 내려놓기 → 그리퍼 열기 → 뒤로 빼기 → 운반 자세
- 접근 방향은 0°·90°·180°·270° 중 베이스가 작업대와 겹치지 않고, 물체가 작업대 모서리에서 가장 가까운 쪽을 자동으로 고릅니다. `parameters.approach_yaw`로 직접 줄 수도 있습니다.
- PICK의 물체 위치는 시뮬레이션의 실제 위치(`object_id` → MuJoCo body, 브리지 `objects` 파라미터)를 씁니다. 실제 시스템에서는 인식 결과(Observation)로 바꿀 부분입니다.
- 결과 확인: PICK은 물체가 5 cm 이상 들렸는지, PLACE는 목표 5 cm 안에 세워졌는지 보고 `HW_ERROR`로 실패를 알립니다. IK로 닿지 않는 위치·모르는 물체는 `UNSUPPORTED`입니다.

**정지·안전**
- 동작은 제너레이터로 나뉘어 브리지의 물리 타이머가 한 단계씩 진행합니다. 그래서 집는 도중에도 `/odom`·카메라 발행이 멈추지 않습니다.
- IK(한 번에 약 0.15초)는 별도 스레드에서 풀고, 그동안 팔은 제자리를 유지합니다.
- E-Stop·HOLD·STOP·TIMEOUT이면 어댑터가 `CANCEL`을 보내고, 브리지는 동작을 버리고 베이스를 세웁니다. 팔은 그 자리에 멈추고 그리퍼는 그대로라 들고 있던 물체를 떨어뜨리지 않습니다.
- 집기·놓기 중에는 브리지가 `/cmd_vel`을 무시합니다 (동작이 베이스를 직접 움직임).

**TIAGo 모델 보정 (원본 파일은 수정하지 않음)**
- 손가락 메시의 볼록 껍질(convex hull) 안쪽 면이 쐐기처럼 기울어, 닫을 때 물체가 손끝으로 밀려 빠졌습니다. 그래서 불러올 때 `MjSpec`으로 손가락 패드(얇은 상자)를 붙이고 메시는 보이기만 하게 바꿉니다 (`simulation.add_tiago_finger_pads`).
- 그리퍼를 다 벌려도 손가락 사이가 4.2 cm라 상자 폭을 2.5 cm로 정했습니다.
- 손을 수평으로 뻗을 수 있는 거리는 작업대 높이에서 베이스 중심부터 0.45~0.80 m입니다. 그래서 0.62 m 떨어져 집습니다.
- 팔이 중력으로 약 8 mm 처지므로 물체 중심보다 2 cm 위를 잡습니다 (손가락이 작업대에 끌리지 않게).

**시험 결과 (2026-10-30)**
- `pixi run pick-place`: 상자를 0.84 m까지 들어 B 작업대 목표 1.6 cm 안에 세워 놓음 (시뮬 약 39초, 계산 2.6초)
- `pixi run scenario-a` (ROS 2 전체 경로) 7/7 통과: PICK 시작 → 베이스가 움직이기 시작할 때 E-Stop → RESULT `ESTOP`, 로봇 실제 정지, 브리지 동작 취소 → 해제·RESUME → PICK 성공(20 cm 들어 올림) → PLACE 성공(오차 1.0 cm)

## ONT 담당과 협의할 점

1. **토픽 이름의 robot_id**: ROS 2 토픽 이름에는 `-`를 쓸 수 없어 `robot-01`은 `/adapter/robot_01/result`가 됩니다. robot_id 규칙을 영숫자와 `_`로 정하거나, 이 변환 규칙을 인터페이스 명세에 넣어야 합니다.
2. **사유 코드 추가**: 스키마 v1.1의 `UNSUPPORTED`·`TIMEOUT`·`HW_ERROR` 외에 어댑터에서 `NOT_APPROVED`, `INVALID`, `ROBOT_BUSY`, `ROBOT_HELD`, `NO_HEARTBEAT`, `CANCELLED`, `ESTOP`을 쓰고 있습니다.
3. **상태 토픽**: `/adapter/{robot_id}/state`는 부록 A에 없는 토픽입니다. Robot 객체 갱신 경로로 쓸지 정해야 합니다.
4. **하트비트 손실 시 이동 거리**: 스키마 9장의 1초(5주기) 기준이면 0.3~0.4 m/s로 달리던 로봇은 정지까지 약 30~40 cm를 더 갑니다. 작업자 안전거리(1.2 m)·비상정지 반경(0.6 m)과 비교해 이 기준이 충분한지 확인이 필요합니다.

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
│   ├── tiago_manipulation.py       # TIAGo IK·그리퍼·베이스 이동, PICK·PLACE, 시나리오 A
│   └── manipulator.py              # Panda move_to·그리퍼·pick_and_place
├── ros2_ws/src/
│   ├── mujoco_bridge/              # ROS 2 ↔ MuJoCo 브리지, odom_echo, rate_monitor
│   ├── robot_adapter/              # 공통 Action → 로봇 명령 (Mock 어댑터, TIAGo 어댑터)
│   └── safety_control/             # HOLD·E-Stop 판단(gate), 토픽·QoS, 시험 하네스, 정지 지연·시나리오 A 시험
├── tests/                          # pytest 단위 테스트
├── scripts/                        # cmd_vel_demo.sh, roundtrip.sh, stop_test.sh, scenario_a.sh, measure_rates.sh, fetch_models.sh
├── pixi.toml, pixi.lock            # 환경 정의 (macOS·Ubuntu 공용)
├── requirements.txt
└── README.md
```
