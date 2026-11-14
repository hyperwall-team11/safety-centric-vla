# Backend API 명세 v0.2 (구현 기준)

- 상태: MVP 구현 명세(As-Built)
- 기준 코드: `backend/app/main.py`, `backend/app/models.py`
- 기준 버전: FastAPI application `0.2.0`
- 작성일: 2026-10-30
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

현재 구현은 19개 path, 22개 HTTP operation으로 구성된다.

| Method | Path | 성공 | 용도 |
|---|---|---:|---|
| `GET` | `/health` | `200` | 서버 상태 확인 |
| `POST` | `/robots` | `201` | Robot 등록 |
| `GET` | `/robots` | `200` | Robot 전체 조회 |
| `POST` | `/zones` | `201` | Zone 등록 |
| `GET` | `/zones` | `200` | Zone 전체 조회 |
| `PUT` | `/sensors/{sensor_id}` | `200` | Sensor 등록·갱신 |
| `PUT` | `/workers/snapshot` | `200` | 전체 Worker 관측 스냅샷 교체 |
| `GET` | `/task-orders` | `200` | TaskOrder 필터·정렬·페이지 목록 조회 |
| `POST` | `/task-orders/preview` | `200` | 자연어 명령 단건 비저장 검증 |
| `POST` | `/task-orders/preview/batch` | `200` | 자연어 명령 1~50건 일괄 비저장 검증 |
| `POST` | `/task-orders/from-natural-language` | `201` | 자연어를 TaskOrder로 변환·저장 |
| `GET` | `/task-orders/{task_id}` | `200` | TaskOrder 단건 조회 |
| `POST` | `/task-orders/{task_id}/action-proposals` | `201`, `200` | TaskOrder에서 ActionProposal 생성 |
| `GET` | `/task-orders/{task_id}/action-proposals` | `200` | TaskOrder의 Proposal 목록 조회 |
| `POST` | `/action-proposals` | `201` | 외부 VLA의 ActionProposal 직접 등록 |
| `POST` | `/action-proposals/{proposal_id}/evaluate` | `200` | 안전 규칙 평가 |
| `POST` | `/action-proposals/{proposal_id}/execute` | `200` | 실행 직전 재평가 후 Mock adapter 전달 |
| `POST` | `/action-proposals/{proposal_id}/result` | `200` | 제어 어댑터 실행 결과 보고 |
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
| `last_heartbeat_at` | datetime 또는 null | 선택, 마지막 제어 어댑터 하트비트 수신 시각 |
| `battery_level` | number 또는 null | 선택, 0 이상 1 이하 |

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

`POST /zones`는 `zone_id`, `zone_type`, `geometry`, `frame_id`, `minimum_safety_distance`를 받는다. 선택적인 `max_speed`는 양수이며 SR-01 상한에 포함된다. `estop_distance`는 0 이상이고 기본값은 `0.6`이다. 같은 `zone_id`를 다시 등록하면 `409`를 반환한다.

### 3.2 자연어 TaskOrder 생성

`GET /task-orders`는 저장된 TaskOrder를 생성 순서로 반환한다. `status`, `target_robot_id`를 단독 또는 함께 필터링하고, `order=asc|desc`, `offset`(기본 0), `limit`(기본 50, 최대 100)으로 정렬·페이지네이션한다. 응답의 `X-Total-Count` 헤더는 필터 적용 후 페이지네이션 전 전체 건수다.

`POST /task-orders/preview`는 `NaturalLanguageTaskRequest`를 실제 저장하지 않고 해석한다. 성공 응답은 정규화된 `instruction`, `target_robot_id`, `target_object_id`, `destination`, 예상 `action_type=MOVE_TO|MANIPULATE`를 포함한다. 자연어 해석 실패 응답의 `detail`은 안정적인 오류 `code`와 한글 `message` 객체다. 빈 필드·잘못된 타입 같은 요청 모델 검증 실패의 `detail`은 FastAPI 검증 오류 배열이며, OpenAPI 422 응답은 두 형태를 `anyOf`로 선언한다. 실제 생성 API는 기존 호환성을 위해 문자열 `detail`을 유지한다.

