# Backend API 명세 v0.2 (구현 기준)

- 상태: MVP 구현 명세(As-Built)
- 기준 코드: `backend/app/main.py`, `backend/app/models.py`
- 기준 버전: FastAPI application `0.2.0`
- 작성일: 2026-10-30
- 수정일: 2026-11-25
- 기본 URL(로컬): `http://127.0.0.1:8000`
- 대화형 문서: `/docs`
- OpenAPI JSON: `/openapi.json`

이 문서는 현재 저장소에서 실제 호출 가능한 HTTP API를 정의한다. [ontology-schema-v1.1.md](ontology-schema-v1.1.md)는 객체·상태·안전 의미의 기준이고, 이 문서는 그중 현재 백엔드 MVP가 구현한 전송 경로와 동작의 기준이다. 개발계획서의 `/api/v1/...` 표기 중 통합 객체 조회 경로를 우선 적용했으며, 나머지 기존 경로는 하위 호환을 위해 기존 경로를 유지한다.

## 1. 공통 규약

- 요청과 응답 본문은 JSON이며 `Content-Type: application/json`을 사용한다.
- 정의되지 않은 요청 필드는 허용하지 않는다(`extra=forbid`).
- 시간은 UTC ISO 8601 문자열로 반환한다.
- 거리 단위는 m, 속도 단위는 m/s이다.
- ID는 문자열이다. 서버 생성 ID는 `task-`, `proposal-`, `hazard-`, `log-` 접두사와 UUID를 사용한다.
- 기본 개발 모드에서는 인증이 비활성화된다. `BACKEND_API_TOKEN_CONFIG`를 설정하면 역할별 Bearer API 토큰 인증을 적용한다. CV 안전 확인은 `SENSOR|CV`, 관리자 위험 해제와 RESUME 제안 생성은 `OPERATOR|ADMIN` 역할만 허용한다.
- `/api/v1` 접두사는 통합 객체 조회 API에만 적용되어 있다.
- 기본 저장소는 인메모리이다. `BACKEND_STORE=redis`를 지정하면 Redis에 상태를 영속 저장한다.
- Redis 모드에서 연결, 분산 잠금, 스냅샷 읽기·쓰기가 실패하면 fail-closed로 `503`을 반환한다. 저장은 Lua 스크립트로 잠금 토큰 비교와 스냅샷 교체를 원자적으로 수행하므로 만료되거나 재획득된 잠금은 저장에 사용할 수 없다. 일반적인 읽기 실패나 잠금 경합은 E-Stop을 발행하지 않으며, 실행 명령 발행 후 상태 커밋이 실패한 경우에만 물리 상태와 Backend 상태 불일치를 막기 위해 E-Stop을 발행한다.
- 다중 Backend의 E-Stop 발행은 Redis 분산 잠금으로 직렬화하며 시작 시 저장된 정지 상태를 CTL에 다시 발행한다. Redis Pub/Sub 변경 알림은 다른 인스턴스의 `/ws/state` 구독자 갱신에 사용한다.

### 공통 오류

| 상태 코드 | 의미 | 대표 응답 |
|---|---|---|
| `401` | 인증이 활성화된 환경에서 Bearer 토큰이 없거나 유효하지 않음 | `{"detail":"유효한 Bearer API 토큰이 필요합니다."}` |
| `404` | 요청한 Robot, TaskOrder, ActionProposal 또는 HazardEvent가 없음 | `{"detail":"...찾을 수 없습니다."}` |
| `409` | 현재 상태에서 실행 불가, ID 중복, 활성 Proposal 또는 멱등성 충돌 | `{"detail":"..."}` |
| `422` | JSON 스키마 위반, 자연어 해석 실패 또는 안전한 제안 생성 불가 | FastAPI 검증 오류 배열 또는 `{"detail":"..."}` |
| `503` | Redis 저장소를 안전하게 사용할 수 없음, VLA 추론 서비스 연결 실패·시간 초과, 제어 어댑터 전송 실패 | `{"detail":"..."}` |

## 2. 구현 엔드포인트

현재 구현은 25개 path, 31개 HTTP operation으로 구성된다.

