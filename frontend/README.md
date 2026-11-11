# VLA 안전 운영 콘솔

React 18 + Vite + TypeScript로 만든 안전 중심 운영 콘솔입니다. 얇은 상단 내비게이션, 넓은 작업면, 대형 상태 시각화, 상태별 색 체계와 반응형 레이아웃으로 로봇·작업·위험·판단 이력을 빠르게 구분합니다. Backend(FastAPI) API 계약과 기존 `backend/app/static/console`(`/console`)은 그대로 유지합니다.

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, /api → http://127.0.0.1:8000 프록시
npm run build        # 타입 검사 + 빌드
```

Backend 주소가 다르면 `VITE_BACKEND_URL=http://host:8000 npm run dev`. Backend에 CORS 설정이 없어 개발 서버 프록시(`/api`)로 호출합니다.

## 화면과 사용자 흐름

| 경로 | 화면 | 흐름 | 사용 API |
|---|---|---|---|
| `/` | 대시보드 | 조치 필요 대기열(CRITICAL·WARNING 위험 → 정지 로봇 → 실행·검증 대기·거절 제안, 항목에서 바로 해제·검증·실행) → 현장 지도(열린 위험 구역 표시) → 요약 카드·목록. 대기열·목록·지도 중 하나를 고르면 같은 로봇·구역을 함께 강조 | `GET /robots`, `/zones`, `/task-orders`, `/task-orders/{id}/action-proposals`, `/execution-logs`, `/hazards`* |
| `/proposals`, `/proposals/:taskId` | 행동 제안 | 제안별 규칙 결과(위반 먼저, 해결 방법·속도 낮춰 재제안) → 승인 시 [실행]. 고른 제안의 경로·위반 위치를 지도에 표시 | `POST /action-proposals/{id}/evaluate`, `/execute`, `GET /execution-logs` |
| `/hazards` | 위험 모니터링 | CRITICAL·WARNING 목록 → 운영자 해제 | `GET /hazards`*, `POST /hazards/{id}/resolve` |
| `/site` | 현장·작업 요청 | 현장 지도·CCTV 아래에서 바로 작업 요청: 지도에서 로봇·구역 클릭(명령 자동 작성) 또는 자연어 입력 → 해석 미리보기 → 작업·행동 제안 생성 → 같은 화면에서 안전 검증·실행. 계획 경로와 위반 구역·작업자를 지도에 표시. `/tasks/new`는 여기로 이동 | `POST /task-orders/preview`, `/task-orders/from-natural-language`, `/task-orders/{id}/action-proposals`, `GET /zones`, `/robots`, `/workers/snapshot`*, `/workers/snapshot`* |
| `/estop` | 비상정지·복귀 | 정지 로봇 → 위험 해제 → RESUME 제안 → 검증·실행 | `GET /robots`, `/task-orders?status=HELD`, `/execution-logs` |
| `/logs` | 판단 이력 | ExecutionLog 타임라인 | `GET /execution-logs` |

열린 CRITICAL 위험이 있으면 모든 화면 위에 경보 배너를 고정합니다.

## 아직 없는 Backend API (* 표시)

화면은 미리 만들어 두었고, API가 없으면 "API 대기"로 표시하며 반복 조회하지 않습니다.

- `GET /hazards`: 위험 목록·경보 배너·위험 모니터링
- `GET /workers/snapshot`: 현장 지도의 작업자 위치
- 운영자 E-Stop API: 비상정지 버튼(현재 비활성)
- CCTV 영상: ROS 2 토픽을 웹으로 보내는 브리지(#55)

## 실시간 갱신

`StateStreamProvider`가 WebSocket `/ws/state`(#76)를 구독합니다. 스냅샷의 `revision`이 바뀌면 각 화면이 쓰던 조회 API를 한 번 다시 부르고, 15초 heartbeat(같은 `revision`)에는 조회하지 않습니다. 연결이 끊기거나 20초간 수신이 없으면 1~15초 간격으로 재연결하며, 그동안 화면은 기존 주기 조회(`usePolling`)로 동작합니다. 상단 바에 `실시간`/`주기 조회`로 표시합니다.

## 구조

```text
src/
├── api/types.ts       # backend/app/models.py와 같은 이름·상태값
├── api/explain.ts      # 규칙 결과 → 운영자용 설명·해결 방법·위반 위치(safety.py 사유 문구 기준)
├── api/client.ts      # fetch 래퍼, 미구현 API는 MissingApiError
├── hooks/usePolling.ts      # 스트림 연결 시 revision 기반, 아니면 주기 조회
├── hooks/useStateStream.tsx # /ws/state 구독·재연결
├── components/        # Layout, 아이콘, CriticalBanner, ui(Panel·Table·Stat·Status·Async)
└── pages/             # 화면 6개
```