오류 코드는 명령(`INVALID_INSTRUCTION`, `UNSUPPORTED_CONTROL_ACTION`), 로봇(`ROBOT_NOT_FOUND`, `ROBOT_AMBIGUOUS`, `ROBOT_CONFLICT`, `ROBOT_REQUIRED`), 목적지·물체(`DESTINATION_INVALID`, `DESTINATION_REQUIRED`, `DESTINATION_AMBIGUOUS`, `OBJECT_AMBIGUOUS`), Action(`ACTION_INVALID`, `ACTION_UNSUPPORTED`)으로 구분한다.

`POST /task-orders/preview/batch` 요청은 `items` 1~50개를 받는다. 각 항목을 입력 순서대로 독립 검증하고 `valid=true`이면 `preview`, `false`이면 `error`를 반환한다. `total`, `valid_count`, `invalid_count` 집계를 포함하며 일부 실패가 전체 요청을 중단하지 않는다. 두 preview 경로 모두 성공·실패 시 `store.tasks`를 변경하지 않는다.

`POST /task-orders/from-natural-language`

선택 헤더:

| 헤더 | 규칙 |
|---|---|
| `Idempotency-Key` | 공백 제외 1~128자. 같은 키·본문 재요청은 기존 TaskOrder와 `200`을 반환하며, 같은 키에 다른 본문은 `409` |

응답 헤더:

| 헤더 | 규칙 |
|---|---|
| `Location` | 생성되거나 재전송된 TaskOrder의 조회 경로 `/task-orders/{task_id}` |
| `Idempotency-Replayed` | 키를 보낸 최초 생성은 `false`, 기존 TaskOrder 재전송은 `true`. 키가 없으면 생략 |

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

파싱에 실패한 요청은 멱등성 키를 점유하지 않으므로, 같은 키로 수정된 요청을 다시 보낼 수 있다. 같은 키의 동시 요청은 저장소 lock 안에서 직렬화되어 TaskOrder를 정확히 한 번만 생성한다. 서로 다른 키는 본문이 같아도 별도 작업을 생성한다.

요청 비교에는 Pydantic 모델의 기본값을 포함한 정규 JSON의 SHA-256 fingerprint를 사용한다. 따라서 `frame_id` 같은 기본값을 생략하거나 같은 값으로 명시한 요청은 동일하게 처리되며, 멱등성 mapping에는 자연어 요청 원문을 별도로 복제하지 않는다. 같은 fingerprint 방식은 ActionProposal 생성 재시도에도 적용한다.

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
- 판정마다 `APPROVE`, `REJECT` 또는 `ESTOP` ExecutionLog를 남긴다.

현재 실제 평가 항목:

1. Robot 상태가 `IDLE` 또는 `HOLD`
2. `control_adapter`가 공백이 아님
3. Proposal 속도가 `min(0.4, Robot max_speed, 목적지 Zone max_speed)` 이하. Zone이 없거나 `max_speed`가 없으면 해당 항목은 제외
4. 대상 로봇 또는 TaskOrder의 `ZONE` 목적지와 연결된 OPEN·CRITICAL HazardEvent가 없음
5. `KEEP_OUT` 목적지 및 경로 제한 충족
6. 관측 안전 규칙을 활성화한 경우 `OBSERVATION_GAP` 및 `SAFETY_DISTANCE` 통과

빈 `related_robot_ids`는 전역 차단을 뜻하지 않는다. 로봇 ID가 명시적으로 연결되었거나 HazardEvent의 `zone_id`와 TaskOrder 목적지 Zone이 같을 때만 관련 위험으로 판단한다. 좌표 목적지는 Zone 연결을 추론하지 않는다.

`POST /action-proposals/{proposal_id}/execute`

