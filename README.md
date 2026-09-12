# VLA 기반 피지컬 AI 자율 대응 및 실시간 관제 통합 플랫폼

시스템 구조도 및 파이프라인 설계를 바탕으로, 구체적인 구현 및 시스템 통합 시 발생할 수 있는 기술적 병목 요소를 사전에 방지하기 위한 추가 검토 사항과 권장 개발 전략 정리

---

## 1. 데이터 연동 및 실시간 통신 파이프라인 (통신 프로토콜 구체화)

### 1.1 LiveLink 및 3D 동기화 방식 규격화
- 실시간 화재 확산 카메라 영상과 Isaac Sim 가상환경 간 연동을 위해 데이터 전송 포맷(USD, RTSP Stream, WebRTC 등)을 명확히 정의
- 시뮬레이션 씬 업데이트 주기(Hz) 및 렌더링 프레임 레이트 간 synchronization 방안이 필요

### 1.2 ROS 2 및 메시지 브로커 인터페이스
- Spring Boot 백엔드 서버와 Isaac Sim / ROS 2 로봇 제어 노드 간 연결을 위한 인터페이스로 **rosbridge_suite(WebSocket)** 또는 **ZeroMQ / MQTT** 기반 중계 레이어 구축이 필요

### 1.3 End-to-End Latency 목표 설정
- 카메라 입력 수신 → Vision 위험도 탐지 및 계산 → VLA/강화학습 동적 경로 재계산 → 로봇 제어 명령 하강까지의 **전체 지연 시간(End-to-End Latency) 목표치(예: 100ms 이내)**를 설정하여 실시간 제어의 안정성을 검증

---

## 2. AI & Isaac Sim Core (VLA / Risk 모델 최적화)

### 2.1 VLM/VLA 추론 속도 및 실시간성 확보 대책
- GPT-4V / LLaVA 등 대형 VLM 모델은 실시간 프레임 단위(30fps) 제어 추론이 불가능하므로, **Edge/Local 전용 Lightweight VLM(예: OpenVLA, Florence-2 등)**을 분리 배치하는 구조가 필요
- 또는 **Event-driven 추론 방식**(화재 확산 위험도 변화 감지 시에만 VLM 분석 호출)을 적용하여 컴퓨팅 리소스 및 지연 시간을 최적화

### 2.2 Risk Score 산출 알고리즘 수식화
- 화재 크기, 확산 속도, 거리 요소를 반영한 Risk Score 산출 알고리즘을 단순 Heuristic 방식에서 **2D/3D Grid Map 형태의 Costmap**으로 변환하여 ROS 2 Navigation2(Nav2)의 Dynamic Costmap 입력으로 활용하는 방안을 고려

### 2.3 Sim-to-Real Domain Randomization 파이프라인
- 가상환경 학습 알고리즘을 실제 로봇에 적용할 때 발생하는 Gap을 최소화하기 위해 Isaac Sim 내 광원, 마찰력, 카메라 센서 노이즈 등에 대한 **도메인 난수화(Domain Randomization)** 환경 설정이 필요

---

## 3. 백엔드 및 대시보드 아키텍처 (Server & UI)

### 3.1 Spring Boot 및 FastAPI 역할 분담 명확화
- **Spring Boot**: 시스템 메인 비즈니스 로직, 사용자 인증, 대시보드 API, PostgreSQL DB 관리 및 데이터 저장
- **FastAPI**: AI/PyTorch 모델 추론 연동, Isaac Sim 통신 전용 경량화 Inference Gateway 역할 수행

### 3.2 3D 웹 대시보드 렌더링 방식 선택
- React 기반 대시보드에서 3D 씬을 시각화할 때, Isaac Sim의 WebRTC 스트리밍 화면을 디스플레이할 것인지, 혹은 **Three.js / WebGL** 기반으로 백엔드 좌표 데이터(JSON/Protobuf)만 수신하여 클라이언트 측에서 경량 렌더링할 것인지 결정해야 함

---

## 4. 안정 검증 및 예외 처리 (Safety & Interlock Layer)

### 4.1 하드웨어/소프트웨어 Interlock 체계
- VLA 또는 강화학습 모델이 위험 지역 진입 등의 오류 액션을 출력할 경우, 이를 강제로 차단하고 안전 구역으로 복귀시키는 **Safety Controller(안전 펜스)** 로직이 시스템 레이어에 반드시 포함

---

## 5. 팀원 직무 배치 및 추천 스택 보완 점검

