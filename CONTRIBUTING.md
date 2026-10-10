# 협업 가이드

Ontology-Driven Safety-Centric VLA 팀의 브랜치, 커밋, PR, 리뷰 규칙입니다. 모든 팀원은 작업을 시작하기 전에 이 문서를 확인합니다.

## 1. 브랜치 구조

```text
main      ← 마일스톤 시연용 안정 버전 (develop에서만 머지)
 ▲
develop   ← 통합 브랜치, 기본 브랜치
 ▲
feat/*, fix/*, docs/*, chore/*   ← 개인 작업 브랜치
```

| 브랜치 | 용도 | 생성 위치 | 머지 대상 |
|---|---|---|---|
| `main` | M1~M4 마일스톤과 시연용 안정 버전 | - | - |
| `develop` | 모듈 통합, 기본 브랜치 | - | `main` |
| `feat/<모듈>-<내용>` | 기능 개발 | `develop` | `develop` |
| `fix/<모듈>-<내용>` | 버그 수정 | `develop` | `develop` |
| `docs/<내용>` | 문서 작성·수정 | `develop` | `develop` |
| `chore/<내용>` | CI, 설정, 의존성 | `develop` | `develop` |

### 모듈 이름

| 모듈 | 범위 |
|---|---|
| `cv` | VLM 위험 감지, `Worker`·`HazardEvent` 갱신 |
| `nlp` | 자연어 파싱, `TaskOrder`, VLA 인터페이스 |
| `ont` | 온톨로지 스키마, Action 서버, Guardrail, 감사 로그 |
| `ctl` | ROS 2 제어, Isaac Sim, 상태 동기화 |
| `backend` | FastAPI 서버 공통 코드 |
| `sim` | MuJoCo 데모 등 시뮬레이션 실험 |

예시: `feat/cv-hazard-event`, `feat/ont-submission-criteria`, `fix/ctl-estop-latency`

### 규칙

- `main`, `develop`에 직접 푸시하지 않습니다. 모든 변경은 PR로 올립니다.
- `main`에는 `develop`에서 온 PR만 머지할 수 있습니다. GitHub Actions의 `check-source-branch` 검사가 다른 브랜치에서 온 PR을 막습니다.
- 작업 브랜치는 하나의 기능이나 수정만 담고, 가능하면 3일 안에 PR을 올립니다.
- 머지가 끝난 작업 브랜치는 삭제합니다.

## 2. 작업 흐름

```bash
# 1. 최신 develop에서 브랜치 생성
git checkout develop
git pull origin develop
git checkout -b feat/ont-submission-criteria

# 2. 작업 후 커밋
git add .
git commit -m "feat(ont): add speed/torque submission criteria"

# 3. 푸시 전에 develop 최신 내용 반영
git fetch origin
git rebase origin/develop

# 4. 푸시 후 develop 대상으로 PR 생성
git push -u origin feat/ont-submission-criteria
```

`rebase` 이후 이미 푸시한 브랜치를 다시 올릴 때는 `git push --force-with-lease`를 사용합니다. `main`, `develop`에는 강제 푸시를 하지 않습니다.

## 3. 커밋 메시지

```text
<type>(<모듈>): <요약>
```

| type | 의미 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서 |
| `refactor` | 동작 변화 없는 구조 개선 |
| `test` | 테스트 추가·수정 |
| `chore` | 빌드, CI, 설정, 의존성 |

- 요약은 50자 이내로, 영어 또는 한국어로 씁니다.
- 필요하면 한 줄을 띄우고 본문에 변경 이유를 적습니다.

예시:

```text
feat(cv): publish HazardEvent when worker enters KeepOutZone
fix(ctl): reduce TriggerEStop latency below 100ms
docs(ont): describe ActionProposal state transitions
```

## 4. Pull Request

### PR 올리기 전 확인

- [ ] 대상 브랜치가 `develop`인가 (`main` 반영은 아래 5절 참고)
- [ ] 로컬에서 실행과 테스트를 확인했는가 (`backend/tests` 등)
- [ ] 온톨로지 스키마나 API가 바뀌었다면 문서에 반영했는가
- [ ] 비밀키, 모델 가중치, 대용량 데이터가 커밋에 들어가지 않았는가

### PR 본문

```markdown
## 변경 내용
- 무엇을 바꿨는지

## 이유
- 왜 바꿨는지, 관련 요구사항 ID (R1~R5)

## 확인 방법
- 실행한 명령, 테스트 결과, 스크린샷

## 영향 범위
- 다른 모듈에 영향이 있는지 (특히 온톨로지 API 변경 여부)
```

### 머지 방식

- `develop`으로 머지할 때는 **Squash and merge**를 사용합니다. PR 제목이 커밋 메시지가 되므로 커밋 메시지 규칙에 맞춥니다.
- `develop` → `main`은 **Create a merge commit**으로 머지해 이력을 남깁니다.

## 5. 리뷰

- 모든 PR은 작성자가 아닌 팀원 1명 이상의 승인을 받아야 머지할 수 있습니다.
- 가능하면 **다른 모듈 담당자**가 리뷰합니다. 모든 모듈이 온톨로지 API를 공통 인터페이스로 쓰기 때문입니다.
- **온톨로지 스키마·API를 바꾸는 PR은 ONT 담당자의 승인이 필수**입니다. M1 이후 스키마 변경은 팀 전체에 공유합니다.
- 리뷰 요청을 받으면 24시간 안에 응답합니다.
- 리뷰 코멘트가 모두 해결된 뒤 머지합니다.

## 6. main 반영과 버전

`main`에는 마일스톤 시연이나 중간·최종 발표 직전에 반영합니다.

| 마일스톤 | 시기 | 태그 |
|---|---|---|
| M1 온톨로지 스키마 확정 | 4주차 | `v0.1-M1` |
| M2 Action 파이프라인 데모 | 8주차 | `v0.2-M2` |
| M3 통합 파이프라인 가동 | 12주차 | `v0.3-M3` |
| M4 MVP 최종 시연 | 16주차 | `v1.0-M4` |

반영 절차:

1. `develop`에서 전체 시나리오(정상 동작, 실시간 위험 개입)가 동작하는지 확인
2. `develop` → `main` PR 생성, 승인 1명 이상
3. 머지 후 `main`에 태그 추가

```bash
git checkout main
git pull origin main
git tag v0.1-M1
git push origin v0.1-M1
```

시연 직전에 `main`에서 문제가 발견되면 `develop`에서 `fix/*` 브랜치로 고친 뒤 같은 절차로 다시 반영합니다.

## 7. 이슈 관리

- 작업은 GitHub Issue로 등록하고 담당자와 모듈 라벨(`cv`, `nlp`, `ont`, `ctl`)을 지정합니다.
- PR 본문에 `Closes #이슈번호`를 적어 머지 시 이슈가 자동으로 닫히게 합니다.
- 버그 이슈에는 재현 방법, 기대 결과, 실제 결과를 적습니다.

## 8. 저장소에 올리지 않는 것

- API 키, 토큰, `.env` 파일
- 모델 가중치(OpenVLA, Florence-2 등)와 대용량 데이터셋
- 개인 실험 노트북의 출력 결과, 캐시 파일

대용량 파일은 공유 드라이브에 올리고, 저장소에는 받는 방법만 문서로 남깁니다.
