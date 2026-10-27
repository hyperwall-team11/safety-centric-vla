# Backend API 명세 v0.2 (구현 기준)

- 상태: MVP 구현 명세(As-Built)
- 기준 코드: `backend/app/main.py`, `backend/app/models.py`
- 기준 버전: FastAPI application `0.2.0`
- 작성일: 2026-10-16
- 기본 URL(로컬): `http://127.0.0.1:8000`
- 대화형 문서: `/docs`
- OpenAPI JSON: `/openapi.json`

이 문서는 현재 저장소에서 실제 호출 가능한 HTTP API를 정의한다. [ontology-schema-v1.1.md](ontology-schema-v1.1.md)는 객체·상태·안전 의미의 기준이고, 이 문서는 그중 현재 백엔드 MVP가 구현한 전송 경로와 동작의 기준이다. 개발계획서의 `/api/v1/...` 표기는 목표 인터페이스 초안이며 아직 현재 코드에 적용되지 않았다.

## 1. 공통 규약

- 요청과 응답 본문은 JSON이며 `Content-Type: application/json`을 사용한다.
- 정의되지 않은 요청 필드는 허용하지 않는다(`extra=forbid`).
- 시간은 UTC ISO 8601 문자열로 반환한다.
- 거리 단위는 m, 속도 단위는 m/s이다.
- ID는 문자열이다. 서버 생성 ID는 `task-`, `proposal-`, `hazard-`, `log-` 접두사와 UUID를 사용한다.
- 현재 인증·인가와 `/api/v1` 접두사는 구현하지 않았다.
- 현재 저장소는 인메모리이므로 서버 재시작 시 등록 데이터가 사라진다.

### 공통 오류

| 상태 코드 | 의미 | 대표 응답 |
|---|---|---|
| `404` | 요청한 Robot, TaskOrder, ActionProposal 또는 HazardEvent가 없음 | `{"detail":"...찾을 수 없습니다."}` |
| `409` | 현재 상태에서 실행 불가, ID 중복, 활성 Proposal 또는 멱등성 충돌 | `{"detail":"..."}` |
| `422` | JSON 스키마 위반, 자연어 해석 실패 또는 안전한 제안 생성 불가 | FastAPI 검증 오류 배열 또는 `{"detail":"..."}` |

## 2. 구현 엔드포인트

현재 구현은 12개 path, 14개 HTTP operation으로 구성된다.

| Method | Path | 성공 | 용도 |
|---|---|---:|---|
| `GET` | `/health` | `200` | 서버 상태 확인 |
| `POST` | `/robots` | `201` | Robot 등록 |
| `GET` | `/robots` | `200` | Robot 전체 조회 |
| `POST` | `/task-orders/from-natural-language` | `201` | 자연어를 TaskOrder로 변환·저장 |
| `GET` | `/task-orders/{task_id}` | `200` | TaskOrder 단건 조회 |
| `POST` | `/task-orders/{task_id}/action-proposals` | `201`, `200` | TaskOrder에서 ActionProposal 생성 |
| `GET` | `/task-orders/{task_id}/action-proposals` | `200` | TaskOrder의 Proposal 목록 조회 |
| `POST` | `/action-proposals` | `201` | 외부 VLA의 ActionProposal 직접 등록 |
| `POST` | `/action-proposals/{proposal_id}/evaluate` | `200` | 안전 규칙 평가 |
| `POST` | `/action-proposals/{proposal_id}/execute` | `200` | 실행 직전 재평가 후 Mock adapter 전달 |
| `POST` | `/hazards` | `201` | HazardEvent 등록 |
| `POST` | `/hazards/{hazard_id}/resolve` | `200` | HazardEvent 해제 |
| `GET` | `/safety-rules` | `200` | 활성 안전 규칙 조회 |
| `GET` | `/execution-logs` | `200` | 전체 ExecutionLog 조회 |

## 3. 데이터 계약

### 3.1 Robot

`POST /robots` 요청과 응답은 같은 구조다.

