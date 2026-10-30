# MuJoCo 가상 CCTV 장면

`scene.xml`에는 바닥, 금지구역의 빨간 표시, 단순 작업자 모형, `cctv_1` 카메라가 있습니다. 빨간 표시는 시각화일 뿐 안전 규칙을 실행하지 않습니다.

## 이미지 생성

MuJoCo 3.15.0과 Pillow를 설치한 로컬 Python 환경에서 실행합니다.

```bash
python -m pip install mujoco==3.15.0 pillow
python capture.py
```

`outputs/`에 작업자가 구역 바깥과 안에 있는 640×480 PNG 두 장 및 `metadata.json`이 생성됩니다. GUI 뷰어에는 `scene.xml`을 드래그하여 장면을 볼 수 있습니다.
`metadata.json`에는 이미지 SHA-256, 촬영 시각, 카메라 위치·회전·시야각도 기록됩니다. `ground_truth_*` 값은 시뮬레이션 평가용이며 추론 입력으로 사용하지 않습니다.

## Florence-2 탐지

CUDA 지원 PyTorch, `transformers==4.49.0`, `timm`, `Pillow`가 있는 GPU 환경에서 실행합니다. 서버 환경에 맞는 PyTorch를 먼저 준비하세요. 모델 파일은 Hugging Face에서 내려받거나 로컬 경로로 전달합니다.

```bash
python florence_detect.py \
  outputs/cctv_1_outside.png outputs/cctv_1_inside.png \
  --model microsoft/Florence-2-base \
  --output vlm_results
```

외부망이 없는 계산 노드에서는 모델 파일을 미리 공유 저장소에 내려받은 뒤 `--model /path/to/Florence-2-base`로 실행할 수 있습니다. 실행 결과는 이미지별 JSON과 바운딩 박스가 그려진 PNG입니다. `examples/`에는 2026-10-09에 만든 예시 이미지가 있습니다. 이 코드는 허깅페이스 모델 구현을 `trust_remote_code=True`로 로드하므로 신뢰할 수 있는 모델 파일을 사용해야 합니다.

현재 Florence-2 탐지 출력은 픽셀 좌표의 `person` 바운딩 박스이며 신뢰도 점수는 없습니다. `HazardEvent` 판정은 아직 구현되지 않았습니다.

## Observation 변환

`capture.py`로 이미지를 다시 만든 뒤 **그 이미지에 대해** `florence_detect.py`를 실행하세요. 캡처 메타데이터와 탐지 JSON의 이미지 SHA-256이 같아야 합니다. 변환에는 Python 표준 라이브러리만 필요하며 GPU가 없어도 됩니다.

```bash
python observation_export.py \
  vlm_results/cctv_1_outside.json vlm_results/cctv_1_inside.json \
  --metadata outputs/metadata.json \
  --output vlm_results/observations.json
```

변환기는 `person`을 `WORKER`로 매핑하고 상자의 아래쪽 중앙 픽셀에서 바닥 평면(`z=0`)으로 광선을 투영해 `world` 좌표를 추정합니다. 발이 가려졌거나 상자가 부정확하면 위치 오차가 생길 수 있습니다. 출력의 `observation_details`에는 상자 `[x, y, w, h]`와 계산 방식을 보관합니다. `ground_truth_*`는 위치 계산에 사용하지 않습니다.

현재 Florence-2 `<OD>` 출력에는 신뢰도 점수가 없습니다. 따라서 실제 출력은 `pending_confidence`에 보류되고, 필수 `confidence`가 없는 값을 완전한 `Observation`이나 안전 판단 입력으로 사용하지 않습니다. 향후 **검증된 탐지기**가 `<OD>` 결과에 상자별 `scores` 배열을 제공하면 `observations`에 스키마 v1.1의 필수 필드를 갖춘 레코드가 나옵니다. 임의의 점수나 고정값을 넣지 마세요. 현재 백엔드 `Observation` 모델은 v1.1의 선택 필드(`bbox`, `position_source`, `ingested_at`)를 받지 않고 등록 API도 없으므로, 서버 연결은 ONT 담당과 인터페이스를 정한 뒤 진행합니다.

## 연속 추론 속도 측정 (CV-03)

GPU 할당을 받은 터미널에서 아래처럼 실행하면 모델 로드와 워밍업 3회를 제외하고 두 정지 이미지를 순서대로 **60초 이상 반복 추론**합니다. 출력은 `vlm_results/benchmark.json`에 저장됩니다.

```bash
python florence_detect.py \
  outputs/cctv_1_outside.png outputs/cctv_1_inside.png \
  --model /abr/coss38/florence-2-base \
  --output vlm_results \
  --benchmark-seconds 60
```

보고할 값은 `throughput_hz`, `p95_latency_ms`, `completed_iterations`, `gpu`입니다. `below_7hz_fallback_threshold`는 경량 탐지기 전환 검토 신호이며 자동 전환 기능은 아닙니다. 각 반복의 지연도 `latencies_ms`에 남습니다. 측정에는 전처리·GPU 전송·생성·후처리가 포함되지만 **카메라 캡처, 네트워크 전송, 모델 로드, 결과 파일 저장은 포함되지 않습니다.** 정지 이미지 순차 재생이므로 다중 카메라 처리율이나 위험 감지부터 E-Stop까지의 지연을 증명하지 않습니다.

2026-10-16 DIS01에서 수행한 첫 실측 결과는 [`benchmarks/2026-10-16-dis01.md`](benchmarks/2026-10-16-dis01.md)에 기록했습니다. 같은 조건에서 재측정하려면 위 명령의 이미지 경로를 `~/cctv_1_outside.png ~/cctv_1_inside.png`로, 출력 경로를 `~/vlm_results_cv03`으로 바꾸어 실행합니다.

## CPU 검증

```bash
# 저장소 루트에서 실행. 모델과 GPU를 내려받지 않습니다.
python3 -m unittest discover -s mujoco-demo/tests -v
```

검증 범위는 알려진 카메라 위치·회전에서의 바닥 투영, 이미지 해시 불일치 거부, 시간대 검사, 신뢰도 없는 탐지 보류 및 잘못된 신뢰도 거부입니다. 테스트의 점수는 출력 계약을 확인하는 인공 값이며 실제 탐지 신뢰도나 재현율의 근거가 아닙니다. MuJoCo 캡처와 CPU 검증 기록은 [2026-10-30 검토](../docs/cv-review-2026-10-30.md)를 참고하세요.
