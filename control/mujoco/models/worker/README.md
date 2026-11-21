# 작업자 실사 모델

| 파일 | 내용 |
|---|---|
| `*.skn`, `*.png` | 피부·티셔츠/청바지·신발·머리카락·눈·눈썹·속눈썹 skin과 텍스처 (1024px) |
| `kinematics.xml` | 걷기 자세 순운동학용 인체 관절 모델 (장면에는 포함되지 않음) |
| `walk.npy` | 걷기 한 주기 이상의 관절 자세 `(프레임, nq)`, 60 fps, 발 미끄러짐 없음 |

에셋 출처: MakeHuman system assets (CC0), Blender MPFB 애드온으로 캐릭터를 만들었습니다.

## 다시 만들기

생성 스크립트는 `control/scripts/worker_skin/`에 있습니다. Blender 5.2와 MPFB 애드온, MakeHuman system assets가 필요합니다.

```bash
brew install --cask blender
blender --online-mode -c extension install -s -e mpfb
# makehuman_system_assets_cc0.zip 을 받아 MPFB 사용자 데이터 폴더에 압축 해제
#   macOS: ~/Library/Application Support/Blender/5.2/extensions/.user/blender_org/mpfb/data
cd control
blender -b --python scripts/worker_skin/build_human.py            # → outputs/worker/human.blend
blender -b outputs/worker/human.blend --python scripts/worker_skin/export_mujoco_skin.py   # → 이 폴더
```

캐릭터(성별·나이·키·피부·머리·옷)는 `build_human.py` 위쪽 설정에서 바꿉니다. `walk.npy`와 `kinematics.xml`은 다시 만들 필요가 없습니다.
