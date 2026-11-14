# Three.js 가상 CCTV 오프라인 영상

MuJoCo ① 공장 장면(`mujoco/models/plant_scene.xml`)을 Three.js로 렌더해 CV 오프라인 시험용 CCTV 영상을 만든다.

- **MuJoCo**: 위치·충돌·가림의 기준. 로봇 셀(TIAGo, 금지구역, A·B 작업대)은 `scenario_scene.xml`과 같고, 주변 설비는 단순 형상 `prop_<종류>`로 둔다.
- **Three.js**: `prop_*`를 실사형 설비(펜스·로봇팔·탱크·배관 랙·지게차·전기실·맨홀·난간)로 덮어 그리고, 바닥은 PBR 콘크리트 + 도장 표시로 그린다. 작업자 이동 범위 밖 가장자리에는 glTF 소품을 둔다.

```bash
cd control/scripts/threejs_cctv
npm install
python fetch_assets.py          # Poly Haven CC0 텍스처·HDRI·glTF 약 50 MB → assets/ (커밋하지 않음)
# 1) MuJoCo 30초 실행 → 장면·프레임·정답 내보내기 (pixi 환경의 python)
python export_scene.py --out ../../outputs/threejs_cctv/data --duration 30 --fps 10
# 2) 헤드리스 Chromium + Three.js 렌더 (Playwright 캐시의 Chromium 사용, 없으면 CHROMIUM_PATH 지정)
node render.mjs --data ../../outputs/threejs_cctv/data --out ../../outputs/threejs_cctv
```

300프레임 × 카메라 2대 렌더는 Apple M3에서 약 70초 걸린다.

### 지게차 모델

