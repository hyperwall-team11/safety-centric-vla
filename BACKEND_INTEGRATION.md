# 백엔드 API v0.2 연결 안내 — 수정판 v1.1

## 현재 완료 범위

안전검사 서비스와 v0.2 객체→검사 요청→v0.2 결과 연결 함수를 구현했습니다. 기존 백엔드의 최신 main.py/store.py는 제공되지 않아 해당 라우트는 수정하지 않았고, 실제 백엔드·MuJoCo 연동 시험도 수행하지 않았습니다. 제공된 models.py, safety.py와 backend-api-spec-v0.2.md를 기준으로 테스트했습니다.

기존 공개 API 경로·요청 필드·열거값을 임의로 바꾸지 않습니다. 안전검사 서비스는 별도 포트 8001의 POST /evaluate이고, 기존 백엔드 API는 포트 8000을 유지합니다. 아래는 통합 제안이며 기존 백엔드의 As-Built 명세가 이미 변경되었다는 뜻이 아닙니다.

## 백엔드 환경에 패키지 설치

백엔드의 가상환경을 활성화한 뒤 프로젝트 경로에서 다음을 실행합니다.

```bash
pip install -e /실제/경로/ontology_safety
```

검사 서비스도 실행 중이어야 합니다. 두 프로그램이 다른 장비에 있으면 url과 인증된 통신 경로를 별도 구성하세요.

## 기존 객체만으로 호출하기

backend_bridge.py의 build_backend_snapshot / assess_for_backend는 기존 Pydantic 객체 또는 JSON dict를 받습니다. 공개 API에 없는 GET /hazards, /sensors를 호출하지 않습니다. 백엔드 라우트가 자기 저장소에서 객체를 읽어 함수에 전달합니다.

```python
from ontology_safety.backend_bridge import assess_for_backend
from ontology_safety.backend_client import SafetyServiceUnavailable

assessment = assess_for_backend(
    proposal=proposal,
    robot=robot,
    task=task,
    hazards=list(store.hazards.values()),
    scene=trusted_scene_state,
    phase="evaluate",  # execute 재검사에서는 "execute"
)
```

- scene은 센서·구역·작업자·어댑터 상태, 실제 스냅샷 시각, 완전성 정보를 담습니다. demo의 샘플을 실제 값처럼 사용하지 않습니다.
- scene을 생략해도 기존 객체는 파싱됩니다. 하지만 센서·구역 등을 임의로 생성하지 않으므로 승인되지 않습니다.
- 기존 데이터에서 온 updated_at/proposed_at을 그대로 보존합니다. 오래된 데이터를 현재 시각으로 재포장하지 않습니다.
- hazards는 대상 로봇만 사전 필터링하지 말고 전체 스냅샷을 전달해야 전역 위험이 보존됩니다.
- 원본 HazardEvent에 없는 관리자 해제 근거는 scene.hazard_metadata에 별도로 담을 수 있습니다. 기존 POST /hazards에 추가 필드를 넣으면 extra=forbid로 거부될 수 있습니다.

## 라우트에서 처리할 상태

| 위치 | 서비스 판정 | 백엔드 Proposal 상태 | 기존 로그 decision | HTTP |
|---|---|---|---|---|
| evaluate | 승인 | APPROVED | APPROVE | 200 |
| evaluate | 반려/판정 불가 | REJECTED | REJECT | 200 |
| execute 재검사 | 반려/판정 불가 | HELD | ESTOP | 409 |
| execute 재검사 | 승인 | 어댑터 전달 전까지 APPROVED | 심사 APPROVE | 200 |
| 통신·검사 오류 | 실행 차단 | evaluate는 REJECTED / execute는 HELD | REJECT / ESTOP | 503 제안 |

execute 승인 후 **실제로 어댑터에 전달한 뒤** 기존 백엔드가 EXECUTING/EXECUTE를 기록해야 합니다. 연결 함수는 전달하지 않은 명령을 실행했다고 기록하지 않습니다. API v0.2의 실행 재검사 실패 ESTOP 로그는 실제 비상정지 발행/완료와 구분해야 합니다.

서비스 응답의 requires_estop=true는 독립 제어 경로로 정지가 필요하다는 뜻입니다. 백엔드가 정지 발행과 ACK 확인을 담당해야 하며, 이 서비스/연결 함수 자체는 로봇을 정지시키지 않습니다.

## 최소 라우트 변경 절차

