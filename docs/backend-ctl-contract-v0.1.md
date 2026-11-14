# Backend–CTL ROS 2 연동 계약 v0.2

- 상태: Draft (팀 승인 전)
- 작성일: 2026-11-04
- 개정: 2026-11-06 v0.2 — 가상 CCTV 영상 출력(3장)과 MuJoCo ② 사전 시뮬레이션 서비스(13장) 추가. 기존 1~12장 변경 없음. 파일명은 링크 유지를 위해 v0.1 그대로 둔다.
- 적용 대상: `backend` 검증 서버, `control` ROS 2 제어 어댑터
- 승인 필요: ONT 임성현, CTL 최민서
- 관련 이슈: #28
- 기준: `ontology-schema-v1.1.md`, `backend-api-spec-v0.2.md`, `control/README.md`

이 문서는 실행 직전 재검증을 통과한 `ActionProposal`을 Backend에서 ROS 2 어댑터로 전달하고, 어댑터 결과를 Backend 상태와 `ExecutionLog`에 반영하는 경계를 정의한다. 스키마 v1.1의 온톨로지 객체를 변경하지 않고, ROS 2 전송을 위한 직렬화·변환 규칙을 추가한다.

## 1. 범위와 원칙

v0.1의 필수 연동 범위는 다음과 같다.

1. Backend가 실행 직전 안전 재검증을 수행한다.
2. 통과한 Proposal만 `/safety/approved_action`에 발행한다.
3. CTL 어댑터는 수신 즉시 `ACK`를, 완료·실패 시 `RESULT`를 보고한다.
4. Backend 브리지는 최종 `RESULT`를 `POST /action-proposals/{proposal_id}/result`로 반영한다.
5. E-Stop은 일반 실행 결과보다 우선하며 `/safety/estop` 전용 경로를 사용한다.

전송 의미는 **at-least-once**로 두고 `proposal_id`와 동일 본문을 기준으로 멱등 처리한다. 미승인 Proposal을 ROS 2에 직접 발행하거나 CTL이 Backend 저장소를 직접 변경해서는 안 된다.

## 2. 연동 구조

```text
Backend ExecuteAction
  └─ 실행 직전 안전 재검증
      └─ ApprovedActionCommand 변환
          └─ /safety/approved_action
              └─ Robot Adapter
                  ├─ /adapter/{robot_topic_id}/result : ACK
                  ├─ ROS 2 / MuJoCo 실행
                  └─ /adapter/{robot_topic_id}/result : RESULT
                      └─ Backend Result Bridge
                          └─ POST /action-proposals/{proposal_id}/result
```

Backend가 ROS 2 노드를 같은 프로세스에서 실행하지 않는 경우, `backend-ctl-bridge` 프로세스가 ROS 2 publish·subscribe와 HTTP 결과 반영을 전담한다. 브리지는 비즈니스 판단을 하지 않고 이 문서의 변환만 수행한다.

## 3. 토픽·QoS

| 토픽 | 방향 | ROS 2 타입 | QoS | 용도 |
|---|---|---|---|---|
| `/safety/approved_action` | Backend → CTL | `std_msgs/String` JSON | Reliable, Keep Last 10 | 승인된 실행 명령 |
| `/adapter/{robot_topic_id}/result` | CTL → Backend | `std_msgs/String` JSON | Reliable, Keep Last 10 | `ACK`, `RESULT`, `HOLD`, `ESTOP` 이벤트 |
| `/safety/estop` | Backend·운영자 → CTL | `std_msgs/String` JSON | Reliable, Transient Local, Keep Last 1 | E-Stop 전용 경로 |
| `/safety/heartbeat` | Backend → CTL | `std_msgs/String` JSON | Reliable, Keep Last 1, 5 Hz | 검증 서버 생존 확인 |
| `/adapter/{robot_topic_id}/state` | CTL → Backend | `std_msgs/String` JSON | Reliable, Keep Last 10, 10 Hz | 로봇 상태 관측(선택) |

가상 CCTV 영상 [v0.2]:

| 토픽 | 방향 | ROS 2 타입 | QoS | 용도 |
|---|---|---|---|---|
| `/cctv_{n}/image_raw` | CTL(MuJoCo ① + Three.js) → CV | `sensor_msgs/Image`, `rgb8`, 640×480 | Best Effort, Keep Last 1 | CV 입력 CCTV 영상. `frame_id=camera_cctv_{n}`, `header.stamp`는 시뮬레이션 시각 |

- Three.js 렌더러는 장면 전체(로봇, 작업자, 장비, 바닥, 금지구역 표시)를 그리고, 카메라 위치·방향·화각(fovy 60°)은 `scenario_scene.xml`의 `cctv_1`, `cctv_2`와 같게 맞춘다.
- 좌표 변환: MuJoCo(x 앞, y 왼쪽, z 위) → Three.js(y 위). MuJoCo `(x, y, z)`는 Three.js `(x, z, -y)`로 옮긴다.
- 시연용 안내 문구는 CV 입력 영상에 넣지 않는다.
- 작업자 정답 좌표(`/sim/worker_pose`)는 평가용이며 CV·MuJoCo ②·Backend 안전 판정에 쓰지 않는다.
- 렌더러가 멈추면 프레임이 끊기고, Backend는 `OBSERVATION_GAP`(2초)으로 실행을 거부한다.

`robot_topic_id`는 원본 `robot_id`의 영문·숫자·`_` 외 문자를 `_`로 바꾸고, 첫 문자가 숫자이면 `r_`를 붙인다.

```text
robot-01  -> robot_01
01-arm    -> r_01_arm
```

토픽 경로에만 변환값을 사용하고, JSON 본문의 `robot_id`는 원본 ID를 유지한다.

## 4. 승인 Action 명령

### 4.1 직렬화 규칙

Backend의 `ActionProposal` 객체를 그대로 publish하지 않고 CTL이 실행할 수 있는 `ApprovedActionCommand` JSON으로 변환한다.

- 명령 `status`는 항상 `APPROVED`이다.
- Backend 내부의 Proposal은 발행 성공 후 `EXECUTING`으로 전이할 수 있지만, 어댑터에 보내는 불변 승인 스냅샷은 `APPROVED`를 유지한다.
- `COORDINATE.position` 객체 `{x,y,z}`는 CTL 형식 `[x,y,z]`로 변환한다.
- `ZONE` 목적지는 등록된 `Zone.geometry.navigation_target`을 좌표로 해석한다.
- `navigation_target`이 없거나 frame을 확정할 수 없으면 발행하지 않고 통합 오류로 처리한다.
- 실행 속도는 `min(proposal.speed_limit, parameters.speed_limit, robot.motion_limits.max_speed)`를 사용한다. 필드가 없는 값은 제외한다.

Zone 직렬화 규약:

```json
{
  "zone_id": "A구역",
  "frame_id": "world",
  "geometry": {
    "type": "polygon",
    "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    "navigation_target": {"x": 0.5, "y": 0.5, "z": 0.0}
  }
}
```

### 4.2 MOVE_TO 예시

```json
{
  "contract_version": "backend-ctl/0.1",
  "proposal_id": "proposal-123",
  "task_id": "task-123",
  "robot_id": "robot-01",
  "action_type": "MOVE_TO",
  "parameters": {
    "target_pose": {
      "frame_id": "world",
      "position": [1.2, -0.8, 0.0]
    },
    "speed_limit": 0.3
  },
  "trajectory": null,
  "frame_id": "world",
  "speed_limit": 0.3,
  "model_name": "rule-based-action-planner",
  "model_version": "0.2.0",
  "status": "APPROVED",
  "proposed_at": "2026-11-04T00:00:00Z"
}
```

### 4.3 MANIPULATE 예시

```json
{
  "contract_version": "backend-ctl/0.1",
  "proposal_id": "proposal-456",
  "task_id": "task-456",
  "robot_id": "robot-01",
  "action_type": "MANIPULATE",
  "parameters": {
    "manipulation": "PICK",
    "object_id": "box-01",
    "target_pose": {
      "frame_id": "world",
      "position": [0.5, 0.3, 0.02]
    }
  },
  "trajectory": null,
  "frame_id": "world",
  "speed_limit": 0.3,
  "model_name": "rule-based-action-planner",
  "model_version": "0.2.0",
  "status": "APPROVED",
  "proposed_at": "2026-11-04T00:00:00Z"
}
```