| Method | Path | 성공 | 용도 |
|---|---|---:|---|
| `GET` | `/health` | `200` | 서버 상태 확인 |
| `GET` | `/auth/me` | `200` | 인증 활성화 여부와 현재 요청자 확인 |
| `GET` | `/api/v1/objects/{type}` | `200` | 저장된 온톨로지 객체를 타입별로 통합 조회 |
| `POST` | `/robots` | `201` | Robot 등록 |
| `GET` | `/robots` | `200` | Robot 전체 조회 |
| `POST` | `/physical-objects` | `201` | PhysicalObject 등록 |
| `GET` | `/physical-objects` | `200` | PhysicalObject 전체 조회 |
| `POST` | `/zones` | `201` | Zone 등록 |
| `GET` | `/zones` | `200` | Zone 전체 조회 |
| `PUT` | `/sensors/{sensor_id}` | `200` | Sensor 등록·갱신 |
| `GET` | `/workers/snapshot` | `200`, `404` | 최신 전체 Worker 관측 스냅샷 조회 |
| `PUT` | `/workers/snapshot` | `200` | 전체 Worker 관측 스냅샷 교체 |
| `GET` | `/task-orders` | `200` | TaskOrder 필터·정렬·페이지 목록 조회 |
| `POST` | `/task-orders/preview` | `200` | 자연어 명령 단건 비저장 검증 |
| `POST` | `/task-orders/preview/batch` | `200` | 자연어 명령 1~50건 일괄 비저장 검증 |
| `POST` | `/task-orders/from-natural-language` | `201` | 자연어를 TaskOrder로 변환·저장 |
| `GET` | `/task-orders/{task_id}` | `200` | TaskOrder 단건 조회 |
| `POST` | `/task-orders/{task_id}/resume-proposal` | `201` | HELD TaskOrder의 안전 복귀 Proposal 생성 |
| `POST` | `/task-orders/{task_id}/action-proposals` | `201`, `200` | TaskOrder에서 ActionProposal 생성 |
| `GET` | `/task-orders/{task_id}/action-proposals` | `200` | TaskOrder의 Proposal 목록 조회 |
| `POST` | `/action-proposals` | `201` | 외부 VLA의 ActionProposal 직접 등록 |
| `POST` | `/action-proposals/{proposal_id}/evaluate` | `200` | 안전 규칙 평가 |
| `POST` | `/action-proposals/{proposal_id}/execute` | `200` | 실행 직전 재평가 후 Mock adapter 전달 |
| `GET` | `/action-proposals/{proposal_id}/ack` | `200` | 어댑터 ACK 수신 상태와 KPI 9 원시 지연 조회 |
| `POST` | `/action-proposals/{proposal_id}/result` | `200` | 제어 어댑터 실행 결과 보고 |
| `GET` | `/hazards` | `200` | HazardEvent 필터 목록 조회 |
| `POST` | `/hazards` | `201` | HazardEvent 등록 |
| `POST` | `/hazards/{hazard_id}/clearance` | `200` | CV 안전 해제 근거 등록 |
| `POST` | `/hazards/{hazard_id}/resolve` | `200` | HazardEvent 해제 |
| `GET` | `/safety-rules` | `200` | 활성 안전 규칙 조회 |
| `GET` | `/execution-logs` | `200` | 전체 ExecutionLog 조회 |

### 2.1 인증

`BACKEND_API_TOKEN_CONFIG`가 없으면 기존 개발 호환을 위해 인증을 적용하지 않는다. 값이 있으면 각 항목은 `token_sha256`, `subject`, `role`을 가져야 하며, 서버는 요청의 평문 토큰을 SHA-256으로 계산해 상수 시간 비교한다. 설정에는 평문 토큰을 넣지 않는다. 빈 배열, 잘못된 digest, 중복 digest, 빈 요청자·역할은 서버 시작 오류다.

인증 활성화 시 `/health`, `/docs`, `/openapi.json`, `/redoc`, `/console`과 정적 asset만 공개한다. 나머지 HTTP API는 `Authorization: Bearer <token>`이 필요하고, 실패 시 `401`과 `WWW-Authenticate: Bearer`를 반환한다. `GET /auth/me`는 비활성화 상태에서는 `enabled=false`를 반환하며, 활성화 상태에서는 유효한 토큰으로 호출해야 `subject`와 `role`을 반환한다.

`WS /ws/state`는 비브라우저 클라이언트의 `Authorization` 헤더 또는 브라우저의 WebSocket subprotocol `bearer.<base64url(token)>`을 받는다. 브라우저는 함께 `state.v1`을 요청하고 서버는 인증 성공 시 `state.v1`만 선택한다. 토큰 query parameter는 URL·로그 노출을 막기 위해 지원하지 않는다. 인증 실패 시 연결 전 WebSocket code `4401`로 닫는다.

인증 계층은 검증된 요청자의 `subject`와 `role`을 HTTP `request.state.auth_principal` 또는 WebSocket `state.auth_principal`에 전달한다. CV 안전 확인은 `SENSOR|CV`, 관리자 위험 해제와 RESUME 제안 생성은 `OPERATOR|ADMIN`만 호출할 수 있다. 개발 모드에서는 호환성을 위해 역할 검사를 생략한다. React 운영 콘솔은 토큰을 sessionStorage에만 저장해 HTTP와 WebSocket에 전달하고 역할에 따라 관리자 버튼을 제어한다.

### 2.2 통합 객체 조회

