# AGENTS.md

AI 코딩 도구(Claude Code, Codex, Cursor 등)가 이 저장소에서 작업할 때 따르는 규칙입니다. 사람용 협업 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)가 기준이며, 이 문서는 AI가 놓치기 쉬운 부분을 강제 규칙으로 정리합니다. 두 문서가 충돌하면 CONTRIBUTING.md를 따르고, 이 문서를 고치는 PR을 올립니다.

## 1. 프로젝트 개요

온톨로지 기반 실행 거버넌스를 적용한 안전 중심 VLA 로봇 제어 플랫폼입니다. **VLA 출력은 실행 명령이 아니라 `ActionProposal`이며, 온톨로지 Action 제출 기준을 통과한 행동만 실행합니다.** 이 원칙을 우회하는 코드(제안을 검증 없이 로봇에 전달, 모듈이 로봇 상태를 직접 변경 등)를 만들지 않습니다.

| 폴더 | 모듈 | 실행·테스트 |
|---|---|---|
| `backend/` | `backend`, `nlp`, `ont` (FastAPI) | `cd backend && python3 -m pytest` |
| `control/` | `ctl` (ROS 2 + MuJoCo, pixi) | `cd control && pixi run test` 또는 `python3 -m pytest tests` |
| `mujoco-demo/` | `cv`, `sim` (Florence-2 탐지 실험) | [mujoco-demo/README.md](mujoco-demo/README.md) |
| `docs/` | 기준 문서·명세·진행 기록 | [docs/README.md](docs/README.md) |

시뮬레이터는 **MuJoCo**입니다(2026-10-11 Isaac Sim에서 변경). 새 코드·문서에 Isaac Sim을 쓰지 않습니다.

## 2. 작업 전 확인

- 작업 시작 전 `git status`와 현재 브랜치를 확인합니다. 사용자가 만든 커밋되지 않은 변경은 되돌리거나 덮어쓰지 않습니다.
- 새 작업은 최신 `origin/develop`에서 브랜치를 땁니다. 이름은 `feat|fix|docs|chore/<모듈>-<내용>` (예: `feat/ctl-tiago-adapter`).
- `main`, `develop`에 직접 커밋·푸시하지 않고, 어떤 브랜치든 `--force` 대신 `--force-with-lease`만 씁니다.
- 한 브랜치·PR에는 한 가지 기능이나 수정만 담습니다. 작업 중 발견한 다른 문제는 고치지 말고 사용자에게 알리거나 이슈로 남깁니다.
- 다른 파트(cv/nlp/ont/ctl) 폴더의 코드는 그 작업에 꼭 필요할 때만 수정하고, PR의 `⚠️ 영향 범위`에 적습니다.

## 3. 코드 규칙

- 객체명·필드명·열거값·안전 수치는 [docs/ontology-schema-v1.1.md](docs/ontology-schema-v1.1.md)가 기준입니다. 임의로 새 이름이나 상태값을 만들지 말고 스키마에 있는 것을 씁니다.
- 온톨로지 스키마나 공개 API(요청·응답 형식, 경로, 상태 전이)를 바꾸는 변경은 **ONT 담당자 승인이 필요**하다는 점을 사용자에게 먼저 알리고 진행합니다.
- 안전 관련 수치(속도·토크 상한, 안전거리, E-Stop 지연, 판정 주기)는 상수로 두고 근거 문서를 주석으로 남깁니다. 테스트를 통과시키려고 수치를 완화하지 않습니다.
- 주변 코드의 스타일·명명·주석 밀도를 따릅니다. 기존 코드를 대규모로 리포맷하지 않습니다.
- 기능을 추가·수정하면 해당 폴더의 `tests/`에 테스트를 추가하고, PR 전에 위 표의 테스트 명령을 실행합니다. 실패하면 실패한 채로 숨기지 말고 PR과 사용자에게 그대로 알립니다.
- 커밋하지 않는 것: API 키·토큰·`.env`, 모델 가중치(OpenVLA, Florence-2 등), 대용량 데이터, `outputs/`·캐시·`.pixi/`. 새 의존성은 `requirements.txt` / `pixi.toml`에 반영합니다.