CTL은 자신의 `robot_id`와 다른 명령을 무시한다. 같은 `proposal_id`를 다시 받으면 동작을 중복 실행하지 않고 기존 `ACK` 또는 최종 `RESULT`를 다시 보고한다.

## 5. CTL 이벤트

### 5.1 ACK

수신·기본 계약 검사를 통과하면 동작 시작 전에 즉시 발행한다. ACK는 Backend 상태 전이를 발생시키지 않고 전송 지연 측정과 장애 탐지에만 사용한다.

```json
{
  "kind": "ACK",
  "robot_id": "robot-01",
  "proposal_id": "proposal-123",
  "timestamp": "2026-11-04T00:00:00Z"
}
```

### 5.2 RESULT

```json
{
  "kind": "RESULT",
  "robot_id": "robot-01",
  "proposal_id": "proposal-123",
  "timestamp": "2026-11-04T00:00:01Z",
  "success": true,
  "reason_code": null,
  "message": "목표 도달"
}
```

- `success=true`이면 `reason_code` 는 `null`이어야 한다.
- `success=false`이면 Backend에 보고할 표준 `reason_code`가 필수다.
- Backend 브리지는 `robot_id`와 Proposal의 대상 로봇이 같은지 확인한다.
- 동일 Proposal의 최종 RESULT는 하나만 허용한다.

Backend HTTP 변환:

| CTL RESULT | `POST /action-proposals/{proposal_id}/result` 본문 |
|---|---|
| `success=true` | `{"status":"SUCCEEDED","detail":"<message>"}` |
| `success=false`, `UNSUPPORTED` | `{"status":"FAILED","reason_code":"UNSUPPORTED","detail":"<message>"}` |
| `success=false`, `TIMEOUT` | `{"status":"FAILED","reason_code":"TIMEOUT","detail":"<message>"}` |
| `success=false`, `HW_ERROR` | `{"status":"FAILED","reason_code":"HW_ERROR","detail":"<message>"}` |

### 5.3 어댑터 로컬 사유 코드 정규화

스키마 v1.1과 Backend 결과 API의 표준 실패 코드는 `UNSUPPORTED`, `TIMEOUT`, `HW_ERROR` 세 개다. 현재 CTL의 로컬 코드는 브리지에서 다음처럼 정규화하고, 원본 코드는 `detail` 앞에 보존한다.

| CTL 로컬 코드 | Backend 표준 코드 | 처리 |
|---|---|---|
| `UNSUPPORTED` | `UNSUPPORTED` | 재계획 가능 |
| `NOT_APPROVED`, `INVALID` | `UNSUPPORTED` | 계약 위반 로그 후 재계획 |
| `TIMEOUT`, `ROBOT_BUSY`, `ROBOT_HELD`, `NO_HEARTBEAT` | `TIMEOUT` | Fail-Closed, Robot `HOLD` |
| `HW_ERROR` | `HW_ERROR` | Robot `ERROR` |
| `CANCELLED`, `ESTOP` | `TIMEOUT` | E-Stop/HOLD 이벤트가 우선, 결과는 보조 기록 |

`detail` 예시: `[NO_HEARTBEAT] 검증 서버 하트비트 없음`

E-Stop으로 Backend가 이미 Proposal `HELD`, Task `HELD`, Robot `ESTOP`으로 전이한 후 도착한 RESULT는 상태를 덮어쓰지 않는다. 결과 API의 `409`는 예상된 stale result로 기록하고 재시도하지 않는다.

### 5.4 HOLD·ESTOP 이벤트

```json
{
  "kind": "ESTOP",
  "robot_id": "robot-01",
  "proposal_id": null,
  "timestamp": "2026-11-04T00:00:02Z",
  "triggered_by": "OPERATOR",
  "latency_ms": 82.4
}
```

`triggered_by`는 `RULE_ENGINE`, `OPERATOR`, `HEARTBEAT_LOSS` 중 하나다. HOLD·ESTOP 이벤트는 최종 RESULT보다 우선하며, Backend는 더 안전한 상태를 성공 결과로 덮어쓰지 않는다.

## 6. E-Stop·Heartbeat

E-Stop 명령:

```json
{
  "active": true,
  "triggered_by": "RULE_ENGINE",
  "issued_at": "2026-11-04T00:00:02Z"
}
```

- 해석할 수 없는 E-Stop 메시지는 `active=true`로 처리한다.
- `active=false`는 정지 신호만 해제하며 로봇을 즉시 `IDLE`로 복귀시키지 않는다.
- 복귀는 위험 해제 승인 후 새 `RESUME` ActionProposal을 거친다.

Heartbeat:

```json
{"timestamp":"2026-11-04T00:00:02Z"}
```

Backend는 5 Hz로 발행한다. CTL이 마지막 정상 heartbeat 후 1초 동안 새 heartbeat를 받지 못하면 자율적으로 `HOLD`에 진입하고 `HEARTBEAT_LOSS` 이벤트를 보고한다.

## 7. 상태 전이

| 이벤트 | ActionProposal | TaskOrder | Robot |
|---|---|---|---|
| 재검증 실패 | `HELD` | `HELD` | `ESTOP` |
| 명령 발행 성공 | `EXECUTING` | `RUNNING` | `MOVING` |
| RESULT 성공 | `SUCCEEDED` | `COMPLETED` | `IDLE` |
| `UNSUPPORTED` | `FAILED` | `PLANNING` | `HOLD` |
| `TIMEOUT` | `FAILED` | `FAILED` | `HOLD` |
| `HW_ERROR` | `FAILED` | `FAILED` | `ERROR` |
| HOLD·ESTOP 수신 | `HELD` | `HELD` | `HOLD` 또는 `ESTOP` |

명령 publish에 실패하면 `EXECUTING`으로 전이하지 않는다. 발행 후 ACK가 지정된 타임아웃 내에 도착하지 않으면 브리지는 같은 명령을 재발행할 수 있으며, CTL은 `proposal_id` 멱등성으로 중복 실행을 막아야 한다.

## 8. 결과 재시도·오류 처리

- 네트워크 오류 또는 HTTP `5xx`: 같은 본문으로 유한 횟수 재시도한다.
- HTTP `200`: 반영 완료. 동일 결과 재전송의 `200`도 성공이다.
- HTTP `404`: 알 수 없는 Proposal. 재시도하지 않고 통합 오류로 기록한다.
- HTTP `409`: 다른 결과가 이미 기록됐거나 안전 상태가 먼저 변경된 경우다. 재시도하지 않는다.
- HTTP `422`: 계약 불일치다. 원본 CTL 이벤트와 변환 본문을 모두 기록하고 재시도하지 않는다.

브리지 재시작을 고려해 미반영 RESULT는 메모리가 아닌 지속 저장소에 보존하는 것이 목표다. MVP 단계에서 인메모리 큐를 사용하면 프로세스 재시작 시 손실 가능성을 미구현 범위로 표시한다.

## 9. 상태 토픽

`/adapter/{robot_topic_id}/state`는 v0.1의 필수 상태 전이 경로가 아니다. Backend의 Robot 객체를 직접 덮어쓰지 않고 관측·디버깅에만 사용한다. 후속 버전에서 다음 변환을 확정한다.

- `pose.position`: CTL 배열 → Backend `Vector3`
- `pose.orientation`: CTL 배열 → Backend `Quaternion`
- `velocity`: CTL 스칼라 → Backend `Vector3`
- `motion_limits.max_acceleration` 기본값 출처
- CTL 상태와 Backend 안전 상태가 충돌할 때의 우선순위

## 10. 적합성 시험

연동 완료는 아래 항목을 모두 통과해야 한다.

1. `APPROVED` MOVE_TO만 CTL이 실행한다.
2. Backend 좌표 `{x,y,z}`가 CTL `[x,y,z]`로 정확히 변환된다.
3. ZONE 명령이 `navigation_target`으로 변환되고, 값이 없으면 실행을 차단한다.
4. 명령 수신 후 ACK가 도착한다.
5. 같은 `proposal_id`를 두 번 publish해도 로봇 동작은 한 번만 실행된다.
6. RESULT 성공이 Proposal `SUCCEEDED`, Task `COMPLETED`, Robot `IDLE`로 반영된다.
7. `UNSUPPORTED`, `TIMEOUT`, `HW_ERROR` 각각의 상태 전이가 계약과 일치한다.
8. E-Stop 후 도착한 성공 RESULT가 `ESTOP`을 덮어쓰지 못한다.
9. heartbeat 1초 손실 시 CTL이 자율 HOLD에 진입한다.
10. `robot-01`이 `/adapter/robot_01/result`로 변환되며 JSON 본문의 ID는 유지된다.

