# Safety-Centric VLA Backend MVP

온톨로지 스키마 v1.1을 기반으로 다음 최소 흐름을 구현한 FastAPI 서버입니다.

1. 자연어 작업 명령을 검증해 `TaskOrder`로 변환
2. `TaskOrder`를 공통 `ActionProposal`로 생성하거나 VLA 제안을 등록
3. 최소 안전규칙(로봇 상태, 제어 어댑터, 속도 제한, 열린 CRITICAL 위험) 평가
4. 승인된 제안만 mock 제어 어댑터에 전달
5. 승인, 거절, 실행 판단을 `ExecutionLog`에 기록

현재 저장소는 시연용 인메모리 구현입니다. 프로세스를 재시작하면 데이터가 초기화되며, 이후 Redis 저장소로 교체할 수 있도록 파서·안전규칙·API를 분리했습니다.

## 실행

```bash
cd backend
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload
```

Swagger UI: <http://127.0.0.1:8000/docs>

현재 코드 기준 요청·응답, 상태 전이, 오류와 기준 문서 대비 차이는 [Backend API 명세 v0.2](../docs/backend-api-spec-v0.2.md)를 참고합니다.

## 테스트

```bash
cd backend
python3 -m unittest discover -s tests -v
```

## 자연어 파서 예시

등록된 로봇이 `robot-01`일 때:

```json
{
  "instruction": "robot-01 로봇을 A구역으로 이동해"
}
```

```json
{
  "instruction": "로봇을 좌표 (1.5, -2, 0) 위치로 이동해",
  "target_robot_id": "robot-01",
  "frame_id": "map"
}
```

대상 로봇이나 목적지, 실행 동사를 확실히 식별하지 못하면 값을 추측하지 않고 `422`를 반환합니다. 부정문, 조건문, 질문, 과거 작업 서술, 복수 로봇·복수 목적지도 실행 명령으로 받지 않습니다.

공백이 포함된 구역명은 `"조립 라인 구역"으로`처럼 따옴표로 묶습니다. 물체 ID는 `상자3`, `상자 번호 3`, `3번 상자`, `박스3`, `box-3`, `obj-3` 형식을 지원합니다.

현재 자동 변환 범위는 로봇 이동과 물체 운반입니다. `STOP`, `RESUME`은 Action enum에는 존재하지만 목적지가 필수인 TaskOrder v1.1 계약상 아직 자동 변환하지 않고 `422`를 반환합니다.

## ActionProposal 생성

TaskOrder를 생성한 뒤 아래 API를 호출합니다.

```bash
curl -X POST http://127.0.0.1:8000/task-orders/{task_id}/action-proposals \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-request-1' \
  -d '{"speed_limit": 0.4}'
```

요청 본문은 JSON 객체이며 `speed_limit`은 선택값입니다. 생략하면 명세 기본값인 `0.4 m/s`와 로봇 최대 속도 중 작은 값을 사용합니다. 좌표 목적지는 TaskOrder의 좌표 frame을, 구역 목적지는 로봇 pose frame을 사용합니다. 좌표 변환 없이 frame 이름만 덮어쓰는 실수를 막기 위해 생성 요청에서는 frame을 변경할 수 없습니다.

`MOVE_TO` 제안은 `parameters.target_pose`와 `parameters.speed_limit`를 생성합니다. 물체 TaskOrder는 해석만으로 집기와 놓기를 임의로 결정하지 않으며, 요청 본문에 `"manipulation": "PICK"` 또는 `"PLACE"`를 명시해야 합니다. `MANIPULATE` 제안은 스키마 v1.1에 맞춰 `manipulation`, `object_id`, `target_pose`를 생성합니다. `target_pose`는 TaskOrder의 `COORDINATE` 또는 `ZONE` 목적지 표현을 그대로 유지합니다. 외부 VLA가 직접 제안을 등록할 때 선택적인 `parameters.priority`는 `NORMAL` 또는 `SAFETY`를 받으며, `SAFETY`는 시스템이 발행하는 `STOP`·`RESUME`에만 허용합니다.

성공하면 TaskOrder가 `CREATED`에서 `PLANNING`으로 바뀌며 Proposal은 `PROPOSED`로 저장됩니다. 같은 TaskOrder에 활성 Proposal을 중복 생성하면 `409`를 반환합니다. 선택적인 `Idempotency-Key`를 보낸 경우, 동일한 키와 동일한 본문을 재전송하면 기존 Proposal을 `200`으로 반환하고 새 Proposal을 만들지 않습니다. 같은 키에 다른 본문을 보내면 `409`입니다.

`HELD` 작업은 일반 생성 API로 재개하지 않습니다. 관련 위험 해제까지 확인하는 별도 Resume 흐름이 팀 계약으로 추가되기 전에는 `409`를 반환합니다.

## 구현된 최소 API

- `POST /robots`
- `GET /robots`
- `GET /task-orders`
- `POST /task-orders/from-natural-language`
- `GET /task-orders/{task_id}`
- `POST /task-orders/{task_id}/action-proposals`
- `GET /task-orders/{task_id}/action-proposals`
- `POST /action-proposals`
- `POST /action-proposals/{proposal_id}/evaluate`
- `POST /action-proposals/{proposal_id}/execute`
- `POST /hazards`
- `POST /hazards/{hazard_id}/resolve`
- `GET /safety-rules`
- `GET /execution-logs`
