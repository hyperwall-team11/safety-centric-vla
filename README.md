# Ontology-Driven Safety-Centric VLA

온톨로지 기반 실행 거버넌스를 적용한 안전 중심 VLA 로봇 제어 플랫폼입니다.

VLA(Vision-Language-Action) 모델은 자연어와 시각 정보를 바탕으로 로봇 행동을 제안할 수 있지만, 확률적 생성 특성 때문에 물리 제약을 위반하거나 위험한 궤적을 만들 수 있습니다. 이 프로젝트는 VLA의 출력을 곧바로 로봇에 전달하지 않고, 온톨로지와 Action 제출 기준으로 검증한 뒤 승인된 행동만 실행하는 구조를 구현합니다.

> AI는 행동을 제안하고, 실행은 온톨로지가 승인합니다.

## 프로젝트 목표

한 학기 16주 동안 다음 기능을 갖춘 통합 MVP를 구현합니다.

- 자연어 명령을 `TaskOrder` 객체로 변환
- VLA 출력을 실행 명령이 아닌 `ActionProposal`로 저장
- 로봇, 작업자, 금지구역, 위험 이벤트를 온톨로지 객체로 관리
- 속도, 토크, 금지구역, 작업자 안전거리 기준으로 행동 검증
- 위험 행동 거부와 `TriggerEStop`, `Hold State` 처리
- 승인된 Action만 ROS 2 제어 노드로 전달
- Isaac Sim 디지털 트윈과 온톨로지 상태 동기화
- 제안·승인·거부·실행 전 과정을 감사 로그로 기록

최종 결과물은 정상 실행과 실시간 위험 개입 두 시나리오의 End-to-End 시연입니다.

## 핵심 구조

```text
자연어 명령 + 카메라 입력
          │
          ▼
   TaskOrder 객체화
          │
          ▼
 VLA 행동 제안 생성
 ActionProposal(PROPOSED)
          │
          ▼
 온톨로지 Action 제출 기준
 속도 · 토크 · 금지구역 · 안전거리 검사
       ┌──┴──────────────┐
       │                 │
   APPROVED          REJECTED
       │                 │
       ▼                 ▼
 ROS 2 실행       TriggerEStop
 Isaac Sim 동기화  Hold State
       │
       ▼
     Audit Trail
```

모든 상태 변경은 Action을 통해서만 일어납니다. VLA나 개별 모듈이 로봇 상태를 직접 변경하지 못하도록 실행 권한을 분리합니다.

## 온톨로지 모델

### Semantic Layer

현장에 무엇이 있는지를 표현합니다.

- `Robot`: 관절 상태, 속도·토크 한계
- `Worker`: 위치, 안전거리
- `KeepOutZone`: 금지구역 경계
- `TaskOrder`: 사용자의 작업 명령
- `ActionProposal`: VLA가 생성한 행동 제안
- `HazardEvent`: 작업자 접근, 화재 등 위험 이벤트

객체 사이의 관계는 `Link`로 관리하고, 센서와 VLM 결과에 따라 위치·상태 속성을 갱신합니다.

### Kinetic Layer

상태를 변경하는 행동을 정의합니다.

- `ExecuteMotion`
- `TriggerEStop`
- `ResumeTask`
- `ResolveHazard`

각 Action에는 `Submission Criteria`가 연결됩니다. 기준을 통과한 Action은 `APPROVED`, 기준을 위반한 Action은 `REJECTED` 상태가 됩니다.

## 검증 시나리오

### Scenario 1. 정상 동작

`A구역 부품 상자를 B 작업대로 이송해 줘`라는 명령을 처리합니다.

1. 명령을 `TaskOrder`로 등록
2. VLM이 상자와 작업대를 인식
3. VLA가 접근·파지·이동·놓기 행동을 `ActionProposal`로 생성
4. 제출 기준을 통과한 `ExecuteMotion`을 `APPROVED` 처리
5. ROS 2로 실행하고 온톨로지 및 감사 로그에 결과 기록

### Scenario 2. 실시간 위험 개입

작업 도중 작업자가 접근하거나 화재가 감지되는 상황을 검증합니다.

