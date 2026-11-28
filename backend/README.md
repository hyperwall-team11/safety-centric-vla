# Safety-Centric VLA Backend MVP

온톨로지 스키마 v1.1을 기반으로 다음 최소 흐름을 구현한 FastAPI 서버입니다.

1. 자연어 작업 명령을 검증해 `TaskOrder`로 변환
2. `TaskOrder`를 공통 `ActionProposal`로 생성하거나 VLA 제안을 등록
3. 최소 안전규칙(로봇 상태, 제어 어댑터, 기본·로봇·Zone 속도 상한, KEEP_OUT, 연결된 CRITICAL 위험) 평가
4. 승인된 제안만 mock 또는 ROS 2 제어 어댑터에 전달
5. 승인, 거절, 실행, 완료·실패 판단을 `ExecutionLog`에 기록

기본값은 로컬 개발용 인메모리 저장소입니다. `BACKEND_STORE=redis`를 지정하면
Redis에 버전화된 상태 스냅샷을 저장해 프로세스 재시작 후에도 복구합니다.
Redis 연결·잠금·저장이 실패하면 fail-closed로 `503`을 반환합니다. 제어 명령을 이미
발행한 뒤 실행 상태 커밋이 실패한 경우에만 상태 불일치를 막기 위해 E-Stop도 발행합니다.
운영 화면은 `WS /ws/state`에서 연결 직후와 상태 변경 시 전체 상태 스냅샷을 구독할 수 있습니다. 연속 변경은 최소 200ms 간격으로 합쳐 전송하고 ExecutionLog는 최근 100건만 포함하며, 전체 로그는 `GET /execution-logs`로 조회합니다.

금지구역 이동 경로 검사는 기존 GeoJSON `Polygon/coordinates`와
[Backend–CTL 계약](../docs/backend-ctl-contract-v0.1.md)의 `polygon/points`를 지원합니다.
`points`의 마지막 점과 첫 점을 연결해 경계를 검사합니다.

## 실행

```bash
cd backend
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload
```

Swagger UI: <http://127.0.0.1:8000/docs>

운영 콘솔: <http://127.0.0.1:8000/console>

### 선택형 API 토큰 인증

기본 개발 모드는 기존과 같이 인증이 비활성화되어 있습니다. 역할별 사전 발급 토큰을 사용할 때는 32바이트 이상의 난수 토큰을 만들고, 평문 토큰의 SHA-256과 요청자 식별자·역할을 `BACKEND_API_TOKEN_CONFIG` JSON 배열로 설정합니다. 설정이 있으면 `/health`, API 문서, 운영 콘솔 정적 파일을 제외한 HTTP API와 `WS /ws/state`가 인증을 요구합니다.

평문 토큰을 셸 기록에 남기지 않고 digest를 만들려면 다음 명령을 사용합니다.

```bash
python3 -c 'import getpass,hashlib; print(hashlib.sha256(getpass.getpass("Token: ").encode()).hexdigest())'
```

```bash
export BACKEND_API_TOKEN_CONFIG='[{"token_sha256":"<64자리 SHA-256>","subject":"operator-demo","role":"OPERATOR"}]'
python3 -m uvicorn app.main:app
```

HTTP 요청은 `Authorization: Bearer <평문 토큰>` 헤더를 사용합니다. `GET /auth/me`로 현재 인증의 활성화 여부와 인증된 `subject`, `role`을 확인할 수 있습니다. 서버는 설정과 메모리에 평문 토큰을 저장하지 않으며, 설정 JSON이 잘못되면 시작을 중단합니다.

브라우저 WebSocket은 임의의 `Authorization` 헤더를 지정할 수 없으므로 `state.v1`과 `bearer.<base64url(평문 토큰)>` 두 subprotocol을 보냅니다. 서버는 토큰을 검증한 뒤 `state.v1`만 선택합니다. 토큰이 URL·접속 로그에 남을 수 있는 query parameter 인증은 지원하지 않습니다.