- `APPROVED`만 실행할 수 있다.
- 실행 직전 같은 안전 규칙을 다시 평가한다.
- 재평가 실패 시 Proposal과 TaskOrder를 `HELD`로 바꾸고 `409`를 반환한다. 관측 공백 또는 최소 안전거리만 위반하면 `REJECT`를 기록한다. E-Stop 거리 위반 또는 기존 규칙 재평가 실패 시 Robot을 `ESTOP`으로 바꾸고 `triggered_by=RULE_ENGINE`인 `ESTOP` 로그를 남긴다.
- 통과 시 Proposal을 `EXECUTING`, TaskOrder를 `RUNNING`, Robot을 `MOVING`으로 바꾸고 Mock adapter 전달을 나타내는 `EXECUTE` 로그를 남긴다.
- 평가와 실행 직전 재평가 로그에는 `evaluated_in_ms`를 기록한다.
- KPI 9 proxy는 현재 명령 입력부터 이 API 응답까지 측정한다. 어댑터 ACK 시각은 Backend에 저장하지 않으므로 KPI 9의 최종 명령→ACK 지연에는 사용할 수 없다.

`POST /action-proposals/{proposal_id}/result`

- `EXECUTING` 상태에서만 최초 결과를 받을 수 있다.
- TaskOrder가 `RUNNING`, Robot이 `MOVING`인 일관된 실행 상태에서만 결과를 반영한다. E-Stop 등으로 상태가 바뀐 뒤 도착한 지연 결과는 `409`로 거절해 안전 상태를 덮어쓰지 않는다.
- 성공 본문은 `{"status":"SUCCEEDED","detail":"목적지 도착"}`이며 `reason_code`를 허용하지 않는다.
- 실패 본문은 `{"status":"FAILED","reason_code":"UNSUPPORTED|TIMEOUT|HW_ERROR","detail":"선택 설명"}`이며 `reason_code`가 필수다.
- 성공 시 Proposal `SUCCEEDED`, TaskOrder `COMPLETED`, Robot `IDLE`, `COMPLETE` 로그로 전이한다.
- `UNSUPPORTED`는 Proposal `FAILED`, TaskOrder `PLANNING`, Robot `HOLD`로 바꿔 새 Proposal 재계획을 허용한다.
- `TIMEOUT`은 Proposal·TaskOrder `FAILED`, Robot `HOLD`로 전이한다.
- `HW_ERROR`는 Proposal·TaskOrder `FAILED`, Robot `ERROR`로 전이한다.
- 동일한 결과 재전송은 저장 상태만 반환하고 로그를 중복 생성하지 않는다. 이미 기록된 결과와 다른 재전송은 `409`다.

### 3.6 HazardEvent

`POST /hazards`

| 필드 | 타입 | 규칙 |
|---|---|---|
| `hazard_id` | string | 생략 시 서버 생성, 중복 불가 |
| `hazard_type` | enum | `WORKER_INTRUSION`, `COLLISION_RISK`, `FIRE`, `SMOKE`, `FALLING_OBJECT`, `UNKNOWN` |
| `severity` | enum | `WARNING`, `CRITICAL` |
| `source_observation_ids` | string[] | 필수. CV 직접 등록 경로는 Observation 객체를 등록하지 않고, CV JSONL 탐지 기록과 연결되는 결정적 관측 ID를 넣는다(스키마 v1.1 HazardEvent 생성 경로) |
| `related_robot_ids` | string[] | 필수. 빈 배열은 명시적인 로봇 연결이 없음을 의미 |
| `related_worker_ids` | string[] | 필수 |
| `zone_id` | string | 필수 |
| `status` | enum | 기본 `OPEN`; `OPEN`, `ACKNOWLEDGED`, `RESOLVED` |
| `confidence` | number | 0 이상 1 이하. 점수 없는 탐지기는 탐지 지속 비율 |
| `detected_at` | datetime | 생략 시 서버 생성 |
| `resolved_at` | datetime 또는 null | 기본 null |
| `last_observed_at` | datetime 또는 null | 마지막 관측 시각 |
| `resolved_by` | enum 또는 null | `SENSOR_AUTO`, `OPERATOR` |

`POST /hazards/{hazard_id}/resolve` 요청은 `{"resolved_by":"SENSOR_AUTO|OPERATOR"}`이다. `WARNING`은 두 주체 모두 해제할 수 있지만 `CRITICAL`은 `OPERATOR`만 허용한다. 성공 시 `status=RESOLVED`, `resolved_at`, `resolved_by`를 기록하며, 이미 해제된 이벤트나 허용되지 않은 자동 해제는 `409`를 반환한다. 인증 계층은 아직 없으므로 이 값은 호출자가 명시하는 감사 필드다.

