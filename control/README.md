# Control (CTL)

ROS 2 제어 어댑터, 안전 경로(HOLD·E-Stop), MuJoCo 시뮬레이션 환경입니다. 기준 문서는 개발계획서 v1.0(실행판) 5-5·5-6장, 부록 A와 `ontology-schema-v1.1.md` 7·9장입니다. 시뮬레이터는 계획서의 Isaac Sim 대신 MuJoCo를 사용합니다.

## 진행 상황

| 순서 | 항목 | 상태 | 확인 명령 |
|---|---|---|---|
| 1~5 | Python·MuJoCo·ROS 2 설치, 작업공간 빌드 | 완료 | `pixi run build` |
| 6~7 | 바퀴 로봇 모델(TIAGo), 전진·회전·정지 | 완료 | `pixi run sim` |
| 8 | ROS 2 브리지 (`/cmd_vel` → MuJoCo → `/odom`) | 완료 | `pixi run cmd-vel-demo` |
| 9 | Mock 어댑터 (공통 Action, ACK·RESULT, E-Stop·HOLD) | 완료 | `pixi run roundtrip` |

다음 단계는 공통 Action(`MOVE_TO`)을 받아 `/cmd_vel`로 바퀴 로봇을 움직이는 MuJoCo 어댑터입니다.

## 로봇 모델

| 이름 | 모델 | 용도 |
|---|---|---|
| `tiago` (기본) | PAL Robotics TIAGo: 차동 구동 바퀴 + 7축 팔 + 그리퍼 + 승강 몸통 | 개발계획서 3-3장의 "매니퓰레이터 탑재 모바일 로봇". `MOVE_TO`·`MANIPULATE`를 한 로봇으로 시연 |
| `simple` | 직접 만든 상자형 바퀴 로봇 | 빠른 시험·디버깅 |

- TIAGo는 [MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie)의 `pal_tiago`(Apache-2.0, 5.5MB)입니다. 원본 파일은 수정하지 않고 `tiago_scene.xml`에서 불러옵니다.
- 바퀴 반지름 0.0985 m, 바퀴 간격 0.4044 m, 바퀴 속도 제한 ±5 rad/s라서 최대 선속도는 약 0.49 m/s입니다.
- 팔·머리·몸통은 위치 제어로 `home` 자세를 유지합니다.
- 로봇은 `--robot simple`(시뮬레이션) 또는 `-p robot:=simple`(브리지)로 바꿉니다.

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
| `pixi run test` | 단위 테스트 (어댑터, 안전 게이트, 바퀴 로봇 시뮬레이션) |
| `pixi run build` | `ros2_ws`를 colcon으로 빌드 |
| `pixi run sim` / `sim-view` | 바퀴 로봇 전진·회전·정지 시험 / 뷰어로 보기 |
| `pixi run bridge` / `bridge-view` | MuJoCo 브리지 실행 / 뷰어와 함께 실행 |
| `pixi run cmd-vel-demo` | 브리지를 띄우고 `/cmd_vel`로 전진·회전·정지 후 `/odom` 출력 |
| `pixi run mock-adapter` | Mock 어댑터만 실행 |
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
| `/safety/approved_action` | 검증 서버 → 어댑터 | Reliable | 승인된 ActionProposal (JSON) |
| `/safety/estop` | 검증 서버·운영자 → 어댑터 | Reliable, latched | `{"active", "triggered_by", "issued_at"}` |
| `/safety/heartbeat` | 검증 서버 → 어댑터 | Reliable, 5Hz | `{"timestamp"}` |
| `/adapter/{robot_id}/result` | 어댑터 → 검증 서버 | Reliable | ACK, RESULT, ESTOP, HOLD 이벤트 (JSON) |
| `/adapter/{robot_id}/state` | 어댑터 → 검증 서버 | Reliable, 10Hz | Robot 객체 상태 (제안 토픽) |

`/safety/*`, `/adapter/*` 메시지 본문은 OpenAPI 확정 전까지 `std_msgs/String`에 JSON으로 담습니다.

## 어댑터 동작 규칙

- `status=APPROVED`가 아닌 제안은 ACK 없이 `NOT_APPROVED`로 거부합니다.
- 수신 즉시 ACK를 보내고, 완료·실패 시 RESULT를 보냅니다.
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
│   │   ├── tiago_scene.xml         # TIAGo + 작업자 + CCTV 장면 (기본 로봇)
│   │   ├── pal_tiago/              # MuJoCo Menagerie TIAGo 모델 (Apache-2.0, 5.5MB)
│   │   ├── diff_drive.xml          # 단순 시험용 바퀴 로봇
│   │   ├── panda_scene.xml         # Panda 로봇 팔 + 작업자 + CCTV 장면
│   │   └── franka_emika_panda/     # Panda 모델 (33MB, 저장소 제외 → fetch_models.sh로 받음)
│   ├── simulation.py               # 차동 구동 시뮬레이션 (DiffDriveSim, 로봇 목록 ROBOTS)
│   └── manipulator.py              # Panda move_to·그리퍼·pick_and_place
├── ros2_ws/src/
│   ├── mujoco_bridge/              # ROS 2 ↔ MuJoCo (/cmd_vel → 바퀴, /odom), odom_echo
│   ├── robot_adapter/              # 공통 Action → 로봇 명령 (Mock 어댑터)
│   └── safety_control/             # HOLD·E-Stop 판단(gate), 토픽·QoS, 시험 하네스
├── tests/                          # pytest 단위 테스트
├── scripts/                        # cmd_vel_demo.sh, roundtrip.sh, fetch_models.sh
├── pixi.toml, pixi.lock            # 환경 정의 (macOS·Ubuntu 공용)
├── requirements.txt
└── README.md
```