인증이 활성화되면 React 운영 콘솔 상단의 **관리자 인증 필요**에서 토큰을 입력할 수 있습니다. 토큰은 브라우저 sessionStorage에만 저장되며 HTTP `Authorization`과 WebSocket subprotocol에 전달됩니다. CV 안전 확인은 `SENSOR` 또는 `CV`, 관리자 위험 해제와 RESUME 제안은 `OPERATOR` 또는 `ADMIN` 역할만 허용합니다.

CRITICAL 작업자 침입 복귀는 `CV 2초 안전 확인 → ACKNOWLEDGED → 관리자 해제 → RESUME 제안 평가·실행 → CTL 결과 → Task 재계획` 순서입니다. 위험 해제만으로 로봇이 움직이지 않으며, RESUME도 기존 규칙 게이트를 통과해야 ROS 2로 전달됩니다.

운영 콘솔에서는 별도 프론트엔드 빌드 없이 다음 흐름을 확인할 수 있습니다.

1. 자연어 명령 미리보기와 `TaskOrder` 생성
2. 로봇 빠른 등록과 현재 상태 확인
3. `ActionProposal` 생성, 안전 평가, 승인 제안 실행
4. 작업 상태 필터와 최근 `ExecutionLog` 확인

현재 코드 기준 요청·응답, 상태 전이, 오류와 기준 문서 대비 차이는 [Backend API 명세 v0.2](../docs/backend-api-spec-v0.2.md)를 참고합니다.

### Redis 영속 저장소

공유 시연 환경이나 재시작 복구가 필요하면 루트 디렉터리에서 Redis를 실행합니다.

```bash
docker compose up -d redis
cd backend
python3 -m pip install -r requirements.txt
BACKEND_STORE=redis \
BACKEND_REDIS_URL=redis://127.0.0.1:6379/0 \
python3 -m uvicorn app.main:app --reload
```

`compose.yml`은 AOF를 `everysec`로 활성화하고 `redis-data` 볼륨에 저장합니다.
`docker compose down`만 실행하면 볼륨은 남으며, `docker compose down -v`는 저장 데이터까지
삭제하므로 필요할 때만 사용합니다.

