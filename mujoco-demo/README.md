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