1. 기존 PROPOSED/APPROVED 상태 검사와 404/409 처리 유지.
2. 안전 심사에 필요한 최신 스냅샷 확보. 스냅샷 복사와 상태 버전을 일관되게 읽도록 구현.
3. assess_for_backend 호출. 평가에는 phase=evaluate, 실행 재검사에는 phase=execute.
4. assessment.execution_log를 백엔드의 ExecutionLog 클래스로 재검증하여 저장. 원본 assessment.evaluation도 별도 감사 기록으로 저장.
5. assessment.proposal_status로 상태 반영. execute 성공의 경우 실제 어댑터 전달 전에는 APPROVED 유지.
6. requires_estop이면 별도의 정지 경로 처리. 실제 전달/ACK 기록을 추가.
7. 서비스 판정 도중 위험·로봇 상태가 바뀌었으면 승인 폐기 후 새 스냅샷으로 재검사. 대기 중인 원격 HTTP 호출 하나가 원자적 실행 허가를 보장하지 않음.
8. can_execute=true일 때만 어댑터 전달. 전달 결과 확인 후 EXECUTING 또는 실패 상태 처리.

SafetyServiceUnavailable를 반드시 잡아 실행을 차단하고 상태·실패 로그를 기록해야 합니다. 실패 시 기존 약한 검사로 fallback하지 않습니다. 백엔드 라우트에 새 HTTP 503을 도입할 경우 같은 PR에서 API 문서와 테스트를 함께 바꾸세요.

## 유지한 정책과 강화한 정책

### 유지

- related_robot_ids=[]인 OPEN CRITICAL 위험은 모든 로봇을 차단합니다. 다른 구역에 있어도 마찬가지입니다. 이전 안전검사 MVP와 달라진 부분입니다.
- 기존 ActionProposal/HazardEvent의 필수 필드와 열거값을 유지합니다.
- evaluate의 승인/거절, execute 실패의 HELD/409 매핑을 유지합니다.
- map을 world라고 자동으로 간주하지 않고, 없는 trajectory를 임의 직선으로 생성하지 않습니다.

### 강화 — 통합 전 팀 검토 필요

- ACKNOWLEDGED CRITICAL도 해결 전이므로 차단합니다. v0.2는 OPEN만 차단했습니다.
- 관련 구역 위험도 관계 조회로 차단합니다. 특정 로봇 목록에 다른 로봇만 있어도 실제 행동 영향 구역이 같으면 차단될 수 있습니다.
- 필수 관측·구역·궤적·어댑터 정보가 부족하면 승인하지 않습니다.
- CRITICAL의 관리자 해제 근거가 없으면 승인하지 않습니다. v0.2 /resolve는 인증과 resolved_by 기록이 없어 이 조건을 충족하지 못할 수 있습니다. 해결 방법은 관리자 인증/감사 경로를 추가하는 것이며, 없는 근거를 OPERATOR로 채워 넣지 않습니다.
- 단순 로봇 IDLE/HOLD를 넘어 HOLD 재계획 허용을 명시적으로 확인합니다.

이 강화 조건 때문에 v0.2의 기존 샘플이 입력 형식은 맞더라도 승인되지 않을 수 있습니다. 입력 파싱 호환과 정책상 승인 가능은 서로 다릅니다.

## 제안 시각과 관측 시각

v1.0은 proposed_at이 2초를 넘으면 거부했습니다. v1.1은 제안의 나이만으로 만료하지 않습니다. 오래된 제안을 최신 현장 상태로 재평가할 수 있습니다.

현장 스냅샷·로봇 상태·센서 프레임·작업자 위치의 최신성 검사는 유지합니다. 과거 궤적의 시작점이 현재 위치와 다르면 여전히 승인하지 않습니다. 반려/완료된 제안의 임의 재실행도 상태 검사로 차단합니다.

## PR에 포함할 항목

- ontology_safety 소스와 tests, requirements, 문서.
- 백엔드의 evaluate/execute 라우트 호출 변경은 최신 실제 코드에 적용한 뒤 추가.
- 센서·구역 등은 파일/상태 공급자 또는 새 내부 API 중 팀이 정한 경로로 확보. 현재 없는 API를 존재한다고 문서화하지 않기.
- .venv, __pycache__, .pytest_cache, ZIP, 자동 생성 demo_results는 보통 제외. .gitignore 참고.
- 전체 백엔드 회귀 테스트와 OpenAPI 검증은 실제 저장소에서 별도 수행. 이 패키지의 단위 테스트 통과를 백엔드 통합 완료로 보고하지 않기.