`GET /api/v1/objects/{type}`은 현재 인메모리 저장소에 보관된 객체를 저장 순서대로 배열에 담아 반환한다. `type`은 대소문자를 구분하며 다음 온톨로지 클래스명만 허용한다.

- `Robot`
- `Sensor`
- `Worker`
- `PhysicalObject`
- `Zone`
- `TaskOrder`
- `ActionProposal`
- `HazardEvent`
- `SafetyRule`
- `ExecutionLog`

`offset`(기본 0)과 `limit`(기본 50, 최대 100)으로 조회 범위를 제한하며, 응답의 `X-Total-Count`는 페이지네이션 전 전체 건수다. Worker를 제외한 타입은 저장된 객체가 없으면 `200`과 빈 배열 `[]`을 반환한다. Worker 관측 스냅샷이 한 번도 들어오지 않았으면 `404`를 반환하고, 관측 결과 작업자가 없으면 `200`, `[]`, 관측 시각 `X-Observed-At`을 반환한다. 지원하지 않는 타입이나 대소문자가 다른 값은 `422`를 반환한다. `SafetyRule`은 저장소가 빈 경우 기본 규칙을 생성한 뒤 반환한다. `Observation`은 현재 저장 계층이 없어 조회 대상에서 제외한다.

이 API는 저장소에 보관된 조회 시점의 스냅샷을 제공할 뿐 현재 안전 상태나 관측 최신성을 보장하지 않는다. 안전 판단에 사용할 때는 Robot의 `updated_at`·`last_heartbeat_at`, Sensor의 `last_frame_at`, Worker의 `X-Observed-At`과 각 Worker의 `last_seen_at`을 기존 최신성 정책과 비교해야 하며, Action 실행 승인에는 반드시 Backend의 평가·실행 API를 사용한다.

WebSocket `WS /ws/state`는 연결 직후와 상태 변경 시 전체 상태 스냅샷을 전송한다. 연속 변경은 200ms 전송 간격 안에서 최신 전체 상태 하나로 합치며, 상태 변경이 없어도 15초마다 같은 형식의 스냅샷을 heartbeat로 전송한다. 이 heartbeat는 상태 구독 연결 유지용이며 ROS 2 안전 heartbeat나 Sensor·Worker 관측 최신성을 대신하지 않는다. WebSocket 경로는 HTTP OpenAPI path·operation 수에 포함하지 않는다.

스냅샷은 `type=STATE_SNAPSHOT`, 단조 증가하는 `revision`, 서버 전송 시각 `observed_at`과 함께 Robot, Sensor, Worker, PhysicalObject, Zone, TaskOrder, ActionProposal, HazardEvent, SafetyRule 및 최근 ExecutionLog 100건을 포함한다. Worker 관측이 한 번도 없으면 `workers=null`, 관측 결과 작업자가 없으면 `workers=[]`이며, `workers_observed_at`으로 관측 시각을 제공한다. 관측 유효성은 WebSocket 연결 여부가 아니라 기존 `last_frame_at`, `workers_observed_at` 기준으로 판단한다. 전체 ExecutionLog는 `GET /execution-logs`로 조회한다. 현재는 재연결 시 delta 복구 없이 항상 전체 스냅샷을 보낸다.

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
| `manipulation` | enum | X | `PICK`, `PLACE`. 물체 작업의 수동 단계 생성에 사용하며, 생략하면 다음 단계를 자동 선택 |

MOVE_TO 예:

```json
{"speed_limit":0.4}
```

MANIPULATE 예:

```json
{"manipulation":"PLACE","speed_limit":0.3}
```

대상 물체가 없는 작업은 목적지 `MOVE_TO`를 생성한다. 대상 물체가 있는 작업은 등록된 PhysicalObject pose와 같은 TaskOrder의 성공 이력을 사용해 `물체 접근 MOVE_TO → PICK → 목적지 MOVE_TO → PLACE` 중 아직 완료되지 않은 다음 단계 하나를 생성한다. 클라이언트는 각 단계의 결과가 반영된 뒤 같은 API에 빈 JSON 객체 `{}`을 다시 보내면 된다. `manipulation`을 지정하면 자동 선택 대신 해당 조작 단계를 생성한다.

성공 시 Proposal을 `PROPOSED`로 저장하고 TaskOrder를 `CREATED`에서 `PLANNING`으로 변경한다. 다음 상태에서는 생성하지 않는다.

- TaskOrder가 `CREATED` 또는 `PLANNING`이 아님
- Robot이 없거나 `ERROR`
- 같은 TaskOrder에 `PROPOSED`, `APPROVED`, `EXECUTING`, `HELD` Proposal이 이미 존재
- 자동 물체 작업인데 대상 PhysicalObject가 등록되지 않았거나 이동 불가
- 수동 MANIPULATE에 물체 ID 또는 명시적인 PICK/PLACE가 없음
- MOVE_TO에 물체 또는 manipulation이 지정됨
- TaskOrder v1.1로 안전하게 표현할 수 없는 STOP/RESUME 명령

