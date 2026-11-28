# OpenVLA 7D 연동

GPU 환경에 CUDA PyTorch, `transformers`, `numpy`, `Pillow`를 설치합니다. PyTorch는
서버의 CUDA 버전에 맞게 먼저 설치해야 합니다. 모델은 첫 실행에만
로드하고 반복 추론 동안 메모리에 유지합니다.

## 1회 추론

`openvla_single_image_infer.py`는 RGB 이미지와 영문 조작 명령으로 OpenVLA 7B의
7차원 행동을 생성합니다. GPU 노드에서 다음과 같이 실행하면 결과를
파일로 남기고 Backend의 검증 대기 Proposal로 제출합니다.

```bash
python vla/openvla_single_image_infer.py \
  --image /shared/cctv.png \
  --instruction 'grasp the red box' \
  --backend-url http://127.0.0.1:8000 \
  --task-id task-... \
  --manipulation PICK
```

처리 흐름은 `OpenVLA 7D → MANIPULATE/EE_DELTA → Safety Gate → ROS 2
/arm/command`입니다. Backend은 축별 `0.05m`, `0.25rad`를 넘는 한 주기
이동을 거절합니다. 해당 제안이 `APPROVED`된 뒤에만 실행 API를 호출해야
합니다.

TIAGo MuJoCo 브리지는 월드 좌표계 EE_DELTA를 실제 TCP 목표 자세로 누적한
뒤 IK를 풀어 팔 7축과 몸통 승강 관절을 움직입니다. 그리퍼 계약은
`+1=닫기`, `-1=열기`입니다. 사용하는 OpenVLA 체크포인트의 그리퍼
부호가 반대라면 Backend에 제출하기 전 로봇별 Adapter에서 부호를 반전해야
합니다.

OpenVLA 출력 한 번은 PICK/PLACE 전체가 아니라 0.5초 제어 주기 한
번입니다. 새 이미지와 로봇 상태로 반복 추론하고, 관측으로 해당
단계의 완료를 확인한 마지막 주기에만 `--phase-complete`를 추가합니다.
완료 확인 없이 이 플래그를 설정하면 Task Executor가 다음 이동 단계로
조기 전환할 수 있습니다.

## 반복 관측·추론·실행

`openvla_closed_loop.py`는 다음 주기를 돌립니다.

```text
새 CCTV 파일 대기 → OpenVLA 7D → Proposal 등록 → Safety 평가
  → APPROVED만 실행 → ROS 2 RESULT 대기 → 다음 프레임
```

```bash
python vla/openvla_closed_loop.py \
  --backend-url http://127.0.0.1:8000 \
  --task-id task-... \
  --image /shared/latest_cctv.png \
  --instruction 'grasp the red box' \
  --manipulation PICK \
  --completion-signal /shared/phase_complete.json \
  --max-steps 20
```

`--image`는 CV·카메라 프로세스가 새 프레임마다 덮어쓰는 파일입니다.
루프는 mtime과 크기가 달라진 새 프레임만 사용하며, 기본 30초 동안
새 프레임이 없으면 정지합니다. Backend 인증이 켜져 있으면
`OPENVLA_BACKEND_TOKEN`에 Bearer 토큰을 설정합니다.

안전 거절, E-Stop/HELD, 실행 실패, Backend 연결 실패, 결과 시간 초과 중
하나라도 발생하면 다음 추론을 수행하지 않습니다. 각 단계는 JSON Lines
`TASK_STATUS`, `OBSERVED`, `PROPOSED`, `EVALUATED`, `DISPATCHED`, `RESULT`로
출력됩니다.

### 단계 완료 신호

OpenVLA 운동 출력만으로 PICK/PLACE 완료를 추측하지 않습니다. CV 또는
관리자가 다음 JSON을 작성해야 마지막 주기가 `phase_complete=true`로
제출됩니다.

```json
{
  "manipulation": "PICK",
  "complete": true,
  "frame": "latest_cctv.png",
  "step": 7
}
```

`frame` 또는 `step` 중 하나는 반드시 필요하고, 현재 이미지보다 오래된 신호는
무시합니다. CV는 임시 파일을 완성한 뒤 rename해 불완전한 JSON을 루프가
읽지 않게 해야 합니다. 완료 파일이 없으면 기본값은 언제나 `false`입니다.
복사 가능한 형식은 [`examples/phase_complete.example.json`](examples/phase_complete.example.json)에
있습니다. `manipulation`, `frame`, `step`은 현재 실행 주기와 맞게 바꿉니다.

### 종료 코드

- `0`: Task가 `COMPLETED`이거나, 관측으로 현재 조작 단계 완료
- `1`: Backend·프레임·완료 신호 오류
- `2`: Task `HELD`/`FAILED`, 안전 거절, 제어 실패, 또는 `--max-steps` 도달
- `130`: 사용자 중단

## 테스트

GPU 없이 엔진 경계·HTTP·상태 머신·프레임·완료 신호를 검증합니다.

```bash
python -m pytest -q vla/tests
```
