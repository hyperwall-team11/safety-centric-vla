# OpenVLA 7D 연동

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

OpenVLA 출력 한 번은 PICK/PLACE 전체가 아니라 0.5초 제어 주기 한
번입니다. 새 이미지와 로봇 상태로 반복 추론하고, 관측으로 해당
단계의 완료를 확인한 마지막 주기에만 `--phase-complete`를 추가합니다.
완료 확인 없이 이 플래그를 설정하면 Task Executor가 다음 이동 단계로
조기 전환할 수 있습니다.