VLA Provider 추론은 위험 이벤트·Worker 관측·Robot 상태 갱신을 지연시키지 않도록
저장소 잠금 밖에서 실행한다. Backend는 추론 전 TaskOrder·Robot·Zone을 스냅샷화하고,
저장 직전에 현재 문맥과 활성 Proposal·멱등성 기록을 다시 검증한다. 추론 중
문맥이 변경되면 `409`로 거부하고 아무 제안도 저장하지 않는다. Provider 예외는 `503`으로
정규화하며 TaskOrder 상태를 변경하지 않는다.

`GET /task-orders/{task_id}/action-proposals`는 해당 TaskOrder의 Proposal만 배열로 반환한다.

`POST /task-orders/{task_id}/resume-proposal`은 `HELD` TaskOrder와 `HOLD` 또는 `ESTOP`
Robot을 대상으로 안전 복귀용 `RESUME` Proposal을 생성한다. 인증이 활성화된 환경에서는
`OPERATOR` 또는 `ADMIN` 역할만 호출할 수 있다. 대상 로봇이나 TaskOrder 목적지 Zone과 연결된
미해제 `CRITICAL` HazardEvent가 있거나 처리 중인 Proposal이 있으면 `409`를 반환한다. 생성한
Proposal은 `priority=SAFETY`이며 일반 Proposal과 동일하게 평가·실행 절차를 거친다.

외부 VLA가 `POST /action-proposals`로 등록하는 제안은 요청 모델 검증 후 TaskOrder 문맥과도
대조한다. `action_type`, `robot_id`, `parameters.target_pose`, MANIPULATE의 `object_id`,
`frame_id`가 TaskOrder·Robot과 다르면 모델이 작업 문맥을 바꾼 것으로 보고 `422`로 거부한다.
속도·경로의 안전성은 이 문맥 검증에서 승인하지 않고 이후 Action 제출 기준 평가에서 판정한다.
`MOVE_TO trajectory`가 있으면 시작점은 Robot 현재 위치와 0.05m 이내여야 한다. 좌표
목적지 또는 같은 좌표계의 Zone `navigation_target`이 있으면 끝점도 해당 좌표와 0.05m
이내여야 한다. 물리 좌표가 없는 Zone은 `target_pose`의 Zone ID 일치만 검증한다.

자동 생성한 `MOVE_TO`는 로봇의 현재 위치와 좌표 목적지 또는
`Zone.geometry.navigation_target`을 잇는 직선 `trajectory`를 채운다.
출발점과 목적지의 좌표계가 일치해야 한다. 목적지 좌표가 없거나 좌표계가
다르면 경로는 `null`이며, 경로 검사가 필요한 경우 안전 게이트가 거절한다.

이 직선은 백엔드의 사전 검사(KEEP_OUT 경로 검사 등)에 사용하는 예상 경로다. CTL 이동
제어기는 목표 방향과 5° 이내로 제자리 회전한 뒤 방향을 보정하며 전진하므로, 실제 궤적은
현재 위치에서 목표까지의 직선에서 0.05m 이내에 머문다(제어 계약 13.1절). 자동 생성·외부 직접 등록 여부와 관계없이 실행 직전
Robot 위치와 예상 경로 출발점은 CTL diff-drive의
도착 판정과 같은 0.05m 허용 오차로 비교하며, 이를 넘으면 Proposal을 거절하고 재계획한다.
두 허용 오차를 합치면 실제 궤적은 예상 경로에서 최대 약 0.1m 벗어날 수 있다. 이동 중 실시간 위치를 안전 검증에 반영하는 작업은 후속 연동 범위다.

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

TaskOrder 기반 생성기와 외부 VLA 직접 등록은 현재 `MOVE_TO`와 `MANIPULATE`만 허용한다. `STOP`·`RESUME`은 일반 VLA 제안이 아니라 별도 안전 제어 경로에서만 생성한다. 직접 등록은 TaskOrder·Robot 존재 및 일치, 상태, 중복 활성 Proposal 검사를 모두 통과해야 한다.

### 3.5 평가와 실행

`POST /action-proposals/{proposal_id}/evaluate`

- `PROPOSED`만 평가할 수 있다.
- 전 규칙 통과 시 `APPROVED`, 하나라도 실패하면 `REJECTED`로 변경한다.
- 판정마다 `APPROVE`, `REJECT` 또는 `ESTOP` ExecutionLog를 남긴다.

현재 실제 평가 항목:

1. Robot 상태가 `IDLE` 또는 `HOLD`. 단, `RESUME`은 `ESTOP`도 허용
2. `control_adapter`가 공백이 아님
3. Proposal 속도가 `min(0.4, Robot max_speed, 목적지 Zone max_speed)` 이하. Zone이 없거나 `max_speed`가 없으면 해당 항목은 제외
4. 대상 로봇 또는 TaskOrder의 `ZONE` 목적지와 연결된 미해제 `CRITICAL` HazardEvent가 없음
5. `KEEP_OUT` 목적지 및 경로 제한 충족
6. 관측 안전 규칙을 활성화한 경우 `OBSERVATION_GAP` 및 `SAFETY_DISTANCE` 통과
7. 사전 시뮬레이션을 활성화한 경우 1~6을 모두 통과한 후보만 MuJoCo ② `COLLISION` 판정 `PASS`(3.9절)

빈 `related_robot_ids`는 전역 차단을 뜻하지 않는다. 로봇 ID가 명시적으로 연결되었거나 HazardEvent의 `zone_id`와 TaskOrder 목적지 Zone이 같을 때만 관련 위험으로 판단한다. 좌표 목적지는 Zone 연결을 추론하지 않는다.

`POST /action-proposals/{proposal_id}/execute`

- `APPROVED`만 실행할 수 있다.
- 실행 직전 같은 안전 규칙을 다시 평가한다.
- 재평가 실패 시 Proposal과 TaskOrder를 `HELD`로 바꾸고 `409`를 반환한다. 관측 공백, 최소 안전거리, 사전 시뮬레이션(`rule-collision`)만 위반하면 `REJECT`를 기록한다. E-Stop 거리 위반 또는 기존 규칙 재평가 실패 시 Robot을 `ESTOP`으로 바꾸고 `triggered_by=RULE_ENGINE`인 `ESTOP` 로그를 남긴다.
- 자동 생성한 MOVE_TO의 출발 위치나 좌표계가 실행 직전 백엔드에 저장된 로봇 위치와 다르면 전송을 차단한다. 출발점만 불일치하면 Proposal을 `REJECTED`, TaskOrder를 `PLANNING`으로 바꿔 새 제안을 생성할 수 있다.
- 통과 시 Proposal을 `EXECUTING`, TaskOrder를 `RUNNING`, Robot을 `MOVING`으로 바꾸고 Mock adapter 전달을 나타내는 `EXECUTE` 로그를 남긴다.
- 평가와 실행 직전 재평가 로그에는 `evaluated_in_ms`를 기록한다. 사전 시뮬레이션을 호출하면 그 대기 시간도 포함한다.
- 실행 요청 전송 실패 시 ACK 대기 기록을 제거한다. 발행 성공 시 1초 기한의 ACK 대기 기록을 만든다.

`GET /action-proposals/{proposal_id}/ack`

- `PENDING`: 발행 후 1초 기한 내에 ACK를 기다리는 상태다.
- `ACKNOWLEDGED`: 기한 내 첫 ACK를 수신한 상태다.
- `TIMED_OUT`: 1초 기한 내 ACK를 받지 못했거나 기한 후 첫 ACK가 도착한 상태다.
- `PENDING`의 기한 경과는 이 조회 API 호출 또는 늦은 ACK 처리 시점에 `TIMED_OUT`으로 확정한다. 백그라운드 타이머는 사용하지 않는다.
- 응답은 `command_received_at`, `dispatched_at`, `timeout_at`, CTL 원본 `adapter_ack_at`, Backend `ack_received_at`, `command_to_ack_ms`, `dispatch_to_ack_ms`, `duplicate_count`를 포함한다.
- `command_to_ack_ms`는 Backend의 명령 HTTP 수신 시각과 ACK 콜백 수신 시각으로 계산해 분리 호스트 간 시계 오차를 피한다. CTL `timestamp`는 원본 근거로 별도 보존한다.
- `dispatch_to_ack_ms`는 Backend 발행 시각과 ACK 콜백 수신 시각의 차이로, 실행 승인 대기를 제외한 순수 전송 진단값이다. 1초 ACK 상태 기한도 같은 `dispatched_at`을 기준으로 한다.
- 중복 ACK는 첫 수신 시각과 지연을 덮어쓰지 않고 `duplicate_count`만 증가시킨다. 알 수 없는 Proposal, Robot 불일치, 시각 형식 오류 ACK는 무시한다.
- ACK 상태는 전송 진단값이며 Proposal·TaskOrder·Robot 실행 상태를 변경하지 않는다. 완료·실패 전이는 실제 CTL `RESULT`만 처리한다.
- 장시간 운영에서 메모리가 계속 늘지 않도록 최신 ACK 기록은 최대 1000개를 유지하며, 한도에 도달하면 실행 중이 아닌 가장 오래된 기록을 먼저 제거한다.
- Proposal이 없거나 실행되지 않아 ACK 대기 기록이 없으면 `404`다.