## 4. 커밋 메시지

```text
<type>(<모듈>): <요약>
```

- type: `feat` `fix` `docs` `refactor` `test` `chore`
- 모듈: `cv` `nlp` `ont` `ctl` `backend` `sim` (문서·CI 전반이면 생략 가능: `docs: ...`)
- 요약은 50자 이내, 영어 또는 한국어. 필요하면 한 줄 띄우고 본문에 변경 이유를 적습니다.

## 5. Pull Request 작성 규칙

- PR을 만들기 전에 반드시 [.github/pull_request_template.md](.github/pull_request_template.md)를 읽고, **그 템플릿의 섹션 구조와 제목(이모지 포함)을 그대로 사용**해 본문을 작성합니다.
  - 섹션을 임의로 바꾸거나(예: `## 변경 내용`, `## Summary`) 새 형식을 만들지 않습니다.
  - 해당 사항이 없는 섹션은 지우지 말고 `해당 없음`이라고 적습니다.
  - 체크박스는 실제로 한 것만 체크합니다. 실행하지 않은 테스트를 체크하지 않습니다.
  - 템플릿의 HTML 주석(`<!-- -->`) 안내는 내용을 채운 뒤 지워도 됩니다.
- `gh pr create --body`/`--body-file`로 본문을 넣을 때도 위 규칙을 따릅니다. (`--fill`은 템플릿을 무시하므로 쓰지 않습니다.)
- PR 제목은 `<type>(<모듈>): <요약>` 형식이며, **요약은 반드시 한국어로** 씁니다. type·모듈·코드 식별자(`TaskOrder` 등)만 영어로 둡니다.
  - 예) `feat(ctl): TIAGo 팔 집기·놓기 MANIPULATE 동작 추가`, `docs: AI 에이전트 작업 규칙 추가`
  - `develop`에는 Squash and merge되므로 PR 제목이 최종 커밋 메시지가 됩니다.
- 대상 브랜치는 `develop`입니다. `main`은 `develop` → `main` PR로만 반영합니다.
- 관련 이슈가 있으면 `Closes #번호`, 요구사항 ID(R1~R5)를 적습니다.
- PR을 머지하거나 리뷰를 승인하지 않습니다. 머지는 사람 리뷰어가 합니다.

## 6. 문서 규칙

- 문서는 한국어로 씁니다. 코드·객체명·명령어·경로는 백틱으로 감쌉니다.
- 문서 우선순위: **스키마 v1.1 > 개발계획서·사용자정의서**. 기준 문서끼리 내용이 다르면 스키마를 따르고, 다른 문서를 맞추는 수정을 제안합니다.
- 기준 문서(`docs/` 의 개발계획서·스키마·사용자정의서)는 GitHub 이슈 제안 → 영향 파트 승인 후에만 고칩니다. 사용자가 명시적으로 요청하지 않으면 AI가 직접 수정하지 않습니다.
- 코드를 바꿔 동작·API·실행 방법이 달라지면 **같은 PR에서** 관련 문서를 함께 갱신합니다.
  - API 변경 → [docs/backend-api-spec-v0.2.md](docs/backend-api-spec-v0.2.md)
  - 실행 방법·명령 변경 → 해당 폴더 `README.md`
  - 진행 단계 변경 → `control/README.md` 등의 진행 표
- 새 문서를 `docs/`에 추가하면 [docs/README.md](docs/README.md) 목록에 한 줄 추가합니다.
- 파일명: 명세·스키마는 `영문-kebab-case-v<버전>.md`, 회의·진행 기록은 `<주제>_<YYYY-MM-DD>.md`. 버전을 올리면 문서 상단에 변경 이력을 남깁니다.
- 날짜는 `YYYY-MM-DD` 절대 날짜로 씁니다("어제", "이번 주" 금지). 구현되지 않은 기능을 구현된 것처럼 쓰지 않고, 미구현 범위는 따로 표시합니다.