환경변수 예시는 루트의 `.env.example`에 있습니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `BACKEND_STORE` | `memory` | `redis`로 설정할 때만 Redis 사용 |
| `BACKEND_REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 접속 URL |
| `BACKEND_REDIS_KEY` | `safety-centric-vla:state:v1` | 공유 스냅샷 키. 환경별 분리 가능 |
| `BACKEND_CLOSED_LOOP_ENABLED` | `0` | `1`이면 Task 자동 실행 Worker 활성화 |
| `BACKEND_CLOSED_LOOP_POLL_INTERVAL_SECONDS` | `0.5` | 자동 실행 상태 확인 주기(초) |
| `BACKEND_CLOSED_LOOP_MAX_ATTEMPTS_PER_STEP` | `3` | 단계별 최대 시도 횟수(최초 시도 포함) |
| `BACKEND_CLOSED_LOOP_RESULT_TIMEOUT_SECONDS` | `120` | 실행 결과 미수신을 TIMEOUT으로 처리하는 시간(초) |

백업 파일을 즉시 생성하려면 다음을 실행합니다.

```bash
docker compose exec redis redis-cli BGSAVE
```

백엔드는 Redis 분산 잠금을 획득한 후 최신 스냅샷을 읽습니다. 저장할 때는 Lua 스크립트가
잠금 토큰 비교와 전체 스냅샷 `SET`을 하나의 원자적 연산으로 수행합니다. 이 방식은 현재
인메모리 API의 객체 수정 계약을 유지하면서 재시작 복구와 다중 백엔드 프로세스 직렬화를
제공하고, 만료되거나 다른 프로세스가 다시 획득한 잠금으로 최신 스냅샷을 덮어쓰는 경쟁을
차단합니다. 잠금 토큰이 일치하지 않으면 저장하지 않고 메모리 상태를 이전 스냅샷으로
복구한 뒤 `503`을 반환합니다.

일반적인 Redis 읽기 실패나 잠금 경합은 로봇 명령을 발행하지 않은 요청이므로 `503`만
반환합니다. 실행 API가 제어 명령을 발행한 뒤 Redis 커밋에 실패한 경우에는 로봇의 물리
상태와 Backend 상태가 어긋날 수 있으므로 추가로 E-Stop을 발행합니다.

E-Stop 발행은 별도 Redis 분산 잠금과 최신 상태 스냅샷 읽기로 직렬화합니다. Backend가
재시작되면 Redis의 정지 상태를 ROS 2 latched 토픽에 다시 발행합니다. Redis Pub/Sub 변경
알림을 구독하므로 다른 Backend 인스턴스의 상태 변경도 각 인스턴스의 `/ws/state`
구독자에게 전달됩니다. Pub/Sub 알림은 WebSocket 갱신용이며 상태의 기준은 항상 Redis
스냅샷입니다.

CI는 실제 Redis 7.4 서비스를 띄워 재시작 복구, 2개 저장소의 동시 쓰기, Pub/Sub 알림,
E-Stop 상태 복구, Action 제출·검증을 검증합니다. 로컬에서 같은 통합 테스트를 실행하려면
Redis를 실행한 뒤 다음처럼 지정합니다.

```bash
cd backend
REDIS_TEST_URL=redis://127.0.0.1:6379/15 python3 -m pytest tests/test_redis_store.py -q
```

### ROS 2 제어 어댑터 연결

기본 실행은 ROS 2가 없는 개발 환경에서도 쓸 수 있도록 mock 전송기를 사용합니다. 실제 CTL 어댑터와 연결할 때는 `control`의 ROS 2 환경을 활성화하고 아래처럼 실행합니다.

```bash
cd control
pixi run build
pixi shell
source ros2_ws/install/setup.bash
python -m pip install -r ../backend/requirements.txt
cd ../backend
BACKEND_CTL_ROS_ENABLED=1 python -m uvicorn app.main:app
```

다른 주소의 Backend 결과 API를 사용할 때는 `BACKEND_CTL_RESULT_API`를 지정합니다. 기본값은 `http://127.0.0.1:8000`입니다.

ROS 2 모드에서는 실행 직전 재검증을 통과한 승인 스냅샷만 `/safety/approved_action`에 발행합니다. Backend의 `{x,y,z}` 좌표는 CTL의 `[x,y,z]` 좌표로 바꾸고, 발행 성공 후에만 Proposal을 `EXECUTING`으로 전이합니다. `/adapter/{robot_id}/result`의 `RESULT`는 표준 실패 코드로 정규화해 결과 API에 반영하며 `/safety/heartbeat`는 5 Hz로 발행합니다.

실행 중인 로봇과 연결된 `CRITICAL` HazardEvent가 등록되면 `/safety/estop`(latched)에 정지 신호를 발행하고, 그 위험이 모두 해제되면 정지 신호를 풉니다. 로봇 복귀에는 승인된 `RESUME`이 필요합니다. 창고 시연에서 운영자 위험 해제 뒤 AMR에 `RESUME`을 발행하는 단계는 아직 연결되지 않았습니다.

운영 콘솔의 수동 정지는 `POST /safety/estop`으로 전역 E-Stop을 발행합니다. 운영자
정지 상태는 Redis 스냅샷에도 보존되며, 실행 중 Proposal과 Task를 `HELD`, Robot을
`ESTOP`으로 전이하고 `triggered_by=OPERATOR` 감사 로그를 남깁니다. 인증이 활성화된
환경에서는 `OPERATOR` 또는 `ADMIN` 역할만 정지·해제를 호출할 수 있습니다. `POST
/safety/estop/release`는 latched 정지 신호만 해제하며 로봇을 자동 재개하지 않습니다.
열린 위험 기반 정지가 남아 있으면 신호는 계속 활성 상태입니다.

