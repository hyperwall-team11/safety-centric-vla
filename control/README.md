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
| #53 | MuJoCo ② 사전 시뮬레이션 서비스 `POST /twin/simulate`: `MOVE_TO` 경로 접촉·안전거리·목표 도달 판정 (판정 수치는 #50 확정 전 잠정값) | 완료 | `pixi run test`, `pixi run twin-sim` |
| #95 | 로봇 팔 공통 어댑터(UR5e·UR10e·iiwa14·Panda), 창고형 적재·포장 구역, `MANIPULATE` 사전 시뮬레이션 (요청 형식 잠정) | 완료 | `pixi run arms-demo`, `pixi run test` |
| AMR | 이동형 운반 로봇(AMR) 2대: 창고 ↔ TIAGo 작업 셀 팔레트 운반, `MOVE_TO`·`MANIPULATE`(싣기·내리기) 어댑터, 사전 시뮬레이션 차체 크기에 실린 팔레트 반영 | 완료 | `pixi run amr-demo`, `pixi run test` |
| #55 | Three.js CCTV 실시간 발행: 브리지 MuJoCo 상태 → Three.js 렌더 → `/cctv_{n}/image_raw`(stamp = 시뮬레이션 시각) | 완료 (카메라당 15Hz) | `pixi run bridge-threejs` + `pixi run cctv-threejs` |

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
| `pixi run bridge-threejs` / `cctv-threejs` | CCTV를 Three.js로 렌더하는 브리지 / 렌더러 (#55) |
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

## Three.js CCTV 실시간 발행 (#55)

브리지를 `cctv_renderer:=threejs`로 띄우면 고정 CCTV(`cctv_1`, `cctv_2`)를 MuJoCo 렌더 대신 Three.js(#45) 렌더로 발행합니다. 로봇 머리 카메라는 그대로 MuJoCo가 렌더합니다.

```bash
cd scripts/threejs_cctv && npm install && python fetch_assets.py   # 처음 한 번 (에셋은 커밋하지 않음)
pixi run bridge-threejs     # 터미널 1: 브리지 (카메라 15Hz, 장면을 outputs/threejs_live/data에 씀)
pixi run bridge-threejs-plant   # 또는: 공장 장면(plant_scene.xml, 작업자 3명, 카메라 10Hz)
pixi run cctv-threejs       # 터미널 2: 헤드리스 Chromium 렌더러, 5초마다 처리율 출력
```

1. 브리지가 시작할 때 자기 장면(기본 `scenario_scene.xml`)을 `scene.json`·텍스처로 내보냅니다. 카메라 위치·방향·fovy는 장면의 `cctv_1`, `cctv_2`와 같습니다.
2. `camera_hz`마다 MuJoCo 상태(geom 자세, 작업자 skin 정점)를 한 프레임으로 묶어 `http://127.0.0.1:8765/frame`에 둡니다(`threejs_live.py`). 프레임 형식은 오프라인 `export_scene.py`의 `FramePacker`와 같습니다.
3. 렌더러(`node render.mjs --ros`)가 새 프레임을 롱폴링으로 받아 카메라마다 렌더하고, 오프라인 영상과 같은 CCTV 효과를 거친 JPEG(품질 0.72)를 돌려보냅니다. 브리지가 rgb8로 풀어 발행합니다. JPEG 인코딩은 다음 카메라 렌더와, 전송은 다음 프레임 렌더와 겹쳐 돌립니다.
4. 브리지는 그 영상을 **렌더한 프레임의 시뮬레이션 시각**을 `header.stamp`로 붙여 발행합니다. 렌더가 밀리면 중간 프레임은 건너뛰고 최신 프레임만 렌더합니다.

- 렌더러가 멈추면 영상이 끊기고, Backend는 `OBSERVATION_GAP`(2초)으로 실행을 거부합니다(제어 계약 3장).
- 같은 ROS 도메인에서 브리지를 둘 띄우면 같은 카메라 토픽에 두 영상이 섞입니다. 브리지는 5초마다 자기 카메라 토픽의 발행자 수를 확인해, 다른 발행자가 있으면 오류 로그를 남깁니다. 하나만 띄우거나 `ROS_DOMAIN_ID`를 나눕니다.
- `plant_scene.xml`을 `model_path`로 주면 작업자 2·3(`worker_2`, `worker_3` mocap)도 오프라인 영상과 같은 경로로 함께 렌더합니다.

처리율 (Apple M3, 별도 `ROS_DOMAIN_ID`, 2026-11-15 측정, 물리는 모든 경우 실시간 대비 1.00배):

| 장면 | `camera_hz` | 카메라당 발행 | 프레임당 시간 (카메라 2대) |
|---|---|---|---|
| `scenario_scene.xml` | 10 | 10.0 Hz | |
| `scenario_scene.xml` | 15 | **15.0 Hz** | |
| `scenario_scene.xml` | 20 | 약 12~13 Hz | 렌더 약 35 ms, 다음 프레임 대기 약 30 ms |
| `plant_scene.xml` (작업자 3명) | 10 | **10.0 Hz** | 자세 10 ms, 렌더 33 ms |
| `plant_scene.xml` (작업자 3명) | 15 | 약 11 Hz | 자세 17 ms, 렌더 45 ms |

`bridge-threejs`는 15Hz, `bridge-threejs-plant`는 10Hz로 띄웁니다. 그보다 높이면 렌더(GPU 제출)와 브리지 쪽 프레임 준비가 한계입니다. 렌더러는 5초마다 단계별 시간을 출력합니다.

### 로봇 팔·AMR이 있는 현장

브리지의 `arms:=true`·`amrs:=true`와 함께 쓰면 팔 4대·랙·팔레트·포장대·AMR 2대·거치대도 Three.js 영상에 나옵니다. 프레임은 모든 geom의 자세를 담으므로, 어댑터가 움직이는 팔 관절·AMR 차체·실린 팔레트가 그대로 따라 움직입니다.

```bash
pixi run bridge-threejs-amrs    # 터미널 1: 브리지 (plant_scene + AMR 2대, Three.js CCTV 5Hz)
pixi run cctv-threejs           # 터미널 2: 렌더러
pixi run amr-adapters           # 터미널 3: AMR 어댑터
pixi run amr-cycle-demo         # 터미널 4: 순환 데모 (검증 서버 대역, 시험 전용)
# 팔까지: 브리지에 -p arms:=true를 더하고 pixi run arm-adapters, pixi run arm-cycle-demo
```

- 오프라인 내보내기(`scripts/threejs_cctv/export_scene.py`)도 기본(`--plant full`)으로 팔 4대 + AMR 2대가 든 장면을 내보냅니다. ROS 없이 `mujoco/plant_demo.py`가 같은 궤적 계획·AMR 운동학으로 팔은 집기·놓기, AMR은 싣기 → 이동 → 내리기를 반복합니다. 예전 장면은 `--plant bare`입니다.
- 팔·랙·AMR은 Three.js 설비 모델(`prop_*`)로 바꾸지 않고 MuJoCo 형상(팔은 Menagerie 메시)과 색 그대로 그립니다.
- 바닥 라이트맵(`floor_bake.png`)은 이 구역이 없던 장면으로 구웠습니다. 구운 바닥을 쓰면 랙·받침대처럼 움직이지 않는 물체는 그림자가 없으므로, 새 장면에서는 다시 굽거나 `?nobake`로 봅니다.
- 팔 4대를 켜면 브리지 물리가 실시간 대비 약 0.3배로 느려집니다(Apple M3, 2026-11-14). 이때 AMR `/odom` 간격이 0.5초를 넘어 AMR 어댑터가 `ERROR`가 될 수 있어, AMR 확인은 `amrs:=true`만 켜고 했습니다(실시간 대비 1.00배, Three.js 카메라당 5Hz).

## 토픽

| 토픽 | 방향 | QoS | 내용 |
|---|---|---|---|
| `/cmd_vel` | 외부 → 브리지 | 기본 | `geometry_msgs/Twist` (linear.x m/s, angular.z rad/s) |
| `/odom` | 브리지 → 외부 | 기본, 50Hz | `nav_msgs/Odometry` (`odom` → `base_link`, TF 함께 발행) |
| `/joint_states` | 브리지 → 외부 | 기본, 50Hz | `sensor_msgs/JointState`: 팔 7, 몸통, 머리 2, 그리퍼 2, 바퀴 2 (위치·속도) |
| `/cctv_1/image_raw`, `/cctv_2/image_raw` | 브리지 → 외부 | sensor data, 15Hz | `sensor_msgs/Image` rgb8 640×480, frame_id `camera_cctv_1`·`camera_cctv_2`. `cctv_renderer:=threejs`이면 Three.js 렌더 영상, stamp는 시뮬레이션 시각 |
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
- **Worker 스냅샷 최신성**: 이 모듈은 스냅샷이 없을 때만 거부하고 `observed_at`이 오래되었는지는 검사하지 않습니다. 최신성 검사와 Fail-Closed 처리는 사전 시뮬레이션 서비스(`twin_simulate.py`)가 합니다.

## MuJoCo ② 사전 시뮬레이션 서비스 (#53)

`mujoco/twin_simulate.py`는 제어 계약 13장 `POST /twin/simulate`를 구현합니다. Backend가 보낸 후보 행동과 상태로 트윈을 만들고, 로봇을 목표까지 움직여 본 결과만 돌려줍니다. 승인 판정과 감사 기록은 Backend가 합니다.

```bash
pixi run twin-sim                                          # http://127.0.0.1:8100/twin/simulate
python mujoco/twin_simulate.py --request req.json          # 서버 없이 요청 한 건 판정
```

- 지원 범위: `MOVE_TO`와 `MANIPULATE`(#95, 아래 "로봇 팔")를 받습니다. 다른 `action_type`은 `ERROR`입니다.
- 동작: 로봇을 현재 위치에서 `target_pose`까지 `speed_limit`으로 직선 이동시키며 0.01 s마다 MuJoCo 접촉을 검사합니다. 작업자는 스냅샷 위치에 멈춰 있다고 가정합니다(이동 예측 없음).
- `result` 우선순위: `COLLISION`(이동하는 로봇이 여유 반경 포함 작업자 캡슐이나 다른 로봇과 접촉) → `UNSAFE_DISTANCE`(몸 표면 사이 거리가 `UNSAFE_DISTANCE_M` 미만) → `GOAL_NOT_REACHED`(최대 구간 안에 목표 0.05 m 이내 미도달) → `PASS`.
- `min_worker_distance_m`은 로봇과 작업자 **몸 표면 사이** 최소 거리입니다(CV 여유 반경 제외). 작업자가 없으면 `null`, 접촉이 없으면 `first_contact_at_s`가 `null`입니다. `snapshot_id`는 요청 값을 그대로 돌려줍니다.
- `ERROR`(Fail-Closed): `contract_version` 불일치, `snapshot_id`·`snapshot_at` 누락, Worker 스냅샷 없음, 스냅샷이 오래됨(`snapshot_at - observed_at > 2 s`) 또는 `observed_at`이 0.5 s 넘게 미래, 대상 로봇 없음, `speed_limit <= 0`, `world`가 아닌 좌표계. `ERROR` 응답에는 원인 `detail`이 붙습니다. JSON이 아닌 본문은 HTTP 400입니다.
- `robot_type`이 없는 로봇(계약 13.1 예시)은 가장 큰 반경의 형상으로 보수적으로 둡니다.

| 상수 | 잠정값 | 근거 |
|---|---|---|
| `SNAPSHOT_MAX_AGE_S` | 2.0 s | 스키마 v1.1 Sensor `last_frame_at` 2초 무수신 → `DEGRADED` |
| `UNSAFE_DISTANCE_M` | 0.6 m | 스키마 v1.1 Zone `estop_distance` 기본값 |
| `MAX_HORIZON_S` | 30 s | 시연 장면 크기 기준 |

위 값은 #50(ONT 판정 기준·시간 제한)이 확정되기 전의 **잠정값**입니다. 확정되면 상수를 바꿉니다. 요청 시간 제한과 시간 초과 처리는 Backend가 합니다(계약 13.2).

한계: 로봇 원기둥과 정지한 작업자로 만든 단순화 모델의 검사입니다. 결과를 안전 보장으로 해석하지 않고, Safety Gate의 `SAFETY_DISTANCE` 판정을 대신하지 않습니다. Backend 호출·`snapshot_id` 계산·`ExecutionLog` 기록은 Backend 작업입니다.
- 미구현: Backend에 Worker 조회 API(`GET /workers/snapshot`)가 없어 `--backend` 모드에서는 Worker 스냅샷을 파일로 넘깁니다. 작업자 이동 예측은 아직 없습니다. 상자·작업대 같은 물체는 `state.objects`(#95)로 넣습니다.

## 로봇 팔 (#95)

로봇 종류가 달라도 같은 코드로 다룹니다. 로봇마다 다른 것은 `mujoco/arms.py`의 `ARM_MODELS` 한 줄(모델 파일, 관절 이름, 공구)뿐입니다.

| `robot_model` | 로봇 | 축 | 공구 | 현장 역할 |
|---|---|---|---|---|
| `ur10e` | Universal Robots UR10e | 6 | 박스 클램프 (0.30 m 상자) | 적재 스테이션 A: 랙 ↔ 팔레트 |
| `iiwa14` | KUKA LBR iiwa 14 | 7 | 박스 클램프 | 적재 스테이션 B: 랙 ↔ 팔레트 |
| `ur5e` | Universal Robots UR5e | 6 | 평행 그리퍼 (0.10 m 부품 상자) | 포장대 |
| `panda` | Franka Emika Panda | 7 | 평행 그리퍼 | 포장대 |

모델은 MuJoCo Menagerie에서 받습니다(`pixi run fetch-models`, 약 105MB, 저장소 제외). 새 로봇은 끝단에 `attachment_site`가 있고 관절이 모두 회전 관절인 모델이면 `ARM_MODELS`에 한 줄을 더해 씁니다.

```bash
pixi run arms-demo        # 공장 현장에 4대를 두고 각자 집기·놓기 → outputs/arms/ (CCTV 2대·근접 시점)
pixi run bridge-arms      # 브리지: plant_scene + 팔 4대, CCTV 영상에 팔이 나옴
pixi run arm-adapters     # 팔 어댑터 4개 (ros2 launch robot_adapter arms.launch.py)
pixi run arm-cycle-demo   # 데모: 4대가 상자를 계속 옮김 (검증 서버 대역, 시험 전용)
```

**현장 배치** (`arms.PLANT_ARMS`): `plant_scene.xml`의 TIAGo 작업 셀 동쪽 열린 바닥에 창고형 구역을 둡니다. CCTV 2대가 모두 보는 범위입니다.
- 적재 스테이션 A·B: 오렌지 랙 아래 칸의 0.30 m 상자를 바닥 팔레트로 내리고 다시 올립니다. 팔레트 A는 작업자 1 경로점(3.4, 1.8) 옆이고, 랙 B 남쪽 끝은 작업자 3 통로 옆입니다.
- 포장대: UR5e·Panda가 포장대 위 두 자리 사이로 부품 상자를 옮깁니다.
- 이 장면은 `plant_scene.xml` 파일을 바꾸지 않고 MjSpec으로 더합니다. 브리지에서 `arms:=true`일 때만 쓰며, 기본 장면과 Three.js 렌더(#55)는 그대로입니다.

**공통 어댑터** (`robot_adapter/arm_core.py`, `arm_adapter_node.py`): 같은 노드를 `robot_model`만 바꿔 띄웁니다. 검증 서버 쪽 토픽·규약(ACK·RESULT, 중복 실행 방지, E-Stop·HOLD)은 Mock·TIAGo 어댑터와 같습니다.
- 지원 Action: `MANIPULATE`(`PICK`·`PLACE`)·`STOP`·`RESUME`. 고정형 팔이라 `MOVE_TO`는 `UNSUPPORTED`입니다.
- 로봇마다 다른 부분은 궤적 계획기(`planner`) 하나입니다. 지금은 MuJoCo 운동학 모델(`arms.ArmPlanner`)이고, 실제 로봇은 같은 형태로 MoveIt 등을 감싼 계획기를 넣습니다.
- 궤적: 현재 자세 → 대상 위(관절 보간) → 대상까지 TCP 직선 하강 → 파지·놓기 → 직선 상승. 그리퍼는 아래를 향합니다. TCP 속도 상한은 `min(0.25 m/s, speed_limit)`입니다.
- 도달할 수 없는 목표는 움직이기 전에 `INVALID`로 거부합니다. `PICK`은 빈손일 때만, `PLACE`는 같은 `object_id`를 들고 있을 때만 받습니다.
- E-Stop·HOLD·STOP: 마지막 관절 목표를 그대로 유지하고 그리퍼는 열지 않습니다(들고 있던 물체를 떨어뜨리지 않음).

| 토픽 (`<seg>`: `arm-ur5e` → `arm_ur5e`) | 방향 | 형식 |
|---|---|---|
| `/<seg>/arm/joint_command` | 어댑터 → 브리지 | `sensor_msgs/JointState` 위치 목표 |
| `/<seg>/arm/gripper` | 어댑터 → 브리지 | `std_msgs/Bool` (닫힘) |
| `/<seg>/joint_states` | 브리지 → 어댑터 | `sensor_msgs/JointState` 실제 관절각 |
| `/adapter/<seg>/state` | 어댑터 → 검증 서버 | 기존 상태에 `robot_model`, `joints`, `tcp_position`, `gripper_closed`, `holding_object_id` 추가 |

**`MANIPULATE` 사전 시뮬레이션 (잠정)**: 제어 계약 13.1절은 대상 물체와 관절 초기 상태 형식을 정하지 않았습니다. 아래 형식은 CTL이 제안하는 **잠정 형식**이며 Backend·ONT 승인 후 계약에 넣습니다.

```json
{
  "action": {"action_type": "MANIPULATE", "robot_id": "arm-ur10e", "speed_limit": 0.25,
             "parameters": {"manipulation": "PICK", "object_id": "box-01",
                            "target_pose": {"frame_id": "world", "position": [5.25, 2.6, 0.6]}}},
  "state": {
    "robots": [{"robot_id": "arm-ur10e", "robot_model": "ur10e", "frame_id": "world",
                "position": [4.35, 2.6, 0.30], "yaw_rad": 0.0,
                "joint_positions": null, "holding_object_id": null}],
    "workers": {"observed_at": "...", "items": []},
    "zones": [],
    "objects": [{"object_id": "box-01", "frame_id": "world", "position": [5.25, 2.6, 0.6], "size": [0.3, 0.3, 0.3]}]
  }
}
```

- `parameters`는 계약 4.3절 `MANIPULATE`와 같습니다(`manipulation`, `object_id`, `target_pose`).
- `robots[].robot_model`: `ARM_MODELS` 키. 이 값이 있는 로봇은 원기둥 대신 실제 팔 모델로 붙입니다. `position`은 베이스(받침대 윗면) 위치입니다.
- `joint_positions`: 생략하거나 `null`이면 모델의 `home` 자세. `holding_object_id`: 들고 있는 물체(`PLACE`에 필요).
- `objects`: 상자 장애물(중심, 크기 m). `MOVE_TO`에서도 장애물로 쓰입니다.
- 판정: 팔·공구가 다른 물체·로봇·작업자와 닿으면 `COLLISION`(잡는 대상 물체와의 접촉은 제외), 팔·공구 표면과 작업자 몸 표면 사이가 `UNSAFE_DISTANCE_M` 미만이면 `UNSAFE_DISTANCE`, 경유점 TCP 오차가 0.02 m를 넘으면 `GOAL_NOT_REACHED`.
- `ERROR`: `robot_model`이 없는 로봇, `PICK`/`PLACE`가 아님, 물체 없음, `PICK`의 `target_pose`가 물체에서 0.05 m 넘게 떨어짐, `PLACE`인데 그 물체를 들고 있지 않음, `joint_positions` 개수 불일치.

한계:
- 파지는 물리 접촉 대신 운동학적으로 붙입니다(집으면 상자가 TCP를 따라감). 그리퍼 마찰·미끄러짐·하중은 다루지 않습니다.
- 사전 시뮬레이션에서 들고 있는 상자와 다른 물체 사이의 접촉은 검사하지 않습니다(둘 다 고정 물체로 둠). 팔·공구의 접촉만 봅니다.
- 브리지에서 팔은 위치 액추에이터로 움직이므로 빠른 동작에서 관절이 목표보다 늦습니다(측정 최대 약 0.2 rad, 0.25 m/s). 놓기 전 0.5 s 멈춰 이 차이를 줄입니다.
- 이동형 운반 로봇은 다음 절(AMR)에서 다룹니다.

## 이동형 운반 로봇 (AMR)

낮은 사각 차체(롤러 상판, 높이 0.30 m) 위에 팔레트(1.1 × 0.84 m, 상자 더미 포함 높이 약 0.74 m)를 싣고, 창고 랙 구역 서쪽 끝 거치대와 TIAGo 작업 셀(테이블 A·B 바깥쪽) 거치대 사이를 오갑니다. 코드는 `mujoco/amr.py`입니다.

```bash
pixi run amr-demo         # 팔레트를 실은 AMR 2대가 통로를 지나는 장면 → outputs/amr/ (CCTV 2대·근접 시점)
pixi run bridge-plant     # 브리지: plant_scene + 팔 4대 + AMR 2대 (arms:=true amrs:=true)
pixi run amr-adapters     # AMR 어댑터 2개 (ros2 launch robot_adapter amrs.launch.py)
pixi run amr-cycle-demo   # 데모: 2대가 팔레트를 계속 나름 (검증 서버 대역, 시험 전용)
```

**현장 배치** (`amr.PLANT_AMRS`): 통로 2개(y = ±1.75)는 작업자 1의 경로점 (3.4, ±1.8)을 지나므로 AMR이 작업자 통로를 가로지릅니다. 북쪽 통로는 UR10e 팔레트와 iiwa14 팔레트 사이입니다.

| `robot_id` | 창고 정차 위치 (x, y) → 거치대 | 작업 셀 정차 위치 → 거치대 | 팔레트 `object_id` |
|---|---|---|---|
| `amr-01` | (3.70, 1.75) → (4.72, 1.75) | (2.05, 1.75) → (1.03, 1.75) | `pallet:amr-01` |
| `amr-02` | (3.70, -1.75) → (4.72, -1.75) | (2.05, -1.75) → (1.03, -1.75) | `pallet:amr-02` |

- 거치대는 상판과 같은 높이(0.30 m)입니다. AMR이 거치대를 바라보고 서면 팔레트를 앞쪽으로 밀어 싣거나 내립니다(롤러 이송 2초). `target_pose.position`은 거치대 위 팔레트 바닥 중심 `[x, y, 0.30]`입니다.
- 장면은 `plant_scene.xml`을 바꾸지 않고 MjSpec으로 더합니다(`arms.build_plant_with_arms(extras=(amr.add_plant_amrs,))`). 브리지에서 `amrs:=true`일 때만 쓰며 `arms:=true`와 따로 켤 수 있습니다.

**어댑터** (`robot_adapter/amr_core.py`, `amr_adapter_node.py`): TIAGo 어댑터(`DiffDriveCore`)의 규약과 `MOVE_TO` 제어기(제자리 회전 → 직진)를 그대로 씁니다. `robot_type`은 `MOBILE`, 상태에 `robot_model: "amr"`과 `holding_object_id`(실린 팔레트)를 더합니다.
- `MOVE_TO`: 직선 이동. 속도 상한 `min(0.4 m/s, speed_limit)`(SR-01 기본값).
- `MANIPULATE` `PICK`(싣기): 빈 차체일 때만. 팔레트가 차체 앞 거치대 자리(0.15 m 안)에 없으면 브리지가 `INVALID`로 돌려줍니다.
- `MANIPULATE` `PLACE`(내리기): 같은 `object_id`를 싣고 있을 때만. 내릴 위치가 차체 앞 거치대 자리가 아니면 `INVALID`.
- E-Stop·HOLD·STOP: 차체에 0 속도를 계속 보내고, 이송 중이면 `CANCEL`을 보내 팔레트를 그 자리에 멈춥니다. 이때 싣기는 완료되지 않은 것으로 봅니다.

| 토픽 (`<seg>`: `amr-01` → `amr_01`) | 방향 | 형식 |
|---|---|---|
| `/<seg>/cmd_vel` | 어댑터 → 브리지 | `geometry_msgs/Twist` |
| `/<seg>/odom` | 브리지 → 어댑터 | `nav_msgs/Odometry` (world 좌표) |
| `/<seg>/amr/load_command` | 어댑터 → 브리지 | `std_msgs/String` JSON `{command_id, manipulation: PICK·PLACE·CANCEL, object_id, target_pose}` |
| `/<seg>/amr/load_result` | 브리지 → 어댑터 | `std_msgs/String` JSON `{command_id, success, reason_code, message}` |

**사전 시뮬레이션**: `MOVE_TO`는 기존 원기둥 판정을 그대로 씁니다. `state.robots[]`에 `"robot_model": "amr"`이 있으면 원기둥 크기를 차체 기준(반경 0.558 m, 높이 0.30 m)으로, `holding_object_id`가 있으면 실린 팔레트까지 덮는 크기(반경 0.692 m, 높이 1.04 m)로 둡니다. AMR의 `MANIPULATE` 사전 시뮬레이션은 아직 없습니다(`ERROR`).

한계:
- 차체 이동과 팔레트 싣기·내리기는 운동학입니다(바퀴 마찰·미끄러짐·하중 없음). 브리지 장면에서 AMR·팔레트는 충돌하지 않는 mocap 바디라 TIAGo·작업자와 겹쳐도 밀지 않습니다. 접촉 판정은 사전 시뮬레이션이 합니다.
- 경로는 직선 `MOVE_TO`만 따라가며 장애물 회피는 하지 않습니다.

## 어댑터 동작 규칙

- `status=APPROVED`가 아닌 제안은 ACK 없이 `NOT_APPROVED`로 거부합니다.
- 수신 즉시 ACK를 보내고, 완료·실패 시 RESULT를 보냅니다.
- 같은 `proposal_id`와 같은 명령을 다시 받으면 동작을 반복하지 않고 기존 ACK 또는 최종 RESULT를 재전송합니다.
- 지원하지 않는 Action(Mock은 `MANIPULATE`, 고정형 팔은 `MOVE_TO`)은 `UNSUPPORTED`로 응답합니다.
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
│   │   ├── franka_emika_panda/     # Panda 모델 (33MB, 저장소 제외 → fetch_models.sh로 받음)
│   │   ├── universal_robots_ur5e/  # UR5e·UR10e·iiwa14 모델 (저장소 제외 → fetch_models.sh)
│   │   ├── universal_robots_ur10e/
│   │   └── kuka_iiwa_14/
│   ├── simulation.py               # 차동 구동 시뮬레이션 (DiffDriveSim, 로봇 목록 ROBOTS)
│   ├── scenario.py                 # 작업자 접근 동작, 카메라 렌더·내부 파라미터, 관절 목록, 스냅샷
│   ├── twin.py                     # MuJoCo ② 디지털 트윈: 온톨로지 상태 → 장면, 정답 대비 위치 오차
│   ├── twin_simulate.py            # MuJoCo ② 사전 시뮬레이션 서비스 POST /twin/simulate
│   ├── manipulator.py              # Panda move_to·그리퍼·pick_and_place
│   ├── arms.py                     # 로봇 팔 공통 계층: 모델 목록, 공구, 역기구학, PICK·PLACE 궤적, 현장 배치
│   ├── amr.py                      # 이동형 운반 로봇: 차체·팔레트·거치대, 운동학 이동·싣기·내리기, 현장 배치
│   └── plant_demo.py               # 팔 4대·AMR 2대 정해진 순서 재생 (Three.js 오프라인 내보내기용)
├── ros2_ws/src/
│   ├── mujoco_bridge/              # ROS 2 ↔ MuJoCo 브리지, odom_echo, rate_monitor, cctv_stream(웹 MJPEG)
│   ├── robot_adapter/              # 공통 Action → 로봇 명령 (Mock·TIAGo·팔·AMR 어댑터, 팔 4대·AMR 2대 launch)
│   └── safety_control/             # HOLD·E-Stop 판단(gate), 토픽·QoS, 시험 하네스
├── tests/                          # pytest 단위 테스트
├── scripts/                        # cmd_vel_demo.sh, roundtrip.sh, measure_rates.sh, fetch_models.sh
├── pixi.toml, pixi.lock            # 환경 정의 (macOS·Ubuntu 공용)
├── requirements.txt
└── README.md
```