| 필드 | 타입 | 규칙 |
|---|---|---|
| `robot_id` | string | 등록된 값과 중복 불가 |
| `robot_type` | enum | `HUMANOID`, `QUADRUPED`, `MANIPULATOR`, `MOBILE` |
| `control_adapter` | string | 안전 평가 시 공백이 아니어야 통과 |
| `status` | enum | `IDLE`, `MOVING`, `HOLD`, `ESTOP`, `ERROR` |
| `pose` | object | `frame_id`, `position`, `orientation` |
| `velocity` | Vector3 | `x`, `y`, `z` |
| `motion_limits` | object | 양수 `max_speed`, `max_acceleration` |
| `updated_at` | datetime | 생략 시 서버 생성 |

```json
{
  "robot_id": "robot-01",
  "robot_type": "MOBILE",
  "control_adapter": "mock-mobile-adapter",
  "status": "IDLE",
  "pose": {
    "frame_id": "map",
    "position": {"x": 0, "y": 0, "z": 0},
    "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}
  },
  "velocity": {"x": 0, "y": 0, "z": 0},
  "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5}
}
```

### 3.2 자연어 TaskOrder 생성

`POST /task-orders/from-natural-language`

선택 헤더:

| 헤더 | 규칙 |
|---|---|
| `Idempotency-Key` | 공백 제외 1~128자. 같은 키·본문 재요청은 기존 TaskOrder와 `200`을 반환하며, 같은 키에 다른 본문은 `409` |

요청:

| 필드 | 타입 | 필수 | 규칙 |
|---|---|---:|---|
| `instruction` | string | O | 공백 제외 1~1000자 |
| `target_robot_id` | string | X | 명령에 로봇 ID가 없을 때 명시 가능 |
| `frame_id` | string | X | 좌표 목적지의 frame, 기본 `map` |

```json
{
  "instruction": "robot-01 로봇을 A구역으로 이동해"
}
```

응답 `TaskOrder`:

| 필드 | 타입 | 설명 |
|---|---|---|
| `task_id` | string | 서버 생성 |
| `instruction` | string | 정규화된 원문 |
| `target_robot_id` | string | 등록된 대상 로봇 |
| `target_object_id` | string 또는 null | 물체 작업이 아니면 null |
| `destination` | union | `ZONE` 또는 `COORDINATE` |
| `status` | enum | 생성 시 `CREATED` |
| `created_at` | datetime | 서버 생성 |

목적지 예:

```json
{"type":"ZONE","zone_id":"A구역"}
```

```json
{
  "type":"COORDINATE",
  "frame_id":"map",
  "position":{"x":1.0,"y":2.0,"z":0.0}
}
```

파서는 로봇, 목적지, 실행 동사를 확실히 식별하지 못하면 추측하지 않고 `422`를 반환한다. 부정문, 조건문, 질문, 과거 서술, 복수 로봇·복수 목적지도 거부한다.

파싱에 실패한 요청은 멱등성 키를 점유하지 않으므로, 같은 키로 수정된 요청을 다시 보낼 수 있다.

### 3.3 TaskOrder 기반 ActionProposal 생성

`POST /task-orders/{task_id}/action-proposals`

선택 헤더:

| 헤더 | 규칙 |
|---|---|
| `Idempotency-Key` | 공백 제외 1~128자. 같은 TaskOrder·키·본문 재요청은 기존 Proposal과 `200`을 반환하며, 같은 키에 다른 본문은 `409` |

요청:

| 필드 | 타입 | 필수 | 규칙 |
|---|---|---:|---|
| `speed_limit` | number | X | 양수. 생략 시 `min(0.4, robot.motion_limits.max_speed)` |
| `manipulation` | enum | 물체 작업만 O | `PICK`, `PLACE`. MOVE_TO에는 지정 불가 |

MOVE_TO 예:

```json
{"speed_limit":0.4}
```

MANIPULATE 예:

```json
{"manipulation":"PLACE","speed_limit":0.3}
```