발표용 창고의 CCTV 04 낙상자 점검은 후방 통로에서 확인된 작업자 위험을 `UNKNOWN / WARNING`으로 저장합니다. [시연 절차](../docs/warehouse-presentation.md)에서 영상 관측과 위험 기록을 확인할 수 있습니다.

`ZONE` 목적지를 실제 실행하려면 해당 Zone의 `geometry.navigation_target`에 `{x,y,z}` 좌표가 있어야 합니다. 없으면 명령을 추측해 보내지 않고 `422`로 차단합니다.

### MuJoCo ② 사전 시뮬레이션 연결

스키마 v1.1 승인 규칙 9번(`COLLISION`)을 적용하려면 CTL 사전 시뮬레이션 서비스를 띄우고 Backend를 아래처럼 실행합니다. 기본값은 비활성화입니다.

```bash
cd control && pixi run twin-sim          # http://127.0.0.1:8100/twin/simulate
cd backend
BACKEND_TWIN_SIMULATION_ENABLED=1 python3 -m uvicorn app.main:app
```

다른 주소면 `BACKEND_TWIN_SIMULATE_URL`을 지정합니다. 활성화하면 서비스가 꺼져 있거나 Worker 스냅샷이 없을 때 이동 제안이 모두 거부됩니다(Fail-Closed). 동작 규칙은 [API 명세 3.9절](../docs/backend-api-spec-v0.2.md)을 참고합니다.

## 테스트

```bash
cd backend
python3 -m unittest discover -s tests -v
```

자연어 파서의 정상·거부 명령 100건 정확도와 처리시간은 별도 평가 명령으로 확인합니다.

```bash
python3 evaluation/run_command_evaluation.py
```

평가 데이터와 결과 해석은 [`evaluation/README.md`](evaluation/README.md)를 참고합니다.

## KPI 9: 명령→어댑터 ACK 지연 측정

ROS 2 dispatcher와 CTL 어댑터를 함께 실행한 상태에서 자연어 명령의 Backend 수신부터
어댑터 ACK의 Backend 수신까지 10회 지연을 측정합니다. 각 회차의 실행 성공률과
1초 이내 지연 통과율을 별도로 집계하며,
한 회차가 실패해도 나머지 측정을 계속합니다. ACK 측정 후에는 실제 CTL `RESULT`가
Backend에 반영될 때까지 기다리며, ACK가 누락되거나 늦어도 현재 실행이
끝나기 전에 다음 회차를 시작하지 않습니다. 벤치마크가 성공·실패 결과를 대신 등록하지
않고, 이동 거리 0인 반복 명령을 피하도록 `(1, 0, 0)`과 `(0, 0, 0)`을 번갈아 지정합니다.

```bash
cd backend
python3 scripts/benchmark_command_to_dispatch.py --runs 10 --threshold-ms 1000
```

CTL 어댑터의 Robot ID가 기본값 `robot-01`과 다르면 `--robot-id`로 같은 값을
지정합니다. 좌표계는 기본 `world`이며 필요하면 `--frame-id`로 바꿉니다. 실제 동작
완료 대기시간은 `--result-timeout`(기본 120초)으로 지정합니다.

Backend는 `GET /action-proposals/{proposal_id}/ack`에서 `PENDING`, `ACKNOWLEDGED`,
`TIMED_OUT`을 반환합니다. 명령 수신·발행·CTL ACK·Backend ACK 수신 시각과
`command_to_ack_ms`, `dispatch_to_ack_ms`를 함께 보존하므로 전체 KPI와 순수 발행→ACK
지연을 구분할 수 있습니다. 어댑터와
Backend의 시계 오차가 KPI에 영향을 주지 않도록 `command_to_ack_ms`는 Backend 수신 시각끼리
계산합니다. `PENDING`의 1초 기한 경과는 ACK 조회 또는 늦은 ACK 처리 시점에
`TIMED_OUT`으로 확정하며 별도 백그라운드 타이머는 없습니다. ACK 상태는 전송 진단값일
뿐 Proposal·TaskOrder·Robot의 실행 상태를 변경하지 않습니다. mock dispatcher는 ACK를
보내지 않으므로 KPI 측정에 사용할 수 없습니다.