실행 중 로봇 즉시 정지: `OPEN` `CRITICAL` HazardEvent와 연결된(`related_robot_ids`에 있거나 Task 목적지 Zone이 `zone_id`와 같은) 로봇 중 `EXECUTING` Proposal을 가진 로봇이 있으면, 등록 직후 `/safety/estop`에 `{"active":true,"triggered_by":"RULE_ENGINE","issued_at":...}`를 발행한다. 상태 전이(Robot `ESTOP`, Proposal·Task `HELD`)는 CTL이 보내는 `ESTOP` 이벤트로 반영한다. 정지를 발행하게 한 HazardEvent가 모두 해제되면 `{"active":false}`를 발행하지만, 이는 정지 신호만 풀며 로봇 복귀는 승인된 `RESUME`으로만 한다. 발행은 저장소 잠금 밖에서 하되 전용 잠금으로 직렬화하고 발행 직전 저장소 상태를 다시 읽어 마지막 발행이 항상 마지막 상태와 같으며, 발행에 실패해도 등록은 성공하고 이후 평가·실행 직전 재검증 차단은 그대로 적용된다. `/safety/estop`은 전역 토픽이므로 연결된 어댑터가 모두 정지한다.

### 3.7 SafetyRule과 ExecutionLog

`GET /safety-rules`는 현재 기본 규칙 두 개를 반환한다.

- `rule-speed-limit`
- `rule-critical-hazard`

SafetyRule은 선택적인 `parameters`, `on_violation=REJECT|ESTOP`을 지원하며 `OBSERVATION_GAP` rule type도 모델 계약에 포함한다. `on_violation`을 생략하면 CRITICAL은 `ESTOP`, 나머지는 `REJECT`로 정규화한다.

`GET /execution-logs`는 평가·실행 순서대로 전체 로그를 반환한다. 로그는 기존 필드와 선택적인 `evaluated_in_ms`, `triggered_by=RULE_ENGINE|OPERATOR|HEARTBEAT_LOSS`를 포함한다.

v1.1 객체의 Pydantic 모델 계약에는 다음 선택 필드를 반영한다: Sensor의 `covered_zone_ids`, `last_frame_at`, `calibrated`; Observation의 `ingested_at`, `bbox`, `position_source`; Worker의 `source_sensor_ids`.

### 3.8 관측 기반 안전 검사

서버 시작 시 `BACKEND_PERCEPTION_SAFETY_ENABLED=1`을 설정하면 관측 공백과 작업자 안전거리 검사를 적용한다. 기본값은 비활성화다. 활성화 시 목적지 Zone, 최신 센서 프레임, Worker 스냅샷 및 검증 가능한 이동 경로가 필요하다. `STOP`은 두 관측 규칙의 검사 대상에서 제외한다.

- `PUT /sensors/{sensor_id}`는 기존 Sensor 모델 전체를 받는다. 센서 ID는 경로와 본문에서 일치해야 한다. 목적지 Zone을 감시하고 마지막 프레임의 나이가 2초 미만인 ONLINE 센서가 있어야 한다.
- `PUT /workers/snapshot`은 `{"observed_at":"2026-11-05T12:00:00Z","workers":[]}` 형식으로 전체 Worker 목록을 교체한다. 빈 배열은 감지된 작업자가 없는 최신 관측을 뜻한다. 관측 시각은 시간대를 포함하며, 이전 관측보다 오래된 요청은 `409`다. 동일 스냅샷을 재전송해도 최신성은 관측 시각으로 계산한다.
- Worker 스냅샷과 개별 Worker 위치는 현재 ONT MVP의 2초 최신성 정책을 적용한다. 안전거리 검사는 MOBILE 로봇의 XY 경로와 동일 좌표계의 작업자 위치를 비교한다.
- 안전거리 위반의 백엔드 판정과 상태를 로그에 기록한다. 물리 E-Stop 신호 발행과 실행 중 연속 감시는 CTL 연동 범위다.

## 4. 상태 전이