성공 시 Proposal을 `PROPOSED`로 저장하고 TaskOrder를 `CREATED`에서 `PLANNING`으로 변경한다. 다음 상태에서는 생성하지 않는다.

- TaskOrder가 `CREATED` 또는 `PLANNING`이 아님
- Robot이 없거나 `ERROR`
- 같은 TaskOrder에 `PROPOSED`, `APPROVED`, `EXECUTING`, `HELD` Proposal이 이미 존재
- MANIPULATE에 물체 ID 또는 명시적인 PICK/PLACE가 없음
- MOVE_TO에 물체 또는 manipulation이 지정됨
- TaskOrder v1.1로 안전하게 표현할 수 없는 STOP/RESUME 명령

`GET /task-orders/{task_id}/action-proposals`는 해당 TaskOrder의 Proposal만 배열로 반환한다.

### 3.4 ActionProposal

| 필드 | 타입 | 규칙 |
|---|---|---|
| `proposal_id` | string | 생략 시 서버 생성 |
| `task_id` | string | 공백 불가, 기존 TaskOrder와 일치 |
| `robot_id` | string | 공백 불가, TaskOrder 대상 로봇과 일치 |
| `action_type` | enum | `MOVE_TO`, `MANIPULATE`, `STOP`, `RESUME` |
| `parameters` | object | action_type별 계약 적용 |
| `trajectory` | array 또는 null | 직접 등록 시 필수 키 |
| `frame_id` | string | 공백 불가 |
| `speed_limit` | number | 양수 |
| `model_name` | string | 공백 불가 |
| `model_version` | string | 공백 불가 |
| `status` | enum | 등록 시 서버가 `PROPOSED`로 설정 |
| `proposed_at` | datetime | 생략 시 서버 생성 |

`parameters` 계약:

| action_type | 필수 parameters | 선택 parameters |
|---|---|---|
| `MOVE_TO` | `target_pose`, `speed_limit` | `priority=NORMAL` |
| `MANIPULATE` | `manipulation=PICK|PLACE`, `object_id`, `target_pose` | `priority=NORMAL` |
| `STOP` | 없음 | `priority=NORMAL|SAFETY` |
| `RESUME` | `task_id` | `priority=NORMAL|SAFETY` |

`MOVE_TO parameters.speed_limit`와 최상위 `speed_limit`는 같아야 한다. `SAFETY` priority는 시스템이 발행하는 STOP·RESUME에만 허용한다.

TaskOrder 기반 생성기는 현재 MOVE_TO와 MANIPULATE만 만든다. 외부 VLA는 `POST /action-proposals`로 네 action_type을 직접 등록할 수 있으나, TaskOrder·Robot 존재 및 일치, 상태, 중복 활성 Proposal 검사를 모두 통과해야 한다.

### 3.5 평가와 실행

`POST /action-proposals/{proposal_id}/evaluate`

- `PROPOSED`만 평가할 수 있다.
- 전 규칙 통과 시 `APPROVED`, 하나라도 실패하면 `REJECTED`로 변경한다.
- 판정마다 `APPROVE` 또는 `REJECT` ExecutionLog를 남긴다.

현재 실제 평가 항목:

1. Robot 상태가 `IDLE` 또는 `HOLD`
2. `control_adapter`가 공백이 아님
3. Proposal 속도가 Robot `max_speed` 이하
4. 대상 로봇과 관련되었거나 특정 로봇이 없는 OPEN·CRITICAL HazardEvent가 없음

`POST /action-proposals/{proposal_id}/execute`

- `APPROVED`만 실행할 수 있다.
- 실행 직전 같은 안전 규칙을 다시 평가한다.
- 재평가 실패 시 Proposal을 `HELD`로 바꾸고 `ESTOP` 로그를 남긴 뒤 `409`를 반환한다.
- 통과 시 Proposal을 `EXECUTING`으로 바꾸고 Mock adapter 전달을 나타내는 `EXECUTE` 로그를 남긴다.

### 3.6 HazardEvent

`POST /hazards`