> **주의:** ROS 2 dispatcher를 활성화한 환경에서는 벤치마크 MOVE가 실제 ROS Action을 발행하므로
> mock 또는 격리된 시험 환경에서만 실행합니다. `--robot-id`에 이미 등록된 Robot을 지정하면
> 재등록하지 않습니다. 새 ID를 지정한 경우 현재 Robot 삭제 API가 없어 저장소에 남습니다.

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

TaskOrder 생성 요청에 선택적인 `Idempotency-Key` 헤더를 보내면 네트워크 재시도나 중복 클릭에도 같은 작업을 한 번만 생성합니다. 같은 키와 본문을 다시 보내면 기존 TaskOrder를 `200`으로 반환하고, 같은 키에 다른 본문을 보내면 `409`입니다. 키는 공백 제외 1~128자입니다. 응답의 `Idempotency-Replayed` 헤더로 최초 생성 여부를, `Location` 헤더로 생성된 TaskOrder 조회 경로를 확인할 수 있습니다. 동시 요청도 같은 키라면 하나의 작업으로 직렬화됩니다. 요청 비교에는 기본값까지 정규화한 SHA-256 fingerprint를 사용하므로 자연어 원문을 멱등성 기록에 중복 저장하지 않습니다.

`POST /task-orders/preview`는 TaskOrder를 저장하지 않고 자연어 명령의 정규화 결과, 대상 로봇·물체, 목적지와 예상 Action 유형을 반환합니다. 해석 실패는 `ROBOT_NOT_FOUND`, `DESTINATION_REQUIRED`처럼 구조화된 오류 코드와 한글 메시지로 제공합니다. `POST /task-orders/preview/batch`는 1~50개 요청을 입력 순서대로 검증하며 일부 항목이 실패해도 나머지 결과와 성공·실패 집계를 반환합니다. 두 미리보기 API는 성공·실패 모두 저장소를 변경하지 않습니다.

공백이 포함된 구역명은 `"조립 라인 구역"으로`처럼 따옴표로 묶습니다. 물체 ID는 `상자3`, `상자 번호 3`, `3번 상자`, `박스3`, `box-3`, `obj-3` 형식을 지원합니다.

현재 자동 변환 범위는 로봇 이동과 물체 운반입니다. `STOP`, `RESUME`은 Action enum에는 존재하지만 목적지가 필수인 TaskOrder v1.1 계약상 아직 자동 변환하지 않고 `422`를 반환합니다.

외부 VLA가 `POST /action-proposals`로 제안을 등록하면 Pydantic 필드 검증에 더해
TaskOrder의 행동, 대상 로봇·물체, 목적지와 좌표계가 바뀌지 않았는지 검증합니다.
불일치 출력은 저장하거나 안전 규칙에 넘기지 않고 `422`로 거부합니다. 속도와 경로가
현장 안전 기준을 만족하는지는 이 문맥 검증과 분리해 기존 제출 기준 평가에서 판정합니다.
직접 제출하는 `MOVE_TO trajectory`가 있으면 시작점은 Robot 현재 위치와, 좌표 목적지
또는 같은 좌표계의 Zone `navigation_target`이 있으면 끝점도 목적지와 0.05m 이내로
일치해야 합니다. 물리 좌표가 없는 Zone은 의미상 `target_pose` 일치만 검증합니다.

