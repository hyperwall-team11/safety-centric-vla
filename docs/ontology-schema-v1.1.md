# Ontology Schema v1.1

- 상태: 개발 기준선 (v1 호환 보완판)
- 확정일: v1 2026-09-23 / v1.1 2026-09-28
- 적용 범위: MuJoCo 기반 안전 중심 VLA MVP (2026-10-11 Isaac Sim에서 변경)
- 변경 원칙: v1의 객체명과 필수 필드는 유지한다. 이후 변경은 선택 필드 추가 또는 v2 제안으로 관리한다.
- v1.1 변경 요약: **객체명·필수 필드 변경 없음.** 선택 필드 추가, 열거값 추가(하위 호환), 규칙 수치화, 개발계획서 v0.2와의 용어 정합만 반영. 추가 항목은 `[v1.1]` 표기.

## 0. 변경 이력 [v1.1]

| 버전 | 일자 | 변경 내용 | 사유 |
|---|---|---|---|
| v1 | 2026-09-23 | 최초 기준선 확정 | - |
| v1.1 | 2026-09-28 | 선택 필드 12건·열거값 2건 추가, 승인 규칙 8번 추가, 통합 규칙 수치화, E-Stop 경로 원칙 추가 | 개발계획서 v1.0(실행판) 정합 및 SR-06·KPI 측정 구현 가능화 |
| v1.1 (적용 범위 수정) | 2026-10-11 | 적용 범위의 시뮬레이터를 Isaac Sim → MuJoCo로 변경. 객체·필드·열거값·규칙 변경 없음 | 팀 시뮬레이터 변경 결정 |

변경 절차 [v1.1]: 스키마 변경은 GitHub 이슈로 제안 → 영향 모듈 담당 전원 승인 → 버전 태그 후 반영한다. 필수 필드 변경·삭제, 열거값 삭제는 v2 제안으로만 다룬다.

## 1. 공통 규칙

- 모든 ID는 문자열 UUID 또는 팀이 정한 고유 문자열을 사용한다.
- 시간은 ISO 8601 UTC 형식으로 기록한다.
- 위치와 자세에는 반드시 `frame_id`를 포함한다.
- 거리 단위는 m, 속도는 m/s, 각속도는 rad/s를 사용한다.
- AI의 출력은 로봇 제어기로 직접 전달하지 않고 반드시 `ActionProposal`로 등록한다.
- 로봇은 상태가 `APPROVED`인 제안만 실행할 수 있다.
- [v1.1] `frame_id` 표준 값: `world`(전역), `map`, `base_link_<robot_id>`(로봇 기준), `camera_<sensor_id>`(영상 픽셀 좌표). 전역 비교가 필요한 판단(안전거리·구역)은 `world` 또는 구역 매핑을 기준으로 한다.
- [v1.1] 시각은 "발생 시각"과 "수신 시각"을 구분한다. 판단·병합은 발생 시각을, 지연 측정은 두 시각의 차를 사용한다.

## 2. 핵심 객체

### Robot

로봇의 종류와 현재 실행 상태를 표현한다.

필수 필드:

- `robot_id`
- `robot_type`: `HUMANOID`, `QUADRUPED`, `MANIPULATOR`, `MOBILE`
- `control_adapter`
- `status`: `IDLE`, `MOVING`, `HOLD`, `ESTOP`, `ERROR`
- `pose`: `frame_id`, `position`, `orientation`
- `velocity`
- `motion_limits`: 최대 속도와 가속도
- `updated_at`

선택 필드 [v1.1]:

- `last_heartbeat_at`: 제어 어댑터의 마지막 하트비트 수신 시각. 1초(5주기) 초과 시 어댑터는 자율적으로 `HOLD`에 진입한다(9장 E-Stop 원칙).
- `battery_level`: 0.0~1.0. 임계 미만이면 승인 규칙 2번(로봇 상태)에서 재계획 불가로 취급할 수 있다.

### Sensor

로봇 카메라, CCTV, 드론 카메라를 표현한다.

필수 필드:

- `sensor_id`
- `sensor_type`: `ROBOT_CAMERA`, `CCTV`, `DRONE_CAMERA`
- `source_uri`
- `frame_id`
- `pose`
- `status`: `ONLINE`, `OFFLINE`, `DEGRADED`
- `mounted_robot_id`: 로봇 탑재형이 아니면 `null`