| 필드 | 타입 | 규칙 |
|---|---|---|
| `hazard_id` | string | 생략 시 서버 생성, 중복 불가 |
| `hazard_type` | enum | `WORKER_INTRUSION`, `COLLISION_RISK`, `FIRE`, `SMOKE`, `FALLING_OBJECT`, `UNKNOWN` |
| `severity` | enum | `WARNING`, `CRITICAL` |
| `source_observation_ids` | string[] | 필수 |
| `related_robot_ids` | string[] | 필수. 빈 배열인 CRITICAL은 모든 로봇을 차단 |
| `related_worker_ids` | string[] | 필수 |
| `zone_id` | string | 필수 |
| `status` | enum | 기본 `OPEN`; `OPEN`, `ACKNOWLEDGED`, `RESOLVED` |
| `confidence` | number | 0 이상 1 이하 |
| `detected_at` | datetime | 생략 시 서버 생성 |
| `resolved_at` | datetime 또는 null | 기본 null |

`POST /hazards/{hazard_id}/resolve`는 현재 인증 없이 상태를 `RESOLVED`로 바꾸고 `resolved_at`을 기록한다.

### 3.7 SafetyRule과 ExecutionLog

`GET /safety-rules`는 현재 기본 규칙 두 개를 반환한다.

- `rule-speed-limit`
- `rule-critical-hazard`

`GET /execution-logs`는 평가·실행 순서대로 전체 로그를 반환한다. 로그는 `log_id`, `proposal_id`, `decision`, `rule_results`, `hazard_ids`, `robot_id`, `timestamp`, `result_message`를 포함한다.

## 4. 상태 전이

```text
TaskOrder: CREATED ── proposal 생성/등록 ──> PLANNING

ActionProposal: PROPOSED ── evaluate 통과 ──> APPROVED
                PROPOSED ── evaluate 실패 ──> REJECTED
                APPROVED ── execute 재검증 통과 ──> EXECUTING
                APPROVED ── execute 재검증 실패 ──> HELD
```

`RUNNING`, `COMPLETED`, `FAILED`, `SUCCEEDED`, ResumeTask와 실행 결과 보고 전이는 스키마에는 있으나 현재 HTTP MVP에는 구현되지 않았다.

## 5. 기준 문서와 현재 구현의 차이

| 항목 | 기준 문서의 목표 | v0.2 현재 구현 |
|---|---|---|
| API prefix | `/api/v1` | prefix 없음 |
| Proposal 제출·평가 | 한 경계에서 판정 결과 반환 | 등록, 평가, 실행 API 분리 |
| 객체 조회 | `GET /api/v1/objects/{type}` | Robot, TaskOrder, Proposal, Rule, Log의 개별 API 일부 |
| ResumeTask | 관리자 승인 2단계 복귀 | 미구현, HELD 생성 요청은 `409` |
| 인증·권한 | CRITICAL 해제와 Resume는 관리자 전용 | 미구현 |
| 규칙 SR-01~06 | 속도·구역·거리·위험·상태·센서 공백 | 로봇 상태·adapter·Robot 속도·관련 CRITICAL 위험만 구현 |
| E-Stop | ROS 2 latched 전용 경로 | HTTP 실행 재검증 실패 시 상태·로그만 변경 |
| 상태 구독 | WebSocket `/ws/state` | 미구현 |
| 영속 저장 | Redis 또는 ROS 2 상태 연동 | 인메모리 |
| 판정 시간 | `evaluated_in_ms` 기록 | 미구현 |

이 차이는 스키마 v1.1의 객체 정의를 변경할 사유가 아니라 구현 진척도 차이로 관리한다. 필수 필드나 열거값을 변경해야 할 때만 팀 변경 절차에 따라 스키마 버전을 올린다.

## 6. 검증 기준

```bash
cd backend
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests
```

OpenAPI 경로 수는 12개, operation 수는 14개여야 한다. 코드 변경으로 이 명세와 OpenAPI가 달라지면 같은 PR에서 문서를 함께 수정한다.