`POST /action-proposals/{proposal_id}/result`

- `EXECUTING` 상태에서만 최초 결과를 받을 수 있다.
- TaskOrder가 `RUNNING`, Robot이 `MOVING`인 일관된 실행 상태에서만 결과를 반영한다. E-Stop 등으로 상태가 바뀐 뒤 도착한 지연 결과는 `409`로 거절해 안전 상태를 덮어쓰지 않는다.
- 성공 본문은 `{"status":"SUCCEEDED","detail":"목적지 도착"}`이며 `reason_code`를 허용하지 않는다.
- 실패 본문은 `{"status":"FAILED","reason_code":"UNSUPPORTED|TIMEOUT|HW_ERROR","detail":"선택 설명"}`이며 `reason_code`가 필수다.
- 일반 행동 성공 시 Proposal `SUCCEEDED`, TaskOrder `COMPLETED`, Robot `IDLE`, `COMPLETE` 로그로 전이한다. `RESUME` 성공은 Robot `IDLE`, TaskOrder `PLANNING`으로 전이해 중단된 작업의 다음 Proposal을 다시 생성하게 한다.
- `UNSUPPORTED`는 Proposal `FAILED`, TaskOrder `PLANNING`, Robot `HOLD`로 바꿔 새 Proposal 재계획을 허용한다.
- `TIMEOUT`은 Proposal·TaskOrder `FAILED`, Robot `HOLD`로 전이한다.
- `HW_ERROR`는 Proposal·TaskOrder `FAILED`, Robot `ERROR`로 전이한다.
- 동일한 결과 재전송은 저장 상태만 반환하고 로그를 중복 생성하지 않는다. 이미 기록된 결과와 다른 재전송은 `409`다.

### 3.6 HazardEvent

`GET /hazards`는 저장된 HazardEvent를 등록 순서로 반환한다. `status`, `severity`, `robot_id`를 단독 또는 함께 필터링할 수 있다. `robot_id`는 `related_robot_ids`에 해당 ID가 포함된 이벤트만 반환한다. 필터와 일치하는 이벤트가 없으면 빈 배열을 반환한다.

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

`POST /hazards/{hazard_id}/clearance`는 `WORKER_INTRUSION` 위험에 대한 CV 안전 해제 근거를
등록한다. 요청은 최신 `observed_at`, 위험 Zone을 감시하는 전체 `source_sensor_ids`, 2초 이상의
`clear_duration_seconds`를 포함해야 한다. 등록 센서가 모두 `ONLINE`이고 최신 프레임을 가졌으며,
최신 Worker 스냅샷에 해당 Zone의 `DANGER` 작업자가 없어야 한다. 성공 시 HazardEvent를
`ACKNOWLEDGED`로 바꾸고 근거를 저장한다. 인증이 활성화된 환경에서는 `SENSOR` 또는 `CV`
역할만 호출할 수 있다.

`POST /hazards/{hazard_id}/resolve` 요청은 `{"resolved_by":"SENSOR_AUTO|OPERATOR"}`이다.
`WARNING`은 두 주체 모두 해제할 수 있지만 `CRITICAL`은 `OPERATOR`만 허용한다. 특히
`CRITICAL WORKER_INTRUSION`은 위 CV 안전 해제 근거가 등록되어 `ACKNOWLEDGED`가 된 뒤에만
해제할 수 있다. 인증이 활성화된 환경에서는 `OPERATOR` 해제에 `OPERATOR` 또는 `ADMIN`,
`SENSOR_AUTO` 해제에 `SENSOR` 또는 `CV` 역할이 필요하다. 성공 시 `status=RESOLVED`,
`resolved_at`, `resolved_by`를 기록하며, 이미 해제된 이벤트나 조건을 충족하지 않은 요청은
`409`를 반환한다.

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
- `GET /workers/snapshot`은 마지막으로 저장된 `{"observed_at", "workers"}` 전체 스냅샷을 반환한다. 한 번도 저장되지 않았으면 `404`다.
- Worker 스냅샷과 개별 Worker 위치는 현재 ONT MVP의 2초 최신성 정책을 적용한다. 안전거리 검사는 MOBILE 로봇의 XY 경로와 동일 좌표계의 작업자 위치를 비교한다.
- 안전거리 위반의 백엔드 판정과 상태를 로그에 기록한다. 물리 E-Stop 신호 발행과 실행 중 연속 감시는 CTL 연동 범위다.

### 3.9 사전 시뮬레이션(`COLLISION`)

