# 자연어 명령 평가

`command_cases.jsonl`의 정상 명령 50건과 거부 명령 50건을 실제 TaskOrder 파서로
검증합니다. 정상 명령은 로봇·행동·목적지·물체 해석까지 비교하고, 거부 명령은
실행 가능한 작업으로 잘못 승인하지 않는지 확인합니다.

```bash
cd backend
python3 evaluation/run_command_evaluation.py
python3 evaluation/run_command_evaluation.py --json
```

종료 코드는 전부 일치하면 `0`, 한 건이라도 다르면 `1`입니다. 출력의 처리시간은
파서 함수만 측정한 값이며 네트워크, 안전 평가, 제어 어댑터 ACK 시간은 포함하지 않습니다.
KPI 9의 명령→실행 개시 시간은 별도 E2E 측정으로 확인해야 합니다.

새 표현을 지원하거나 거부 정책을 바꿀 때는 케이스의 기대값을 먼저 검토하고, 실제 동작에
맞추기 위해 기대값을 임의로 낮추지 않습니다.
