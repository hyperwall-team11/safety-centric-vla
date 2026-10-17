# 온톨로지 기반 안전검사 프로그램 — MuJoCo MVP v1.1

행동 제안과 최신 현장 정보를 JSON으로 받아 RDF 관계 그래프를 만들고, 안전 규칙별 판정과 근거를 반환하는 독립 Python 프로그램입니다. 기존 백엔드 파일은 수정하지 않았습니다.

## 1. 먼저 실행하기

Python 3.11 또는 3.12 권장. 터미널에서 압축을 해제한 프로젝트 폴더로 이동합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
python demo.py
```

Windows PowerShell의 가상환경 활성화는 `.venv\Scripts\Activate.ps1`입니다.

예상 결과:

```text
normal: APPROVE, can_execute=True, requires_estop=False
intrusion: REJECT, can_execute=False, requires_estop=True
insufficient: INDETERMINATE, can_execute=False, requires_estop=False
offline: REJECT, can_execute=False, requires_estop=False
```

`demo_results/`에 실제 입력 JSON, 판정 JSON, RDF Turtle 그래프를 저장합니다. 합성 시험 데이터이며 Florence/VLA에서 수신한 실제 결과가 아닙니다. 샘플의 빈 작업자 목록은 '정상 처리한 영상에 작업자가 없고 현장 정보가 완전하다'고 가정한 시험입니다. 실제 서비스에서 빈 목록만으로 이 가정을 해서는 안 됩니다.

## 2. API 실행

```bash
uvicorn ontology_safety.api:app --host 127.0.0.1 --port 8001
```

- API 문서: http://127.0.0.1:8001/docs
- 상태: GET `/health`
- 안전검사: POST `/evaluate`, Content-Type: application/json

다른 터미널에서 같은 가상환경을 활성화하고 다음을 실행합니다. 최신성 검사 때문에 예제 파일을 **생성 직후** 전송해야 합니다.

```bash
python examples/make_examples.py
curl -X POST http://127.0.0.1:8001/evaluate -H 'Content-Type: application/json' --data-binary @examples/intrusion.json
```

샘플 파일을 오래 열어본 뒤 재사용하면 최신성 검사에 실패하는 것이 정상입니다. 날짜를 임의로 현재로 바꾸는 것은 실제 관측 갱신이 아닙니다. 운영에서는 백엔드가 실제 최신 정보로 요청해야 합니다.

서버 없이 파일 검사:

```bash
python examples/make_examples.py
python -m ontology_safety examples/normal.json --graph inspected_graph.ttl
```

## 3. 입력 계약

요청은 다음 상위 항목을 갖습니다. 정확한 예시는 `examples/normal.json`과 `/docs`를 참고하세요.

| 항목 | 내용 |
|---|---|
| proposal | 사용자가 준 models.py의 ActionProposal 형식 |
| robots | 현재 Robot 목록 |
| tasks | TaskOrder 목록; task_id와 대상 로봇 확인 |
| hazards | HazardEvent 목록; resolved_by 등 선택 필드 추가 |
| zones | Zone 목록; GeoJSON Polygon, max_speed, estop_distance |
| sensors | Sensor 목록; covered_zone_ids, last_frame_at, last_processed_at |
| workers | 현재 Worker 목록; world 위치와 last_seen_at |
| adapters | 신뢰할 수 있는 어댑터 등록/지원/준비/하트비트 정보 |
| context | 스냅샷 시각, 목록 완전성, 로봇 외곽 반경, HOLD 재계획 허용 여부 |

`base_models.py`는 제공받은 models.py의 복사본입니다. `contracts.py`가 서비스 입출력에 필요한 선택 필드와 요청 포장을 추가합니다. 원본 스키마의 필수 필드·열거값은 변경하지 않았습니다. 추가 API 계약은 팀 검토가 필요한 제안입니다.

### 궤적 형식

기존 `trajectory: list[dict]`의 내부 형식을 이 서비스에서는 다음처럼 정합니다.

```json
[
  {"position": {"x": 0, "y": 0, "z": 0}, "time_from_start_s": 0},
  {"position": {"x": 1, "y": 0, "z": 0}, "time_from_start_s": 5}
]
```

- 좌표는 모두 `world`, 단위 m, 시간은 상대 초.
- 시간은 0에서 시작하고 엄격히 증가.
- 궤적은 웨이포인트 사이의 직선 보간을 가정하며 실제 어댑터도 동일한 경로·시간 정책을 따라야 합니다.
- 시작점은 현재 로봇 위치와 일치해야 하고, 끝점은 목표 구역 안 또는 목표 좌표와 일치해야 합니다.
- 지금 예제의 `mujoco_world`는 자동으로 `world`라고 간주하지 않습니다. 통합 담당자가 실제 동일 좌표임을 확인하고 이름을 매핑해야 합니다.
- 실제 VLA의 관절·말단 행동 출력은 VLA 변환기가 변환해야 합니다. 임의 VLA 출력을 바로 실행하지 않습니다.

### 완전성·시각·센서 계약

- `hazards_complete`, `workers_complete`, `zones_complete` 기본값은 false. 신뢰할 수 있는 백엔드가 완전한 최신 상태를 수집했을 때만 true로 설정합니다. VLA가 주장할 수 있는 값이 아닙니다.
- 빈 hazard 목록 자체는 안전의 증거가 아닙니다. 목록 완전성, 센서 정상성, 나머지 규칙을 모두 확인합니다.
- 모든 날짜는 시간대가 있는 UTC. 평가 기준 시각은 서버 시각입니다.
- `snapshot_at`, 로봇 `updated_at`, 센서 프레임·처리 시각, 작업자 last_seen_at은 2초 이내여야 합니다. `proposed_at`은 미래 시각을 검사하지만 생성 후 2초 경과만으로 만료시키지 않습니다. 어댑터 하트비트는 1초 이내. 미래 시각 허용 오차는 0.25초입니다. 로봇 동특성에 맞는 실서비스 주기는 별도 검증해야 합니다.
- `last_processed_at`은 정상 처리된 **프레임의 촬영 시각**입니다. 모델 처리 완료 시각이 아닙니다. 유효한 0명 검출도 정상 처리 프레임으로 기록할 수 있습니다. 지금은 최신 프레임까지 처리가 끝난 경우만 인정하는 보수적인 정책입니다.
- `Sensor.pose`와 `Sensor.frame_id`는 스키마 정의를 따릅니다. 센서의 픽셀 좌표를 로봇의 월드 좌표와 거리 비교하지 않습니다.
- Worker 목록은 백엔드/인식 프로그램이 관리합니다. 미검출=퇴장으로 처리하지 말고, 가림·누락·추적 유실을 UNKNOWN/관측 상태로 반영해야 합니다.

## 4. 실제 온톨로지 사용 부분

`ontology.ttl`에 클래스와 관계를 정의하고, 요청마다 새 RDF 그래프를 만듭니다.

```text
ActionProposal --TARGETS--> Robot
ActionProposal --AFFECTS--> Zone
HazardEvent --OCCURRED_IN--> Zone
HazardEvent --RELATED_TO--> Robot
Sensor --COVERS--> Zone
```

- R06은 SPARQL로 `행동→영향 구역←위험` 또는 `행동→로봇←위험` 관계를 조회합니다. **related_robot_ids가 비어 있는 미해결 CRITICAL 위험은 구역과 무관하게 모든 로봇을 차단**하여 백엔드 v0.2의 의미를 유지합니다.
- R08은 SPARQL로 `센서→관측 구역`과 센서 상태를 조회합니다.
- 결과 그래프에는 EVALUATED_BY, BLOCKED_BY, RECORDED_AS도 남습니다.
- 단순 JSON 이름 비교만 하는 구현이 아닙니다. 다만 OWL 자동 추론기, SWRL, SHACL을 사용하지 않습니다. **RDF/OWL 어휘 + SPARQL 관계 조회 + Python 정책/기하 계산** 방식입니다.
- 거리·교차는 Shapely로 계산합니다. 이런 수치 연산까지 온톨로지만으로 처리하는 구조는 아닙니다.
- 스키마 전체의 객체를 구현한 것은 아닙니다. 현재 심사에 필요한 부분만 RDF로 매핑합니다. Observation→Hazard 생성/병합은 이 서비스 앞단의 역할입니다.

## 5. 규칙과 범위

| 규칙 | 현재 구현 |
|---|---|
| R01 | 로봇·작업 존재, 대상 로봇 일치, 작업과 제안 상태 |
| R02 | IDLE 또는 백엔드가 명시적으로 재계획 가능하다고 확인한 HOLD |
| R03 | 로봇·제안·구역 속도 상한 및 시간 궤적의 최대 구간 속도 |
| R04 | 로봇 외곽 반경을 고려한 전체 선분 경로와 KEEP_OUT 접촉/교차 |
| R05 | 현재 작업자 점 위치와 전체 선분 경로의 최소 거리에서 로봇 반경을 제외한 여유 거리 |
| R06 | 전체 로봇 차단 위험 또는 관련 로봇·영향 구역의 OPEN/ACKNOWLEDGED CRITICAL 위험 |
| R07 | 어댑터 등록, 로봇 종류, 준비 상태, 동작 지원, 최신 하트비트 |
| R08 | 모든 영향 구역에 ONLINE 센서와 최신 정상 처리 프레임 |

스키마의 원칙을 초기 평면 이동 실험용으로 구체화한 고정 정책입니다. 클라이언트가 SafetyRule/condition을 보내 정책을 끄거나 바꿀 수 없습니다. 임의 condition 문자열을 eval하지 않습니다. 규칙 변경은 코드·테스트·POLICY_VERSION 변경으로 관리합니다.

**승인 가능한 범위: MOBILE + MOVE_TO, z=0, world 좌표, 원형 로봇 외곽, 정적 작업자 점 위치와 Polygon 구역.** MANIPULATE, STOP, RESUME, 3D 로봇팔 궤적 등은 지원하지 않아 실행 불가를 반환합니다. 비상정지 명령을 이 일반 행동 검사 API에 넣어 승인받는 구조가 아닙니다.

작업 지시의 의미적 충실성, 로봇 전체 링크 충돌, 가속도/동역학, 정지거리, 움직이는 작업자의 미래 위치, 센서 가림, 실제 하드웨어 제동은 검증하지 않습니다. 이 MVP의 APPROVE는 명시된 모델과 입력 가정 안에서의 통과이며 물리적 안전 보장이 아닙니다. 로봇팔을 통합하려면 로봇 링크 형상과 3D 충돌·거리 계산기를 추가해야 합니다.

구역이 겹치는 경우 가장 보수적인 속도/거리 임계값을 적용합니다. 작업 영향 구역은 목적지만이 아니라 로봇 외곽이 닿는 경로 전체에서 도출합니다. 모든 이동 영역이 선언된 구역들의 합집합 안에 있어야 합니다.

## 6. 결과 처리와 기존 백엔드 연결

- `APPROVE`: 모든 필수 검사 PASS. can_execute=true.
- `REJECT`: 하나 이상 FAIL. can_execute=false.
- `INDETERMINATE`: FAIL은 없지만 정보 부족/미지원 UNKNOWN 존재. can_execute=false.
- 관련 CRITICAL 위험 또는 비상정지 거리 미달이면 requires_estop=true. MOVING 상태에서 R04~R06·R08을 통과하지 못해도 정지 요청 플래그를 반환합니다. 이는 **정지 요청 필요 판정**이며 이 프로그램이 물리적 정지 신호를 보냈다는 뜻이 아닙니다.
- decision은 행동 승인 결과, requires_estop는 독립적인 제어 요청 플래그입니다.

응답에는 규칙별 PASS/FAIL/UNKNOWN, evidence_ids, hazard_ids, 영향 구역, 정책 버전, 입력 해시, 판정 시간, 기존 ExecutionLog 형식이 있습니다. INDETERMINATE는 기존 Decision enum에 없으므로 호환 ExecutionLog에서는 REJECT로 기록하고 reason과 result_message에 구분합니다. 서비스의 기본 호환 로그는 APPROVE/REJECT로 기록합니다. backend_bridge는 evaluate에서는 APPROVE/REJECT, execute 재검사 실패에서는 ESTOP/HELD/409로 변환합니다. ESTOP 로그는 신호 발행 완료를 뜻하지 않습니다. 백엔드는 원본 응답도 저장해야 상태·근거·시간을 잃지 않습니다. 이 서비스는 자체적으로 로그를 영속 저장하지 않습니다.

`ontology_safety/backend_client.py`에 호출·실패 차단 코드가 있고, `backend_bridge.py`에 기존 백엔드 객체를 요청으로 바꾸는 연결 함수가 있습니다. **상세 연결 절차와 v0.2의 차이는 `BACKEND_INTEGRATION.md`를 먼저 읽으세요.** 기존 evaluate_proposal()에 결과 객체만 끼워 넣으면 되는 구조는 아닙니다. 센서·구역·작업자·어댑터를 모아 최신 스냅샷을 만드는 코드가 백엔드에 추가되어야 합니다.

연결 순서:

1. 백엔드가 VLA 제안을 ActionProposal로 등록.
2. 백엔드가 신뢰할 수 있는 현장 스냅샷과 제안을 POST /evaluate로 전송.
3. requires_estop이면 독립 정지 경로로 신호 발행, 작업 HELD 처리 및 ACK 확인.
4. can_execute=true일 때만 APPROVED. 그 외 REJECTED 또는 팀 정책에 따른 HELD.
5. **실행 직전에 최신 스냅샷으로 다시 검사.** 과거 판정을 재사용하지 않음.
6. 실행 중에도 별도의 감시 루프에서 재검사하고 위험 시 중단. 이 API는 감시 루프·ROS 2·MuJoCo를 자체 실행하지 않음.

타임아웃, HTTP 422/503, 응답 파싱 실패, ID/입력 해시 불일치이면 실행 차단. 인증·TLS가 없는 로컬 개발 서버이므로 기본 바인딩은 localhost입니다. 다른 장비 연결은 팀의 인증된 내부 경로로 구성하고, VLA가 위험/센서/정책 정보를 직접 수정하게 하지 마세요.

## 7. 테스트

```bash
python -m pytest -q
```

정상 승인, 관련/무관 구역 위험, ACKNOWLEDGED 차단, 관리자 해제, 정보 누락, 센서 장애, 과속, 선분 중간 금지구역 교차, 안전거리, 최신성, 중복 ID, UTC, 실행 직전 위험 추가, 요청 간 상태 분리, API 검증 등을 확인합니다. 설치 버전은 requirements.lock.txt에 기록했습니다.

## 8. 다음 팀 작업

- 이 API 요청 포장과 확장 필드를 검토하고 버전 합의.
- Florence 출력에서 Observation/HazardEvent를 만드는 프로그램 연결. Florence가 위험 JSON을 직접 출력한다고 가정하지 않기.
- 신뢰도·관측 품질 기준, 다중 센서 가림과 유실 처리 구체화.
- 로봇팔을 사용할 경우 3D 궤적/형상 검사기 추가.
- ROS 2 전용 정지 경로, 하트비트 watchdog, 정지 ACK는 어댑터 담당과 연결.
- 독립 통합 시험과 실제 지연 측정. KPI 달성은 이 단위 테스트로 주장하지 않음.

## 참고한 공식 문서

- RDFLib Graph.query / initBindings: https://rdflib.readthedocs.io/en/stable/apidocs/rdflib.graph/
- FastAPI request body: https://fastapi.tiangolo.com/tutorial/body/
- 설계 기준: 제공받은 Ontology Schema v1.1, models.py, safety.py.
