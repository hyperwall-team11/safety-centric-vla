#!/usr/bin/env bash
# 용량이 커서 저장소에 올리지 않는 로봇 모델을 MuJoCo Menagerie에서 받는다.
#   bash scripts/fetch_models.sh
# 대상 (모두 Apache-2.0 또는 BSD-3, 각 폴더 LICENSE 참고) → mujoco/models/<폴더>/
#   franka_emika_panda (Panda, 약 35MB), universal_robots_ur5e (약 31MB),
#   universal_robots_ur10e (약 34MB), kuka_iiwa_14 (약 7MB)
set -euo pipefail
cd "$(dirname "$0")/.."

REPO=https://github.com/google-deepmind/mujoco_menagerie.git
COMMIT=0059d4335f8156206f63a35662313385f7ad6d74  # 2026-10-07, 지금 코드가 검증된 버전
DIRS=(franka_emika_panda universal_robots_ur5e universal_robots_ur10e kuka_iiwa_14)
MISSING=()
for d in "${DIRS[@]}"; do
  [ -d "mujoco/models/$d" ] || MISSING+=("$d")
done
# 예전 스크립트로 받은 Panda 폴더에는 팔 어댑터용 panda_nohand.xml이 없다
if [ -d mujoco/models/franka_emika_panda ] && [ ! -f mujoco/models/franka_emika_panda/panda_nohand.xml ]; then
  rm -rf mujoco/models/franka_emika_panda
  MISSING+=(franka_emika_panda)
fi
if [ ${#MISSING[@]} -eq 0 ]; then
  echo "이미 있음: ${DIRS[*]}"
  exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git -C "$TMP" init -q
git -C "$TMP" remote add origin "$REPO"
git -C "$TMP" sparse-checkout set "${MISSING[@]}"
git -C "$TMP" fetch -q --depth 1 --filter=blob:none origin "$COMMIT"
git -C "$TMP" checkout -q FETCH_HEAD

for d in "${MISSING[@]}"; do
  if [ "$d" != franka_emika_panda ]; then
    mkdir -p "mujoco/models/$d"
    cp -R "$TMP/$d"/. "mujoco/models/$d"/
    echo "완료: mujoco/models/$d ($(du -sh "mujoco/models/$d" | cut -f1))"
  fi
done
case " ${MISSING[*]} " in *" franka_emika_panda "*) ;; *) exit 0 ;; esac

DEST=mujoco/models/franka_emika_panda
mkdir -p "$DEST"
cp -R "$TMP"/franka_emika_panda/{panda.xml,panda_nohand.xml,assets,LICENSE,README.md} "$DEST"/
# 원본의 9자리 keyframe은 상자(freejoint)가 추가된 panda_scene.xml과 맞지 않아 제거한다.
# 같은 home 자세는 panda_scene.xml에 다시 정의돼 있다.
python3 - "$DEST/panda.xml" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
new = re.sub(r"\n  <keyframe>.*?</keyframe>\n", "\n", text, flags=re.S)
if new == text:
    sys.exit("panda.xml에서 keyframe을 찾지 못함 (Menagerie 버전 확인 필요)")
open(path, "w", encoding="utf-8").write(new)
PY
echo "완료: $DEST ($(du -sh "$DEST" | cut -f1))"