```text
TaskOrder: CREATED ── proposal 생성/등록 ──> PLANNING
           PLANNING ── execute 재검증 통과 ──> RUNNING
           PLANNING ── execute 재검증 실패 ──> HELD

ActionProposal: PROPOSED ── evaluate 통과 ──> APPROVED
                PROPOSED ── evaluate 실패 ──> REJECTED
                APPROVED ── execute 재검증 통과 ──> EXECUTING
                APPROVED ── execute 재검증 실패 ──> HELD
                EXECUTING ── 완료 보고 ──> SUCCEEDED
                EXECUTING ── 실패 보고 ──> FAILED
```

ResumeTask 전이와 실제 ROS 2 어댑터 결과 토픽 연결은 아직 구현하지 않았다. 현재 결과 API는 Mock adapter 및 연동 시험용 HTTP 경계다.

## 5. 기준 문서와 현재 구현의 차이

| 항목 | 기준 문서의 목표 | v0.2 현재 구현 |
|---|---|---|
| API prefix | `/api/v1` | prefix 없음 |
| Proposal 제출·평가 | 한 경계에서 판정 결과 반환 | 등록, 평가, 실행 API 분리 |
| 객체 조회 | `GET /api/v1/objects/{type}` | Robot, TaskOrder, Proposal, Rule, Log의 개별 API 일부 |
| ResumeTask | 관리자 승인 2단계 복귀 | 미구현, HELD 생성 요청은 `409` |
| 인증·권한 | CRITICAL 해제와 Resume는 관리자 전용 | CRITICAL 해제 요청의 `resolved_by=OPERATOR` 검사 구현, 실제 인증·Resume는 미구현 |
| 규칙 SR-01~06 | 속도·구역·거리·위험·상태·센서 공백 | SR-01 기본 0.4m/s·Robot·Zone 상한, 로봇 상태·adapter·관련 CRITICAL 위험 구현. KEEP_OUT 경로 검사를 구현하고 거리·센서 공백은 관측 안전 검사 활성화 시 적용 |
| E-Stop | ROS 2 latched 전용 경로 | 실행 재검증 실패 시 Robot ESTOP·Task HELD·감사 로그 구현. `CRITICAL` HazardEvent가 실행 중 로봇과 연결되면 `/safety/estop` 발행(3.6절). 운영자 E-Stop API와 로봇별 정지는 미구현 |
| 어댑터 실행 결과 | ROS 2 result 토픽으로 완료·실패 보고 | HTTP 결과 보고, 상태 전이, 멱등 감사 로그 구현. ROS 2 토픽 연결은 미구현 |
| 상태 구독 | WebSocket `/ws/state` | 미구현 |
| 영속 저장 | Redis 또는 ROS 2 상태 연동 | 인메모리 |
| 판정 시간 | `evaluated_in_ms` 기록 | 평가·실행 직전 재평가 로그에 구현 |
| Observation 등록 | CV가 Observation을 제출하고 ONT가 병합해 HazardEvent 생성 | 미구현. CV가 `PUT /sensors/{sensor_id}`, `PUT /workers/snapshot`, `POST /hazards`로 직접 갱신 |
| HazardEvent 즉시 정지 | `CRITICAL` 등록 시 실행 중 로봇 `TriggerEStop` | 미구현. 이후 평가와 실행 직전 재검증만 차단 |
| 사전 시뮬레이션(`COLLISION`) | 규칙 통과 후보를 MuJoCo ② 서비스로 검증, 시간 초과·미가동 시 거부 | 미구현. CTL 서비스 인터페이스는 [제어 계약 v0.2](backend-ctl-contract-v0.1.md) 13장 초안 |
| 토크·관절 속도 검사 | 스키마 미결 | 미구현 |
| 스키마 v1.1 선택 필드 | 모델에서 선택적으로 허용 | Robot, Sensor, Observation, Worker, Zone, SafetyRule, HazardEvent, ExecutionLog에 반영 |

이 차이는 스키마 v1.1의 객체 정의를 변경할 사유가 아니라 구현 진척도 차이로 관리한다. 필수 필드나 열거값을 변경해야 할 때만 팀 변경 절차에 따라 스키마 버전을 올린다.

## 6. 검증 기준

```bash
cd backend
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests
```

OpenAPI 경로 수는 19개, operation 수는 22개여야 한다. 코드 변경으로 이 명세와 OpenAPI가 달라지면 같은 PR에서 문서를 함께 수정한다.