VLA 추론 런타임은 `app.vla_output.convert_vla_output_to_proposal()`에 JSON 문자열
또는 파싱된 객체를 넘겨 원시 출력을 `ActionProposal(PROPOSED)`로 변환할 수 있습니다.
VLA는 `action_type`, `parameters`, `trajectory`, `frame_id`, `speed_limit`만 제공하며,
`proposal_id`, `task_id`, `robot_id`, `status`, 모델 이름·버전은 신뢰된 Backend 문맥에서
생성합니다. 모델이 서버 소유 필드를 추가하거나 TaskOrder의 행동·대상·목적지를
바꾸면 변환을 거부합니다.
실제 모델 연동 클라이언트는 `VlaOutputProvider`를 구현하고 `VlaActionPlanner`에
주입하면 기존 ActionProposal 생성 경로를 바꾸지 않고 규칙 기반 플래너를 교체할 수 있습니다.
모델 추론은 저장소 잠금 밖에서 실행하며, 완료 후 TaskOrder·Robot·Zone과 활성
Proposal을 다시 확인합니다. 추론 중 문맥이 바뀌면 오래된 결과를 저장하지 않고
`409`를, 모델 서버 연결 실패·시간 초과 등 Provider 예외는 `503`을 반환합니다.
일반적인 OpenVLA의 7차원 행동 벡터
`[dx, dy, dz, droll, dpitch, dyaw, gripper]`는
`app.openvla_adapter.convert_openvla_action_7d()`가 `MANIPULATE.parameters.ee_delta`로
변환합니다. 평행이동은 m, RPY 회전은 rad, gripper는 `[-1, 1]`이며,
제어 한 주기의 축별 한계는 `0.05m`, `0.25rad`입니다. 한계를 넘는 제안은
Safety Gate가 `precondition-ee-delta-bounds`로 거절합니다.
현재 TaskOrder v1.1에서 VLA 자동·직접 제안으로 허용하는 행동은 목적지가 구조화된
`MOVE_TO`와 대상 물체·목적지가 구조화된 `MANIPULATE`입니다. `STOP`·`RESUME`은
일반 VLA 출력으로 받지 않고 별도 안전 제어 경로에서만 생성합니다.

## ActionProposal 생성

TaskOrder를 생성한 뒤 아래 API를 호출합니다.

```bash
curl -X POST http://127.0.0.1:8000/task-orders/{task_id}/action-proposals \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-request-1' \
  -d '{"speed_limit": 0.4}'
```

요청 본문은 JSON 객체이며 `speed_limit`은 선택값입니다. 생략하면 명세 기본값인 `0.4 m/s`와 로봇 최대 속도 중 작은 값을 사용합니다. 좌표 목적지는 TaskOrder의 좌표 frame을, 구역 목적지는 로봇 pose frame을 사용합니다. 좌표 변환 없이 frame 이름만 덮어쓰는 실수를 막기 위해 생성 요청에서는 frame을 변경할 수 없습니다.

`MOVE_TO` 제안은 `parameters.target_pose`와 `parameters.speed_limit`를 생성합니다. 대상 물체가 없는 이동 작업은 기존처럼 목적지 `MOVE_TO` 하나를 생성합니다. 대상 물체가 있는 작업은 먼저 `POST /physical-objects`로 물체의 현재 pose를 등록한 뒤 빈 요청 `{}`을 보내면, 성공한 Proposal 이력을 기준으로 `물체 접근 MOVE_TO → PICK → 목적지 MOVE_TO → PLACE` 중 다음 단계 하나를 자동 생성합니다. `"manipulation": "PICK"` 또는 `"PLACE"`를 명시하면 기존 수동 단계 생성도 사용할 수 있습니다. `MANIPULATE` 제안은 스키마 v1.1에 맞춰 `manipulation`, `object_id`, `target_pose`를 생성합니다. 외부 VLA가 직접 제안을 등록할 때 선택적인 `parameters.priority`는 `NORMAL` 또는 `SAFETY`를 받으며, `SAFETY`는 시스템이 발행하는 `STOP`·`RESUME`에만 허용합니다.