## 11. 담당 경계

| 항목 | Backend·NLP | ONT | CTL |
|---|---|---|---|
| ActionProposal 실행 직전 재검증 | 구현 | 규칙 승인 | - |
| ApprovedActionCommand 변환·publish | 구현 | 계약 승인 | 형식 검토 |
| Action 검증·멱등 실행 | - | 적합성 기준 | 구현 |
| ACK·RESULT·HOLD·ESTOP 발행 | - | 코드 승인 | 구현 |
| RESULT HTTP 변환·상태 반영 | 구현 | 상태 전이 승인 | 형식 검토 |
| E-Stop·Heartbeat | 발행 | 정책 승인 | 수신·Fail-Closed |

## 12. 현재 코드와의 차이

| 항목 | 현재 | v0.1 연동 요구 |
|---|---|---|
| Backend 실행 | Mock adapter 전달 로그만 남김 | ROS 2 publish 브리지 연결 |
| 실행 결과 | PR #24의 HTTP 결과 API | ROS 2 RESULT → HTTP 변환 |
| Proposal 상태 | Backend 반환값은 `EXECUTING` | 전송 스냅샷은 `APPROVED` |
| 좌표 | Backend `{x,y,z}` | CTL `[x,y,z]` 변환 |
| Zone | `zone_id`만 제안에 유지 | `geometry.navigation_target` 필요 |
| 실패 코드 | CTL 로컬 코드 8종 추가 | 구현: Backend 브리지(`ctl_bridge.LOCAL_FAILURE_CODES`)가 5.3절 표대로 3개 표준 코드로 정규화, 원본 코드는 `detail` 앞에 보존 |
| 멱등성 | Backend 결과 API만 멱등 | 구현(CT-03): 같은 `proposal_id`·같은 본문은 기존 ACK·최종 RESULT 재전송, 본문이 다르면 `INVALID` |
| Robot state | CTL 고유 축약 형식 | v0.1은 관측용, 후속 계약으로 분리 |

| CCTV 영상 | MuJoCo 렌더링 | Three.js 렌더링 (v0.2, 계획) |
| 사전 시뮬레이션 | 없음 | MuJoCo ② 서비스 (v0.2, 계획) |

이 차이는 명세 승인 후 Backend·CTL 각각의 구현 PR로 분리한다. 이 문서 자체는 현재 구현 완료를 의미하지 않는다.

## 13. MuJoCo ② 사전 시뮬레이션 서비스 [v0.2 초안]

스키마 v1.1의 `COLLISION` 규칙을 판정하기 위해 Backend가 CTL의 MuJoCo ② 서비스를 호출한다. 판정과 감사 기록은 Backend(온톨로지)가 하고, MuJoCo ②는 시뮬레이션 결과만 돌려준다.

- 전송: HTTP `POST /twin/simulate` (CTL 서비스, 기본 `http://127.0.0.1:8100`)
- MuJoCo ②는 요청에 담긴 상태만으로 장면을 만들고 MuJoCo ① 정답 좌표를 읽지 않는다.
- 작업자 위치에는 CV 위치 오차(측정값 0.15~0.20 m) 이상의 여유 반경을 더한다.
- 긴급 정지는 이 서비스를 기다리지 않는다. `/safety/estop`과 heartbeat 경로가 우선한다.

### 13.1 첫 지원 범위와 요청 형식

- 첫 버전은 `MOVE_TO`만 지원한다. `MANIPULATE`(PICK·PLACE)는 대상 물체와 관절 초기 상태가 계약에 정해진 뒤 추가한다. 지원하지 않는 `action_type` 요청에는 `ERROR`를 돌려준다.
- 후보 행동은 4장 `ApprovedActionCommand`와 같은 값으로 보낸다. Backend가 4.1절 규칙으로 해석한 결과를 그대로 쓴다.
  - `target_pose`: `ZONE` 목적지를 `navigation_target`으로 해석한 **실제 CTL 전송 좌표**
  - `speed_limit`: 4.1절의 유효 속도 상한 `min(proposal.speed_limit, parameters.speed_limit, robot.motion_limits.max_speed)`