선택 필드 [v1.1]:

- `covered_zone_ids`: 이 센서의 관측 범위가 커버하는 Zone 목록. **승인 규칙 8번(관측 공백)의 판단 근거.**
- `last_frame_at`: 마지막 프레임 수신 시각. 2초 무수신 시 `status`를 `DEGRADED`로 전환한다.
- `calibrated`: 월드 좌표 변환(외부 파라미터) 확보 여부. `false`이면 이 센서의 Observation은 구역 매핑 방식으로만 위치를 표현한다.

### Observation

각 영상 센서가 탐지한 원시 인식 결과를 표현한다.

필수 필드:

- `observation_id`
- `sensor_id`
- `timestamp`
- `detected_type`: `WORKER`, `ROBOT`, `OBJECT`, `FIRE`, `SMOKE`, `OBSTACLE`
- `position`
- `frame_id`
- `confidence`
- `track_id`: 추적하지 못하면 `null`

선택 필드 [v1.1]:

- `ingested_at`: 수집 서버 도착 시각. `timestamp`(촬영)와의 차로 파이프라인 지연을 측정한다(KPI 1·2 증빙).
- `bbox`: 영상 픽셀 좌표계의 경계 상자 `[x, y, w, h]`. 시각화·디버깅용.
- `position_source`: `WORLD`(캘리브레이션 변환) | `ZONE_MAPPED`(구역 매핑) | `IMAGE_ONLY`. 병합 시 거리 조건 적용 가능 여부를 결정한다(6장).

### Worker

작업자의 위치와 안전 상태를 표현한다.

필수 필드:

- `worker_id`
- `pose`
- `current_zone_id`
- `safety_status`: `SAFE`, `WARNING`, `DANGER`, `UNKNOWN`
- `last_seen_at`
- `confidence`

선택 필드 [v1.1]:

- `source_sensor_ids`: 현재 위치 추정에 기여한 센서 목록. 다중 시점 확인 여부를 표현한다.

### PhysicalObject

이동 대상 물체와 장애물을 표현한다.

필수 필드:

- `object_id`
- `object_type`
- `pose`
- `current_zone_id`
- `movable`
- `updated_at`

### Zone

작업구역과 금지구역을 표현한다.

필수 필드:

- `zone_id`
- `zone_type`: `WORK`, `KEEP_OUT`, `SAFE`, `TRANSIT`
- `geometry`
- `frame_id`
- `minimum_safety_distance`

선택 필드 [v1.1]:

- `max_speed`: 구역 내 속도 상한. 승인 규칙 3번은 `min(robot.motion_limits, proposal.speed_limit, zone.max_speed)`를 기준으로 판단한다. 미지정 시 로봇 한계만 적용.
- `estop_distance`: 즉시 비상정지 반경(기본 0.6). `minimum_safety_distance`(경고·거부 반경, 기본 1.2)와 구분한다.

### TaskOrder

사용자가 요청한 작업을 표현한다.

필수 필드:

- `task_id`
- `instruction`
- `target_robot_id`
- `target_object_id`: 대상 물체가 없으면 `null`
- `destination`
- `status`: `CREATED`, `PLANNING`, `RUNNING`, `HELD`, `COMPLETED`, `FAILED`
- `created_at`

### ActionProposal

VLA가 생성한 실행 전 행동 제안을 표현한다.

필수 필드:

- `proposal_id`
- `task_id`
- `robot_id`
- `action_type`: `MOVE_TO`, `MANIPULATE`, `STOP`, `RESUME`
- `parameters`
- `trajectory`: 궤적이 필요하지 않으면 `null`
- `frame_id`
- `speed_limit`
- `model_name`
- `model_version`
- `status`: `PROPOSED`, `APPROVED`, `REJECTED`, `EXECUTING`, `SUCCEEDED`, `FAILED`, `HELD`
- `proposed_at`

용어 정합 [v1.1]:

- 집기·놓기는 별도 열거값이 아니라 `action_type=MANIPULATE` + `parameters.manipulation`: `PICK` | `PLACE`로 표현한다. 개발계획서 v1.0은 이 방식을 그대로 사용한다.
- 어댑터 수준 `HOLD` 명령은 ActionProposal이 아니라 `TriggerEStop` 경로로 전달되는 제어 신호이다. 제안 객체의 정지 상태는 `status=HELD`로만 표현한다.
- `parameters.priority` [v1.1 선택]: `NORMAL` | `SAFETY`. `SAFETY`는 시스템이 발행하는 정지·복귀 계열에만 사용한다.

### SafetyRule

Action 승인 여부를 판단하는 규칙을 표현한다.

필수 필드:

- `rule_id`
- `rule_type`: `SPEED`, `COLLISION`, `KEEP_OUT`, `SAFETY_DISTANCE`, `HAZARD_STATE`
- `target_robot_type`: 공통 규칙이면 `ALL`
- `condition`
- `severity`: `INFO`, `WARNING`, `CRITICAL`
- `enabled`

선택 필드·열거값 [v1.1]:

- `rule_type`에 `OBSERVATION_GAP` 추가(하위 호환) — 작업 구역을 커버하는 `ONLINE` 센서가 없으면 위반. "보이지 않으면 움직이지 않는다"(Fail-Closed).
- `parameters`: 조건식이 참조하는 임계값(예: `{"warning_distance": 1.2, "estop_distance": 0.6}`). Zone·Robot 속성이 있으면 그것이 우선하고, 본 필드는 기본값이다.
- `on_violation`: `REJECT` | `ESTOP`. 미지정 시 `severity=CRITICAL`이면 `ESTOP`, 그 외 `REJECT`로 동작한다. (예: 안전거리 규칙은 1.2m 미만 REJECT, 0.6m 미만 ESTOP의 2단 규칙으로 등록)

### HazardEvent

하나 이상의 Observation을 통합한 위험 사건을 표현한다.

필수 필드:

- `hazard_id`
- `hazard_type`: `WORKER_INTRUSION`, `COLLISION_RISK`, `FIRE`, `SMOKE`, `FALLING_OBJECT`, `UNKNOWN`
- `severity`: `WARNING`, `CRITICAL`
- `source_observation_ids`
- `related_robot_ids`
- `related_worker_ids`
- `zone_id`
- `status`: `OPEN`, `ACKNOWLEDGED`, `RESOLVED`
- `confidence`
- `detected_at`
- `resolved_at`: 미해결이면 `null`

선택 필드 [v1.1]:

- `last_observed_at`: 마지막 관측 시각. 자동 해제(TTL) 판단 기준(6장).
- `resolved_by`: `SENSOR_AUTO` | `OPERATOR` | `null`. 해제 주체를 감사 이력에 남긴다. `CRITICAL`은 `SENSOR_AUTO` 해제를 허용하지 않는다.

용어 정합 [v1.1]: 심각도는 본 스키마의 2등급(`WARNING`/`CRITICAL`) 체계를 표준으로 하며, 개발계획서 v1.0도 이를 그대로 사용한다. E-Stop 트리거는 `CRITICAL`만 해당한다. (구 계획서 v0.2의 3등급 표기는 Low·Medium → `WARNING`, High → `CRITICAL`로 사상되어 폐기됨)

### ExecutionLog

행동 제안부터 실행 결과까지의 판단 이력을 표현한다.

필수 필드:

- `log_id`
- `proposal_id`
- `decision`: `APPROVE`, `REJECT`, `ESTOP`, `EXECUTE`, `COMPLETE`, `FAIL`
- `rule_results`: 규칙별 통과 여부와 사유
- `hazard_ids`
- `robot_id`
- `timestamp`
- `result_message`

선택 필드 [v1.1]:

- `evaluated_in_ms`: 제출 수신부터 판정 완료까지 소요 시간. KPI(판정 지연 p95 ≤ 50ms)의 원시 데이터.
- `triggered_by`: `RULE_ENGINE` | `OPERATOR` | `HEARTBEAT_LOSS`. `ESTOP` 결정의 발원지를 구분해 감사한다.

## 3. 객체 관계