성공하면 TaskOrder가 `CREATED`에서 `PLANNING`으로 바뀌며 Proposal은 `PROPOSED`로 저장됩니다. 같은 TaskOrder에 활성 Proposal을 중복 생성하면 `409`를 반환합니다. 선택적인 `Idempotency-Key`를 보낸 경우, 동일한 키와 동일한 본문을 재전송하면 기존 Proposal을 `200`으로 반환하고 새 Proposal을 만들지 않습니다. 같은 키에 다른 본문을 보내면 `409`입니다.

GPU에서 실행한 OpenVLA 출력은 다음 전용 경계로 보낼 수 있습니다.

```bash
python vla/openvla_single_image_infer.py \
  --image /shared/cctv.png \
  --instruction 'grasp the red box' \
  --backend-url http://127.0.0.1:8000 \
  --task-id {task_id} \
  --manipulation PICK
```

스크립트는 `POST /task-orders/{task_id}/openvla-actions`로 7D 한 주기를
제출하며, Backend는 대상 로봇·물체·목적지를 신뢰된 TaskOrder 문맥에서
주입합니다. 반환된 Proposal은 기존 `/evaluate` → `/execute` 경로를 그대로
통과해야 ROS 2로 전달됩니다. `phase_complete` 기본값은 `false`로, 한 번의
EE_DELTA 실행 성공을 PICK/PLACE 전체 완료로 간주하지 않습니다. 새 관측으로
단계 완료가 확인된 마지막 주기에만 `--phase-complete`를 사용합니다.
새 CCTV 프레임마다 이 과정을 반복하려면
[`vla/openvla_closed_loop.py`](../vla/openvla_closed_loop.py)를 사용합니다. Safety 거절,
HELD/E-Stop, 실행 실패 후에는 새 Proposal을 만들지 않는 Fail-Closed 루프입니다.

대상 물체가 있는 운반 TaskOrder는 여러 ActionProposal을 순차 실행합니다. 수동 연동에서는 0.5초 주기 실행기가 `POST /task-orders/{task_id}/action-proposals`에 `{}`을 반복 전송할 수 있습니다. Backend가 생성·평가·실행까지 자동 진행하게 하려면 다음처럼 Closed-Loop Worker를 활성화합니다.

```bash
BACKEND_CLOSED_LOOP_ENABLED=1 python3 -m uvicorn app.main:app
```

Worker는 기본 0.5초마다 `CREATED`, `PLANNING`, `RUNNING` 작업을 확인하고 `ActionProposal 생성 → 안전 평가 → 승인 Proposal 실행 → RESULT 대기`를 반복합니다. 활성 Proposal은 기존처럼 하나만 허용되며 모든 자동 단계도 같은 안전 평가와 실행 직전 재검증을 통과해야 합니다. OpenVLA 또는 Navigation Planner가 만든 구조화 Proposal을 `POST /action-proposals`로 직접 등록하는 경로도 유지됩니다.

자동 다단계 회귀 테스트 한 건만 실행하려면 다음 명령을 사용합니다.

```bash
python -m pytest -q tests/test_execution_result_api.py \
  -k object_transport_runs_multiple_proposals_before_task_completion
```

이 테스트는 같은 생성 API의 빈 요청으로 `MOVE_TO → PICK → MOVE_TO → PLACE`가 순서대로 생성되고, 앞의 세 단계에서는 TaskOrder가 `PLANNING`, 마지막 `PLACE` 성공 뒤에는 `COMPLETED`가 되는지 확인합니다.

`HELD` 작업은 일반 생성 API로 재개하지 않습니다. 관련 위험 해제까지 확인하는 별도 Resume 흐름이 팀 계약으로 추가되기 전에는 `409`를 반환합니다.

