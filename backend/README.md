# Safety-Centric VLA Backend MVP

온톨로지 스키마 v1.1을 기반으로 다음 최소 흐름을 구현한 FastAPI 서버입니다.

1. 자연어 작업 명령을 검증해 `TaskOrder`로 변환
2. VLA의 `ActionProposal` 등록
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

## 구현된 최소 API

- `POST /robots`
- `GET /robots`
- `POST /task-orders/from-natural-language`
- `GET /task-orders/{task_id}`
- `POST /action-proposals`
- `POST /action-proposals/{proposal_id}/evaluate`
- `POST /action-proposals/{proposal_id}/execute`
- `POST /hazards`
- `POST /hazards/{hazard_id}/resolve`
- `GET /safety-rules`
- `GET /execution-logs`