| 주체 | 관계 | 대상 |
|---|---|---|
| Sensor | `MOUNTED_ON` | Robot |
| Sensor | `PRODUCES` | Observation |
| Sensor | `COVERS` [v1.1] | Zone |
| Observation | `DESCRIBES` | Worker, Robot, PhysicalObject |
| Observation | `FUSED_INTO` | HazardEvent |
| Worker, Robot, PhysicalObject | `LOCATED_IN` | Zone |
| HazardEvent | `OCCURRED_IN` [v1.1] | Zone |
| TaskOrder | `ASSIGNED_TO` | Robot |
| TaskOrder | `GENERATES` | ActionProposal |
| ActionProposal | `TARGETS` | Robot, PhysicalObject |
| ActionProposal | `EVALUATED_BY` | SafetyRule |
| ActionProposal | `BLOCKED_BY` | HazardEvent |
| ActionProposal | `RECORDED_AS` | ExecutionLog |

## 4. 온톨로지 Action

### SubmitActionProposal

VLA 출력을 `PROPOSED` 상태로 등록한다. 필수 필드가 없거나 로봇이 `ERROR` 상태이면 제출을 거부한다.

### EvaluateAction

활성화된 `SafetyRule`을 적용해 제안을 `APPROVED` 또는 `REJECTED`로 변경한다. 규칙별 판단 결과를 `ExecutionLog`에 기록한다. [v1.1] 판정 소요 시간을 `evaluated_in_ms`에 기록한다.

### ExecuteAction

`APPROVED` 상태이며 대상 로봇의 제어 어댑터가 준비된 경우에만 실행한다. 실행 직전에 열린 `CRITICAL` 위험이 발견되면 실행하지 않는다.

### TriggerEStop

`CRITICAL` 위험 이벤트가 발생하거나 실행 중 안전 규칙을 위반하면 로봇 상태를 `ESTOP`으로 변경하고 현재 작업을 `HELD`로 변경한다. [v1.1] 발원지(`RULE_ENGINE`/`OPERATOR`/`HEARTBEAT_LOSS`)를 `ExecutionLog.triggered_by`에 기록하고, 신호는 9장의 전용 경로로 발행한다.

### ResolveHazard

위험 조건이 사라졌음을 센서 또는 관리자가 확인하면 `HazardEvent`를 `RESOLVED`로 변경한다. [v1.1] 해제 주체를 `resolved_by`에 기록한다. `CRITICAL`은 관리자(`OPERATOR`) 해제만 허용한다.

### ResumeTask

관련 위험이 모두 `RESOLVED`이고 로봇에 다른 오류가 없을 때만 작업 재개를 허용한다. 기존 제안을 그대로 실행하지 않고 필요하면 새로운 `ActionProposal`을 생성한다.

## 5. 핵심 승인 규칙

Action은 다음 조건을 모두 만족해야 승인한다.

1. 대상 로봇과 TaskOrder가 존재한다.
2. 로봇 상태가 `IDLE` 또는 재계획 가능한 `HOLD` 상태이다.
3. 제안 속도가 로봇의 `motion_limits`와 `speed_limit`을 넘지 않는다. [v1.1] 구역에 `max_speed`가 있으면 그 값도 상한에 포함한다.
4. 예상 경로가 `KEEP_OUT` 구역과 교차하지 않는다.
5. 예상 경로와 작업자 사이의 거리가 구역별 최소 안전거리 이상이다. [v1.1] `estop_distance` 미만이면 거부가 아니라 즉시 `TriggerEStop`을 호출한다.
6. 대상 로봇이나 작업구역에 연결된 열린 `CRITICAL` HazardEvent가 없다.
7. 로봇 종류에 맞는 `control_adapter`가 존재한다.
8. [v1.1] 작업 대상 구역을 `COVERS`하는 센서 중 `ONLINE` 상태가 1개 이상 존재한다. 없으면 Fail-Closed 원칙에 따라 거부한다.

조건 하나라도 실패하면 제안을 `REJECTED`로 변경한다. 실행 중 4~6, 8번 조건을 위반하면 `TriggerEStop`을 호출한다.

## 6. 이벤트 통합 규칙