서버 시작 시 `BACKEND_TWIN_SIMULATION_ENABLED=1`을 설정하면 스키마 v1.1 5장 9번 규칙(`rule-collision`)을 적용한다. 기본값은 비활성화다. 이미 규칙이 있는 저장소에도 빠진 기본 규칙은 추가하며 기존 규칙은 덮어쓰지 않는다. 호출 주소는 `BACKEND_TWIN_SIMULATE_URL`(기본 `http://127.0.0.1:8100/twin/simulate`)이며 요청·응답은 [제어 계약 v0.2](backend-ctl-contract-v0.1.md) 13장을 따른다.

- 평가와 실행 직전 재검증 모두 다른 규칙을 전부 통과한 후보만 시뮬레이션한다. `STOP`·`RESUME`은 대상에서 제외한다.
- 요청의 `target_pose`·`speed_limit`은 실제 CTL 전송값(4.1절, `ZONE`은 `navigation_target`)이고, `state`에는 등록된 모든 Robot, 마지막 Worker 스냅샷, 모든 Zone을 넣는다. Worker 스냅샷이 없으면 `workers: null`을 보내며 트윈은 이를 `ERROR`로 거부한다.
- `PASS`가 아닌 모든 `result`, 시간 초과(잠정 1.0초, #50 확정 전), 연결 실패, 2xx가 아닌 응답, 형식 오류(필수 필드 누락, 알 수 없는 `result`, 요청과 다른 `snapshot_id`)는 위반이다. `MANIPULATE`는 계약 13.1절 지원 범위가 아니어서 호출 없이 위반으로 기록한다.
- CTL 어댑터는 방향을 맞춘 뒤 현재 위치에서 `target_pose`까지 직선 0.05 m 이내로 움직이고, 트윈은 같은 제어기로 그 궤적을 시뮬레이션한다. `trajectory`가 있으면 두 점(출발점·끝점)이고 끝점이 `target_pose`와 0.05 m 이내일 때만 시뮬레이션하고, 경유점이 있거나 끝점이 다르면 호출 없이 위반으로 기록한다.
- 결과는 `rule-collision` RuleResult의 `reason`에 `result`, `snapshot_id`, 최소 작업자 거리, 첫 접촉 시각, 시뮬레이션 구간, 서비스 처리 시간으로 남긴다.
- `snapshot_id`는 Robot(상태·자세), Worker 스냅샷(관측 시각·위치), 열린 HazardEvent, Zone으로 계산한다. 평가 완료 시점과 실행 직전에 각각 다시 계산해 시뮬레이션한 값과 같은 `PASS`만 쓴다. 실행 직전에는 평가 단계 `PASS`와 같으면 재사용하고, 다르면 다시 시뮬레이션한다. 시뮬레이션 중 상태가 바뀌면 평가·실행 직전 각각 최대 2회까지 다시 하고, 그래도 다르면 거부한다. 위반 결과는 상태가 바뀌어도 그대로 차단한다.
- HTTP 응답을 기다리는 동안 저장소 잠금을 잡지 않는다. 대기 중 등록된 HazardEvent 등은 응답 반영 시 규칙을 다시 평가해 결정에 반영한다.
- 실행 직전 사전 시뮬레이션 위반은 로봇이 아직 움직이지 않았으므로 Robot을 `ESTOP`으로 바꾸지 않는다.

## 4. 상태 전이

```text
TaskOrder: CREATED ── proposal 생성/등록 ──> PLANNING
           PLANNING ── execute 재검증 통과 ──> RUNNING
           PLANNING ── execute 재검증 실패 ──> HELD
           RUNNING ── 중간 단계 성공 ──> PLANNING
           RUNNING ── 단일 작업 또는 최종 PLACE 성공 ──> COMPLETED
           HELD ── RESUME 성공 ──> PLANNING

ActionProposal: PROPOSED ── evaluate 통과 ──> APPROVED
                PROPOSED ── evaluate 실패 ──> REJECTED
                APPROVED ── execute 재검증 통과 ──> EXECUTING
                APPROVED ── execute 재검증 실패 ──> HELD
                EXECUTING ── 완료 보고 ──> SUCCEEDED
                EXECUTING ── 실패 보고 ──> FAILED
```

대상 물체가 있는 운반 TaskOrder는 여러 ActionProposal을 순차 실행한다. Task Executor는 등록된 PhysicalObject와 성공한 Proposal 이력을 보고 다음 `MOVE_TO` 또는 `MANIPULATE`를 하나씩 자동 생성한다. OpenVLA 조작 Proposal과 Navigation Planner의 `MOVE_TO` Proposal을 같은 `task_id`로 직접 등록하는 방식도 지원하지만, 처리 중 상태(`PROPOSED`, `APPROVED`, `EXECUTING`)의 Proposal은 TaskOrder당 하나만 허용한다. `HELD`는 정지 이력으로 남되 RESUME 성공 뒤 다음 단계 생성을 막지 않는다. 물체 접근, `PICK`, 목적지 `MOVE_TO` 성공은 개별 Proposal만 `SUCCEEDED`로 끝내고 TaskOrder를 `PLANNING`으로 돌린다. 최종 `MANIPULATE/PLACE` 성공이 전체 TaskOrder를 `COMPLETED`로 만든다.

ResumeTask 전이는 안전 해제 후 `RESUME` Proposal의 평가·실행·성공 보고로 구현했다. ROS 2
모드에서는 기존 dispatcher와 CTL 결과 이벤트 경계를 그대로 사용하며, Mock 연동 시험에서는
HTTP 결과 API로 같은 상태 전이를 검증한다.

## 5. 기준 문서와 현재 구현의 차이

| 항목 | 기준 문서의 목표 | v0.2 현재 구현 |
|---|---|---|
| API prefix | `/api/v1` | 통합 객체 조회에 적용, 나머지 경로는 prefix 없음 |
| Proposal 제출·평가 | 한 경계에서 판정 결과 반환 | 등록, 평가, 실행 API 분리 |
| 객체 조회 | `GET /api/v1/objects/{type}` | 저장 계층이 있는 10개 객체 타입의 통합 조회 구현 |
| ResumeTask | 관리자 승인 2단계 복귀 | CV 안전 해제 근거, 운영자 Hazard 해제, `RESUME` Proposal 평가·실행으로 구현 |
| 인증·권한 | CRITICAL 해제와 Resume는 관리자 전용 | 선택형 역할별 API 토큰 인증 구현. CV 안전 해제는 `SENSOR|CV`, CRITICAL 해제와 Resume는 `OPERATOR|ADMIN`으로 분리 |
| 규칙 SR-01~06 | 속도·구역·거리·위험·상태·센서 공백 | SR-01 기본 0.4m/s·Robot·Zone 상한, 로봇 상태·adapter·관련 CRITICAL 위험 구현. KEEP_OUT 경로 검사를 구현하고 거리·센서 공백은 관측 안전 검사 활성화 시 적용 |
| E-Stop | ROS 2 latched 전용 경로 | 실행 재검증 실패 시 Robot ESTOP·Task HELD·감사 로그 구현. `CRITICAL` HazardEvent가 실행 중 로봇과 연결되면 `/safety/estop` 발행(3.6절). 운영자 E-Stop API와 로봇별 정지는 미구현 |
| 어댑터 실행 결과 | ROS 2 result 토픽으로 완료·실패 보고 | HTTP 결과 보고, 상태 전이, 멱등 감사 로그 구현. ROS 2 토픽 연결은 미구현 |
| 상태 구독 | WebSocket `/ws/state` | 전체 상태 스냅샷 구독과 15초 heartbeat 구현, delta 복구는 미구현 |
| 영속 저장 | Redis 또는 ROS 2 상태 연동 | 인메모리 기본, Redis 스냅샷·분산 잠금 선택 가능 |
| 판정 시간 | `evaluated_in_ms` 기록 | 평가·실행 직전 재평가 로그에 구현 |
| Observation 등록 | CV가 Observation을 제출하고 ONT가 병합해 HazardEvent 생성 | 미구현. CV가 `PUT /sensors/{sensor_id}`, `PUT /workers/snapshot`, `POST /hazards`로 직접 갱신 |
| HazardEvent 즉시 정지 | `CRITICAL` 등록 시 실행 중 로봇 `TriggerEStop` | 미구현. 이후 평가와 실행 직전 재검증만 차단 |
| 사전 시뮬레이션(`COLLISION`) | 규칙 통과 후보를 MuJoCo ② 서비스로 검증, 시간 초과·미가동 시 거부 | `BACKEND_TWIN_SIMULATION_ENABLED=1`일 때 `MOVE_TO` 호출·Fail-Closed·`snapshot_id` 재검증 구현(3.9절). 기본 비활성화, 시간 제한은 #50 확정 전 잠정값, `MANIPULATE`는 계약 확정 전까지 차단 |
| 토크·관절 속도 검사 | 스키마 미결 | 미구현 |
| 스키마 v1.1 선택 필드 | 모델에서 선택적으로 허용 | Robot, Sensor, Observation, Worker, Zone, SafetyRule, HazardEvent, ExecutionLog에 반영 |

이 차이는 스키마 v1.1의 객체 정의를 변경할 사유가 아니라 구현 진척도 차이로 관리한다. 필수 필드나 열거값을 변경해야 할 때만 팀 변경 절차에 따라 스키마 버전을 올린다.

## 6. 검증 기준

```bash
cd backend
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests
```

OpenAPI 경로 수는 25개, operation 수는 31개여야 한다. 코드 변경으로 이 명세와 OpenAPI가 달라지면 같은 PR에서 문서를 함께 수정한다.