평가 속도 상한은 `min(0.4, robot.motion_limits.max_speed, zone.max_speed)`이며 등록된 목적지 Zone에 `max_speed`가 없으면 Zone 항목은 제외합니다. 실행 직전 재검증이 실패하면 Proposal·TaskOrder를 `HELD`, Robot을 `ESTOP`으로 바꾸고 `RULE_ENGINE` 감사 로그를 남깁니다. 통과하면 승인 명령 발행을 시도하고, 발행까지 성공한 뒤 Proposal은 `EXECUTING`, TaskOrder는 `RUNNING`, Robot은 `MOVING`으로 전이합니다. ROS 2 발행에 실패하면 기존 상태를 유지하고 `503`을 반환합니다.

열린 CRITICAL 위험은 `related_robot_ids`에 대상 Robot이 있거나 Hazard의 Zone과 TaskOrder 목적지 Zone이 같을 때 차단합니다. 빈 `related_robot_ids` 자체는 전역 위험을 뜻하지 않습니다. CRITICAL 위험 해제는 `{"resolved_by":"OPERATOR"}` 요청만 허용하고, WARNING은 `SENSOR_AUTO`도 허용합니다.

실행 중인 Proposal은 `POST /action-proposals/{proposal_id}/result`로 어댑터 결과를 보고합니다. 일반 이동 Task의 `SUCCEEDED`는 TaskOrder를 `COMPLETED`, Robot을 `IDLE`로 돌립니다. 물체 운반 Task에서는 `PICK`과 Navigation `MOVE_TO` 성공 후 TaskOrder를 `PLANNING`으로 되돌려 다음 Proposal을 받고, 최종 `PLACE` 성공 때만 `COMPLETED`로 전이합니다. 같은 결과 재전송은 멱등 처리되어 감사 로그가 중복되지 않습니다.

Closed-Loop Worker의 기본 실패 정책은 다음과 같습니다.

- 안전 평가 `REJECTED`: TaskOrder `HELD`, Robot `HOLD`로 자동 실행 중단. 실행 직전 Robot 위치가 예상 경로 시작점과 달라진 경우만 새 경로로 재계획
- `UNSUPPORTED`: 같은 단계를 다시 계획
- `TIMEOUT`: CTL이 보고하거나 120초 동안 RESULT가 없으면 실제 동작 결과를 확정할 수 없으므로 TaskOrder `HELD`, Robot `HOLD`로 즉시 중단
- `UNSUPPORTED`와 경로 재계획은 단계당 최초 시도를 포함해 최대 3회. 모두 실패하면 TaskOrder `FAILED`, Robot `HOLD`
- `HW_ERROR`: 재시도 없이 TaskOrder `FAILED`, Robot `ERROR`
- 물체 미등록처럼 안전한 Proposal을 만들 수 없는 `404`·`422`: TaskOrder `HELD`, Robot `HOLD`

Worker가 비활성화된 수동 API 모드의 기존 전이는 유지됩니다. `UNSUPPORTED`는 TaskOrder `PLANNING`·Robot `HOLD`, `TIMEOUT`은 TaskOrder `FAILED`·Robot `HOLD`, `HW_ERROR`는 TaskOrder `FAILED`·Robot `ERROR`입니다.

## 구현된 최소 API

- `POST /robots`
- `GET /robots`
- `POST /physical-objects`
- `GET /physical-objects`
- `POST /zones`
- `GET /zones`
- `GET /task-orders`
- `POST /task-orders/preview`
- `POST /task-orders/preview/batch`
- `POST /task-orders/from-natural-language`
- `GET /task-orders/{task_id}`
- `POST /task-orders/{task_id}/action-proposals`
- `POST /task-orders/{task_id}/openvla-actions`
- `GET /task-orders/{task_id}/action-proposals`
- `POST /action-proposals`
- `POST /action-proposals/{proposal_id}/evaluate`
- `POST /action-proposals/{proposal_id}/execute`
- `GET /action-proposals/{proposal_id}/ack`
- `POST /action-proposals/{proposal_id}/result`
- `POST /hazards`
- `POST /hazards/{hazard_id}/resolve`
- `GET /safety-rules`
- `GET /execution-logs`
