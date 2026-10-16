# MuJoCo 가상 CCTV 장면

`scene.xml`에는 바닥, 금지구역의 빨간 표시, 단순 작업자 모형, `cctv_1` 카메라가 있습니다. 빨간 표시는 시각화일 뿐 안전 규칙을 실행하지 않습니다.

## 이미지 생성

MuJoCo 3.15.0과 Pillow를 설치한 로컬 Python 환경에서 실행합니다.

```bash
python -m pip install mujoco==3.15.0 pillow
python capture.py
```

`outputs/`에 작업자가 구역 바깥과 안에 있는 640×480 PNG 두 장 및 `metadata.json`이 생성됩니다. GUI 뷰어에는 `scene.xml`을 드래그하여 장면을 볼 수 있습니다.

## Florence-2 탐지

CUDA 지원 PyTorch, `transformers==4.49.0`, `timm`, `Pillow`가 있는 GPU 환경에서 실행합니다. 서버 환경에 맞는 PyTorch를 먼저 준비하세요. 모델 파일은 Hugging Face에서 내려받거나 로컬 경로로 전달합니다.

```bash
python florence_detect.py \
  outputs/cctv_1_outside.png outputs/cctv_1_inside.png \
  --model microsoft/Florence-2-base \
  --output vlm_results
```

외부망이 없는 계산 노드에서는 모델 파일을 미리 공유 저장소에 내려받은 뒤 `--model /path/to/Florence-2-base`로 실행할 수 있습니다. 실행 결과는 이미지별 JSON과 바운딩 박스가 그려진 PNG입니다. `examples/`에는 2026-10-09에 만든 예시 이미지가 있습니다. 이 코드는 허깅페이스 모델 구현을 `trust_remote_code=True`로 로드하므로 신뢰할 수 있는 모델 파일을 사용해야 합니다.

현재 탐지 출력은 픽셀 좌표의 `person` 바운딩 박스입니다. 월드 좌표, 신뢰도, `Observation` 및 `HazardEvent` 인터페이스로의 변환은 후속 작업입니다.

## 연속 추론 속도 측정 (CV-03)

GPU 할당을 받은 터미널에서 아래처럼 실행하면 모델 로드와 워밍업 3회를 제외하고 두 정지 이미지를 순서대로 **60초 이상 반복 추론**합니다. 출력은 `vlm_results/benchmark.json`에 저장됩니다.

```bash
python florence_detect.py \
  outputs/cctv_1_outside.png outputs/cctv_1_inside.png \
  --model /abr/coss38/florence-2-base \
  --output vlm_results \
  --benchmark-seconds 60
```

보고할 값은 `throughput_hz`, `p95_latency_ms`, `completed_iterations`, `gpu`입니다. `below_7hz_fallback_threshold`는 경량 탐지기 전환 검토 신호이며 자동 전환 기능은 아닙니다. 각 반복의 지연도 `latencies_ms`에 남습니다. 측정에는 전처리·GPU 전송·생성·후처리가 포함되지만 **카메라 캡처, 네트워크 전송, 모델 로드, 결과 파일 저장은 포함되지 않습니다.** 정지 이미지 순차 재생이므로 다중 카메라 처리율이나 위험 감지부터 E-Stop까지의 지연을 증명하지 않습니다. 실제 수치는 GPU에서 명령을 실행한 뒤 기록해야 합니다.