`prop_forklift_*`는 `models/forklift.glb`로 그린다. 출처: Sketchfab [Forklift low poly](https://sketchfab.com/3d-models/forklift-low-poly-8ab650b3982243f8b661142de50f79c9), Ricardo Sanchez, **CC-BY-4.0** (영상·보고서에 출처 표기 필요).
2.7 MB라 저장소에 함께 둔다. 모델은 MuJoCo 박스(`prop_forklift_1`) 안에 비율을 유지해 맞추며, 포크가 +x를 향한다(`props.js`의 `FORKLIFT_YAW`).
모델을 바꾸면 바닥 라이트맵(`floor_bake.png`)을 다시 굽는다.

### 실시간 재생 (Cycles로 구운 바닥 + Three.js)

바닥(화면의 대부분)은 Cycles로 조명·그림자·반사광·도장을 한 번 구워(`floor_bake.png`) 쓰고, 로봇·작업자만 실시간 그림자를 드리운다.
구운 파일이 `data/`에 있으면 `render.html`이 자동으로 쓴다(`?nobake`로 끌 수 있음).

```bash
blender -b -P blender_render.py -- --data ../../outputs/threejs_cctv/data --out /tmp --bake ../../outputs/threejs_cctv/data/floor_bake.png --bake-ppm 128 --samples 512
node render.mjs --live-test              # 실시간 재생 FPS 측정 (Apple M3: 약 58 FPS)
```

브라우저로 보려면 `node render.mjs`가 띄우는 것과 같은 정적 서버에서 `render.html?live&cam=cctv_1`을 연다.
굽기는 A6000에서 30초, Apple M3에서 수 분 걸린다. 장면(설비 배치·조명)을 바꾸면 다시 굽는다.

### 실사 렌더 (Blender Cycles 광선추적, 권장)

Three.js 렌더는 마스크·박스·빠른 확인용이고, CV 입력용 최종 영상은 같은 장면을 Blender Cycles로 렌더한다.
형상·카메라가 같으므로 Three.js가 만든 마스크·`boxes.json`이 그대로 맞는다.

```bash
node render.mjs --glb                     # data/scene.glb(텍스처 뺀 장면), floor_paint.png, decor.json
blender -b -P blender_render.py -- --data ../../outputs/threejs_cctv/data --out ../../outputs/threejs_cctv --samples 128
python cctv_post.py --dir ../../outputs/threejs_cctv   # raw_*.png → CCTV 효과 적용한 최종 PNG
```

- `blender_render.py`는 GLB를 불러와 Poly Haven 원본 텍스처·소품을 다시 붙이고, 작업자 3명의 skin 정점과 안전모를 프레임마다 넣는다. GPU는 OPTIX → CUDA → HIP → METAL 순으로 찾고 없으면 CPU를 쓴다.
- 속도(640×480, 카메라 2대, 128 샘플): RTX A6000(CUDA) 약 7초/프레임, Apple M3(Metal, 48 샘플) 약 27초/프레임.
- SLURM 서버 예: `sbatch --array=0-3 slurm_render.sh`로 GPU 4개에 프레임을 나눠 렌더한다(300프레임 약 10분). Blender는 공식 리눅스 tar를 홈 폴더에 풀어 쓰면 된다(관리자 권한 불필요).

## 출력 (`control/outputs/threejs_cctv/`)

| 경로 | 내용 |
|---|---|
| `cctv_1/000000.png` … | 640×480 RGB, 프레임 번호 = 파일 이름. 박스·문구 없음 |
| `cctv_1_mask/000000.png` … | 작업자(안전모 포함)만 흰색(255). 설비·로봇에 가려진 부분은 검은색 |
| `cctv_2/`, `cctv_2_mask/` | 같은 형식 |
| `frames.csv` | `frame, sim_time, worker_id, x, y, yaw, in_keep_out, dist_to_robot, robot_x, robot_y` (MuJoCo 월드 좌표, m) |
| `boxes.json` | 프레임·카메라·작업자별 화면에 보이는 영역 상자(픽셀). `pixels=0`이면 화면에 안 보임 |
| `cameras.json` | 카메라 위치·회전행렬(`cam_xmat`)·fovy 60°·내부 파라미터(fx, fy, cx, cy), 작업자 목록, 금지구역 |
| `viewer.html` | 확인용 뷰어 (`python3 -m http.server`로 이 폴더를 열기). 시연용 HUD는 여기서만 그린다 |

작업자 정답 좌표는 탐지·위치 오차 평가용이다. CV·MuJoCo ② 안전 판정 입력으로 쓰지 않는다.

## 작업자

| ID | 복장 | 경로 |
|---|---|---|
| `worker-01` | 형광 연두 조끼, 남색 작업복, 흰 안전모 | 금지구역 밖 → `cctv_2`에서 탁자 A·로봇 뒤 → 금지구역 안 2.5초(`cctv_1`에서 탁자 B에 다리 가림) → 금지구역 안 → 밖 → 맨홀 옆 |
| `worker-02` | 주황 조끼, 청바지, 노란 안전모 | 로봇 셀 조작반 앞 9초 → 이동 |
| `worker-03` | 형광 연두 조끼, 청바지, 주황 안전모 | 지게차 옆 통로 왕복 |

- `worker-01`은 MuJoCo skin을 그대로 쓴다. `worker-02`, `worker-03`은 MuJoCo에 위치 기준점(`worker_2`, `worker_3` mocap)만 두고, 같은 skin 데이터로 MuJoCo와 같은 선형 블렌드 스키닝을 계산한다. `export_scene.py`가 `worker-01`로 MuJoCo 결과와 비교해 5 mm 이상 다르면 실패한다(현재 1 mm, skin `inflate` 값).
- 조끼는 MakeHuman 셔츠 텍스처의 몸통 부분을 형광색 + 반사띠로 바꾼 것이다. 안전모는 Three.js에서 머리 바디 자세에 붙인다.

## 장면 조건

- 카메라 위치·방향·화각은 MuJoCo `cctv_1`, `cctv_2`의 `cam_xpos`, `cam_xmat`, `fovy`를 그대로 쓴다. 렌더러 안에서는 MuJoCo 월드 좌표(z 위)를 변환 없이 쓴다. Three.js 표준(y 위)으로 옮길 때는 MuJoCo `(x, y, z)` → Three.js `(x, z, -y)`.
- `plant_scene.xml`의 CCTV는 기둥 위 6~6.5 m 높이에서 로봇 셀을 내려다본다. `scenario_scene.xml`(브리지 기본 장면)의 카메라와 위치가 다르다.
- MuJoCo에 없는 glTF 소품은 작업자 이동 범위 밖에만 둔다. 작업자를 가릴 수 있는 설비는 모두 `prop_*`로 MuJoCo에 있어야 한다.
- 조명: HDRI(`factory_yard`) 환경광 + 그림자 있는 햇빛. 후처리: GTAO(앰비언트 오클루전) → ACES 톤매핑 → CCTV 효과(약한 흐림·채도 감소·비네팅·센서 노이즈) → JPEG(품질 0.72) 압축을 한 번 거친 뒤 PNG로 저장.
- 로봇은 처음 1.2초 제자리 회전 후 정지한다. 로봇팔(`prop_arm_*`)은 장식용으로 천천히 움직인다.

## 에셋 라이선스

`fetch_assets.py`가 받는 텍스처·HDRI·모델은 모두 [Poly Haven](https://polyhaven.com/license) CC0이다. 작업자 모델은 MakeHuman CC0 에셋(PR #43)이다.
