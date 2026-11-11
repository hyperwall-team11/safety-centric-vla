# 운영 콘솔 프론트엔드 (스캐폴드)

React 18 + Vite + TypeScript. Backend(FastAPI) API로 운영자 흐름을 화면 단위로 나눈 뼈대입니다. 스타일은 무채색 와이어프레임이고, 색·디자인은 상태값(`data-status`, `data-type`)에 붙여 나중에 입힙니다. 기존 `backend/app/static/console`(`/console`)은 그대로 둡니다.

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
| `/` | 대시보드 | 요약 카드, 현장 지도, 열린 위험, 로봇 상태, 진행·보류 작업, 최근 판단 | `GET /robots`, `/zones`, `/task-orders`, `/execution-logs`, `/hazards`* |
| `/tasks/new` | 작업 요청 | 자연어 입력 → 해석 미리보기 → 작업 생성 + 행동 제안 생성 | `POST /task-orders/preview`, `/task-orders/from-natural-language`, `/task-orders/{id}/action-proposals` |
| `/proposals`, `/proposals/:taskId` | 행동 제안 | 제안 → [검증] → 규칙별 결과 → 승인 시 [실행] | `POST /action-proposals/{id}/evaluate`, `/execute`, `GET /execution-logs` |
| `/hazards` | 위험 모니터링 | CRITICAL·WARNING 목록 → 운영자 해제 | `GET /hazards`*, `POST /hazards/{id}/resolve` |
| `/site` | CCTV·현장 지도 | 구역·로봇·작업자 평면도, CCTV 자리 | `GET /zones`, `/robots`, `/workers/snapshot`* |
| `/estop` | 비상정지·복귀 | 정지 로봇 → 위험 해제 → RESUME 제안 → 검증·실행 | `GET /robots`, `/task-orders?status=HELD`, `/execution-logs` |
| `/logs` | 판단 이력 | ExecutionLog 타임라인 | `GET /execution-logs` |

열린 CRITICAL 위험이 있으면 모든 화면 위에 경보 배너를 고정합니다.

## 아직 없는 Backend API (* 표시)

화면은 미리 만들어 두었고, API가 없으면 "API 대기"로 표시하며 반복 조회하지 않습니다.

- `GET /hazards`: 위험 목록·경보 배너·위험 모니터링
- `GET /workers/snapshot`: 현장 지도의 작업자 위치
- 운영자 E-Stop API: 비상정지 버튼(현재 비활성)
- WebSocket `/ws/state`: 지금은 1초 주기 조회(`usePolling`)
- CCTV 영상: ROS 2 토픽을 웹으로 보내는 브리지(#55)

## 구조

```text
src/
├── api/types.ts       # backend/app/models.py와 같은 이름·상태값
├── api/client.ts      # fetch 래퍼, 미구현 API는 MissingApiError
├── hooks/usePolling.ts
├── components/        # Layout(상단·메뉴), CriticalBanner, ui(Panel·Table·Stat·Status·Async)
└── pages/             # 화면 7개
```
