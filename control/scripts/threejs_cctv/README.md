# Three.js 가상 CCTV 오프라인 영상

MuJoCo ① 시나리오 장면(`mujoco/models/scenario_scene.xml`)을 Three.js로 렌더해 CV 오프라인 시험용 CCTV 영상을 만든다. 로봇·바닥·탁자·금지구역 표시까지 장면 전체를 그리고, 작업자는 PR #43의 MPFB 실사 skin을 MuJoCo가 계산한 정점 그대로 쓴다.

```bash
cd control/scripts/threejs_cctv
npm install
# 1) MuJoCo 30초 실행 → 장면·프레임·정답 내보내기 (pixi 환경의 python)
python export_scene.py --out ../../outputs/threejs_cctv/data --duration 30 --fps 10
# 2) 헤드리스 Chromium + Three.js 렌더 (Playwright 캐시의 Chromium 사용, 없으면 CHROMIUM_PATH 지정)
node render.mjs --data ../../outputs/threejs_cctv/data --out ../../outputs/threejs_cctv
```

## 출력 (`control/outputs/threejs_cctv/`)

| 경로 | 내용 |
|---|---|
| `cctv_1/000000.png` … | 640×480 RGB, 프레임 번호 = 파일 이름 |
| `cctv_1_mask/000000.png` … | 작업자만 흰색(255), 나머지 검은색. 탁자·로봇에 가려진 부분은 검은색 |
| `cctv_2/`, `cctv_2_mask/` | 같은 형식 |
| `frames.csv` | `frame, sim_time, worker_x, worker_y, worker_yaw, in_keep_out, robot_x, robot_y` (MuJoCo 월드 좌표, m) |
| `cameras.json` | 카메라 위치·회전행렬(`cam_xmat`)·fovy 60°·내부 파라미터(fx, fy, cx, cy) |

작업자 정답 좌표는 탐지·위치 오차 평가용이다. CV·MuJoCo ② 안전 판정 입력으로 쓰지 않는다.

## 장면 조건

- 카메라 위치·방향·화각은 MuJoCo `cctv_1`, `cctv_2`의 `cam_xpos`, `cam_xmat`, `fovy`를 그대로 쓴다. 렌더러 안에서는 MuJoCo 월드 좌표(z 위)를 변환 없이 쓴다. Three.js 표준(y 위)으로 옮길 때는 MuJoCo `(x, y, z)` → Three.js `(x, z, -y)`.
- 작업자 경로(`export_scene.py`의 `TEST_WAYPOINTS`, 0.6 m/s, 약 26초 주기):
  - 금지구역 밖 (3.4, 1.8)
  - (0.75, 0.85): `cctv_2`에서 탁자 A·로봇 뒤에 가려짐 (금지구역 밖)
  - (1.2, -0.5) 2.5초 정지: 금지구역 안, `cctv_1`에서 탁자 B에 다리가 가려짐
  - (1.6, 0.2): 금지구역 안, 가림 없음
  - 금지구역 밖 (3.4, -1.8)
- 로봇은 처음 1.2초 제자리 회전 후 정지한다.
- 바닥은 절차 생성 콘크리트 텍스처, 조명은 MuJoCo 조명 2개(그림자) + 반구광이다. 시연용 안내 문구는 넣지 않는다.
- CCTV 노이즈·압축·렌즈 왜곡은 아직 넣지 않았다.