- 좌표계는 `world`(z 위, 오른손 좌표계), 길이 단위는 m, 속도는 m/s, 각도는 rad, 시각은 ISO 8601 UTC다.
- `snapshot_id`는 Backend가 검증에 사용한 상태를 식별하는 값이다(13.3절).

```json
{
  "contract_version": "backend-ctl/0.2",
  "proposal_id": "proposal-123",
  "snapshot_id": "snap-7f3a…",
  "snapshot_at": "2026-11-06T00:00:00Z",
  "action": {
    "action_type": "MOVE_TO",
    "robot_id": "robot-01",
    "target_pose": {"frame_id": "world", "position": [1.2, -0.8, 0.0]},
    "speed_limit": 0.3
  },
  "state": {
    "robots": [{"robot_id": "robot-01", "frame_id": "world", "position": [0.0, 0.0, 0.0], "yaw_rad": 0.0}],
    "workers": {"observed_at": "2026-11-05T23:59:59.800Z",
                "items": [{"worker_id": "worker-01", "frame_id": "world", "position": [2.1, 0.4, 0.0]}]},
    "zones": [{"zone_id": "keep-out-1", "zone_type": "KEEP_OUT", "frame_id": "world",
               "geometry": {"type": "polygon", "points": [[0.5, -0.6], [1.9, -0.6], [1.9, 0.6], [0.5, 0.6]]}}]
  }
}
```

### 13.2 응답과 판정

- 응답: `result`(`PASS` | `COLLISION` | `UNSAFE_DISTANCE` | `GOAL_NOT_REACHED` | `ERROR`), `snapshot_id`(요청 값 그대로), `min_worker_distance_m`, `first_contact_at_s`, `simulated_horizon_s`, `elapsed_ms`
- `PASS`는 시뮬레이션 구간 안에서 **충돌 없이 목표 동작을 끝낸 경우에만** 쓴다. 충돌·안전거리 미달이 없어도 구간이 끝날 때 목표에 도달하지 못하면 `GOAL_NOT_REACHED`다.
- `min_worker_distance_m`: 작업자가 없으면 `null`. `first_contact_at_s`: 접촉이 없으면 `null`. 필드는 생략하지 않고 `null`로 보낸다.
- Backend는 아래를 모두 **실행 차단**으로 처리한다(Fail-Closed).
  - `PASS`가 아닌 모든 `result`
  - 시간 제한 초과, 연결 실패, 2xx가 아닌 HTTP 응답
  - 응답 형식 오류(필수 필드 누락, 알 수 없는 `result`, 요청과 다른 `snapshot_id`)
- 시간 제한 값은 ONT·백엔드가 정한다.
- 결과는 `ExecutionLog`에 규칙 판정 근거·`snapshot_id`와 함께 기록한다.

### 13.3 오래된 PASS 재사용 방지

- Backend는 요청을 만들 때 관련 상태(대상 로봇 상태, Worker 스냅샷 `observed_at`, 열린 HazardEvent, 관련 Zone)로 `snapshot_id`를 정한다.
- 실행 직전 재검증에서 관련 상태로 `snapshot_id`를 다시 계산해, 시뮬레이션한 값과 다르면 기존 `PASS`를 폐기하고 다시 시뮬레이션하거나 거부한다. 다른 `snapshot_id`의 `PASS`는 재사용하지 않는다.
- Backend는 HTTP 응답을 기다리는 동안 저장소 잠금을 잡지 않는다. 잠금은 스냅샷을 만들 때와 결과를 반영할 때만 짧게 잡아, 대기 중에도 위험 이벤트 등록과 긴급 정지가 지연되지 않게 한다.

| 항목 | Backend | ONT | CTL |
|---|---|---|---|
| 서비스 구현 | - | - | 구현 |
| 호출·시간 초과 처리 | 구현 | 기준 승인 | - |
| 판정 기준(`COLLISION`) | - | 정의 | 형식 검토 |