| 구분 | 담당 팀원 | 현재 보유 기술 스택 | 추가/보완 추천 기술 요소 |
|---|---|---|---|
| **Vision & Path** | 강영한 (팀장) | PyTorch, PointNet, YOLO-seg, AWS DeepRacer | ROS 2 Navigation2 (Nav2), Costmap2D/3D Dynamic Plugin |
| **VLM & Server** | 김경무 | Spring Boot, FastAPI, React | LangChain / LlamaIndex, vLLM / Ollama (로컬 VLM 최적화) |
| **Action & Sim** | 최민서 | ROS 2, PyTorch, OpenGL, FastAPI, Spring Boot | Isaac ROS (GEMs), URDF/XACRO 로봇 모델링, rosbridge |
| **RL & Sim Twin** | 임성현 | React, React Native, Spring Boot | Isaac Lab / Omniverse Isaac Gym, Python RL Lib (Stable-Baselines3) |

---

## 담당자별 3개월(12주) 달성 전략

### 1. Vision & Path: 강영한 (팀장)
- **추가 요소**: ROS 2 Navigation2 (Nav2), Dynamic Costmap Plugin
- **실행 전략**: 이미 PyTorch, YOLO, AWS DeepRacer 경험이 있어 자율주행 기본 개념 이해도가 높고, Nav2의 기본 패키지를 그대로 가져와 화재 확산 좌표를 입력받아 코스트맵(Costmap)을 실시간으로 업데이트하는 커스텀 플러그인 구현에 집중 필요

### 2. VLM & Server: 김경무
- **추가 요소**: LangChain/LlamaIndex, vLLM / Ollama (로컬 VLM 최적화)
- **실행 전략**: 이미 Spring Boot와 FastAPI 백엔드 구축 능력이 있고, 외부 API(GPT-4V) 방식에서 Ollama/vLLM 기반의 경량 로컬 VLM(OpenVLA 또는 Florence-2)으로 전환하는 작업은 Docker 컨테이너화된 서버를 띄우고 FastAPI로 래핑하는 작업이 필요함

### 3. Action & Sim: 최민서
- **추가 요소**: Isaac ROS (GEMs), URDF/XACRO 로봇 모델링, rosbridge
- **실행 전략**: ROS 2, PyTorch, OpenGL 경험을 두루 갖추고 있어 학습 곡선이 가장 빠를 것으로 예상되며, 기본 로봇 3D 모델(URDF)을 Isaac Sim에 로드하고, rosbridge_suite를 통해 웹/백엔드와 ROS Topic을 송수신하는 파이프라인 구축

### 4. RL & Sim Twin: 임성현
- **추가 요소**: Isaac Lab / Omniverse Isaac Gym, RL Lib (Stable-Baselines3)
- **실행 전략**: 강화학습 및 Isaac Sim 디지털 트윈 환경 생성이 가장 난이도가 높고, 모델을 처음부터 복잡하게 설계하기보다 Stable-Baselines3(PPO 알고리즘 등) 표준 라이브러리를 활용하고, Isaac Lab에서 제공하는 기본 튜토리얼 환경(Grid/Path Finding)을 화재 재난 씬에 맞춰 변형하는 방식으로 진행해야 함

---

## 3개월(12주) 추천 개발 로드맵

### 1개월 차 (1~4주): 환경 구축 및 기본 통신 연결
- Isaac Sim - ROS 2(rosbridge) - Spring Boot / FastAPI 간 메시지 통신 검증
- 로봇 URDF 모델링 및 Isaac Sim 씬 구성
- Ollama/vLLM을 활용한 로컬 VLM 추론 API 서빙 테스트

### 2개월 차 (5~8주): 핵심 기능 모듈화 개발
- Vision 탐지 결과를 ROS 2 Nav2 Dynamic Costmap으로 변환 알고리즘 적용
- Stable-Baselines3 기반 RL 경로 재계산 및 위험도 대응 Action 선택 학습
- React 대시보드와 백엔드 간 실시간 3D 데이터(위험도 맵, 로봇 상태) 연동

### 3개월 차 (9~12주): 통합 및 예외 처리 (Safety Layer)
- 전체 파이프라인(카메라 → Isaac Sim → Risk 계산 → VLA/RL → 로봇 모션) End-to-End 통합
- Safety Controller(위험 구역 진입 강제 차단 Interlock) 적용
- 시나리오 테스트 및 성능 검증 (지연 시간 및 경로 재계산 성공률 측정)