1. VLM이 위험을 감지하고 `HazardEvent` 생성
2. 작업자 위치 또는 위험 상태 갱신
3. `TriggerEStop` 발행과 진행 중 제안 거부
4. 위험 해제 전까지 `Hold State` 유지
5. `ResolveHazard`와 `ResumeTask` 승인 후 작업 재개

## 기능 요구사항

| ID | 기능 | 구현 범위 | 확인 방법 |
|---|---|---|---|
| R1 | 명령·행동 제안 | `TaskOrder`와 `ActionProposal` 생성 | 객체 상태 확인 |
| R2 | 현장 상태 인식 | 작업자 위치·위험 이벤트 갱신 | 10Hz 이상 탐지 목표 |
| R3 | 행동 제출 검증 | 속도·토크·금지구역·안전거리 검사 | 위험 제안 차단 |
| R4 | 정지·재개 통제 | E-Stop, Hold State, 안전한 재개 | 위험 개입 시연 |
| R5 | 실행·감사 추적 | 승인 Action 실행 및 전체 이력 저장 | 정상 시연·로그 확인 |

## 비기능 목표

- 위험 요소 탐지: 10Hz 이상
- 위험 인지부터 E-Stop 발행까지: 100ms 이하 목표
- 위험 시나리오 차단 성공률: 100% 목표
- Action 제출부터 판정까지: 50ms 이하 목표
- 정상 실행과 Safety Interception의 End-to-End 검증
- 온톨로지 상태와 감사 로그의 일관성 확인

위 수치는 MVP 검증 목표이며, 구현 과정에서 실제 측정값과 함께 관리합니다.

## 개발 일정

| 기간 | 단계 | 주요 작업 | 마일스톤 |
|---|---|---|---|
| 1~4주 | 기반·스키마 | 요구분석, Object/Link/Action 스키마, ROS 2·Isaac Sim 환경 구성 | M1: 온톨로지 스키마 확정 |
| 5~8주 | 모듈 개발 | 온톨로지 서버, Action 파이프라인, NLP 파서, VLM 위험 인식, Guardrail | M2: Action 파이프라인 데모 |
| 9~12주 | 시스템 통합 | VLA Proposal 연동, ROS 2 실행, Isaac Sim과 온톨로지 동기화 | M3: 통합 파이프라인 가동 |
| 13~16주 | 검증·시연 | 정상·위험 개입 시험, 감사 로그 검증, 최종 시연·보고서 | M4: MVP 최종 시연 |

## 팀 역할

| 역할 | 담당 | 주요 책임 |
|---|---|---|
| CV | 강영한(팀장) | VLM 위험 감지, Worker·HazardEvent 갱신 |
| NLP | 팀원 A | 자연어 명령 파싱, TaskOrder·VLA 인터페이스 |
| ONT | 팀원 B | 온톨로지 스키마, Action 서버, Guardrail, 감사 로그 |
| CTL | 팀원 C | ROS 2 제어, Isaac Sim, 상태 동기화 |

모든 모듈은 온톨로지 API를 공통 인터페이스로 사용합니다. 초기 단계에서 스키마를 확정해 병렬 개발과 통합을 지원합니다.

## 기술 스택

- AI: PyTorch, OpenVLA, LLaVA 또는 Florence-2, OpenCV
- 온톨로지 백엔드: FastAPI, Redis, WebSocket 또는 gRPC
- 로봇 제어: ROS 2 Humble/Jazzy
- 시뮬레이션: NVIDIA Isaac Sim / Isaac Lab
- 모델·상태 연동: Object/Link/Action 스키마, Isaac Sim 양방향 동기화

## 제약사항

- ISO 10218 및 ISO/TS 15066 관련 안전 요구를 검토
- 모듈 간 주요 상태·행동 통신은 온톨로지 API를 통해 관리
- OpenVLA, LLaVA, ROS 2, Isaac Sim 및 기타 오픈소스 라이선스 준수
- 본 프로젝트의 목표는 한 학기 MVP이며, 상용 안전 인증 제품을 의미하지 않음

## 참고 자료

- `Ontology_Driven_Safety_VLA_개발제안서.pptx`: 시스템 구조, 역할 분담, 16주 개발 로드맵
- `VLA_개발계획_요구분석_16주_최종.pptx`: 요구분석 및 발표용 개발계획

## License

프로젝트 라이선스는 팀의 별도 결정을 따릅니다.