- 같은 시간 구간에 동일한 종류의 위험을 가까운 위치에서 탐지하면 하나의 HazardEvent로 병합한다. [v1.1] 기준 수치: 발생 시각 차 1.0초 이내이고, 두 관측이 모두 `position_source=WORLD`이면 거리 1.5m 이내, 그 외에는 동일 `zone_id`일 때 병합한다.
- 병합된 이벤트에는 모든 `source_observation_ids`를 저장한다. [v1.1] `confidence`는 관측값의 최대치를 사용하고, 서로 다른 센서 2개 이상이 확인한 이벤트는 심각도 상향을 검토한다.
- 여러 센서의 판단이 충돌하면 더 높은 위험 등급을 우선한다. (Fail-Closed)
- [v1.1] 유지·해제: 마지막 관측(`last_observed_at`) 후 3초간 이벤트를 유지한다. `WARNING`은 관측 소스 전체에서 15프레임 연속 미탐지 시 `SENSOR_AUTO`로 자동 해제할 수 있다. `CRITICAL`은 자동 해제하지 않고 `ResolveHazard`(관리자) 승인으로만 해제한다.
- CCTV와 로봇 카메라 연동을 MVP에 포함한다.
- 드론 카메라는 동일한 Sensor·Observation 구조를 사용하되 실제 연동은 확장 범위로 둔다.

## 7. 이기종 제어 원칙

온톨로지와 VLA는 로봇 제조사별 제어 명령을 직접 생성하지 않는다. 공통 Action을 생성하면 `control_adapter`가 로봇별 ROS 2 Action 또는 Topic으로 변환한다.

- 휴머노이드: 이동, 자세, 매니퓰레이션 명령으로 변환
- 사족로봇: 목표 위치, 이동 속도, 보행 모드 명령으로 변환
- 매니퓰레이터: 관절 또는 말단 자세 명령으로 변환

로봇을 추가할 때는 온톨로지 객체와 안전 검증 로직을 변경하지 않고 새로운 제어 어댑터를 추가하는 것을 원칙으로 한다.

[v1.1] 어댑터 공통 규약:

- 어댑터는 수신 즉시 ACK를 반환하고, 완료·실패 시 결과(성공 여부, 사유 코드 `UNSUPPORTED`/`TIMEOUT`/`HW_ERROR`)를 보고한다.
- 지원하지 않는 `action_type`·`parameters` 조합은 실행 거부가 아닌 `UNSUPPORTED` 응답으로 알리고, 상위 계층이 재계획한다.
- 신규 어댑터는 적합성 시험(HOLD 수신→정지 지연 측정, 미승인 Action 직접 주입 시 거부 확인)을 통과한 뒤 통합한다.

## 8. v1 완료 기준

- 모든 모듈이 위 객체명과 필수 필드를 사용한다.
- VLA 출력이 ActionProposal로 변환된다.
- CCTV와 로봇 영상의 Observation이 HazardEvent로 통합된다.
- 승인되지 않은 Action은 ROS 2 제어 계층으로 전달되지 않는다.
- 제어 어댑터를 교체해도 공통 Action 형식이 유지된다.
- 모든 판정과 실행 결과가 ExecutionLog로 남는다.
- [v1.1] 판정 소요·파이프라인 지연이 선택 필드로 기록되어 성능지표를 로그만으로 산출할 수 있다.
- [v1.1] 관측 공백(커버 센서 부재) 상황에서 제안이 거부됨을 시험으로 확인한다.

## 9. E-Stop 경로 원칙 [v1.1 신설]

- `TriggerEStop` 신호는 안전 검증 서버 API를 경유하지 않는 ROS 2 전용 latched 토픽으로 발행한다. 검증 서버가 정지해도 비상정지는 가능해야 한다.
- 트리거는 세 경로를 갖는다: 규칙 엔진(`RULE_ENGINE`), 운영자 수동(`OPERATOR`), 하트비트 손실(`HEARTBEAT_LOSS`).
- 검증 서버는 어댑터로 5Hz 하트비트를 발행한다. 어댑터가 1초(5주기) 동안 수신하지 못하면 자율적으로 `HOLD`에 진입한다.
- 복귀는 2단계로만 가능하다: 관련 `CRITICAL` HazardEvent의 `ResolveHazard` 승인 → `ResumeTask` 승인. 두 판단 모두 ExecutionLog에 남는다.
