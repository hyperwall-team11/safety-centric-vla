"""1단계: MPFB(MakeHuman)로 실사형 작업자 캐릭터를 만들어 outputs/worker/human.blend 로 저장한다.

  cd control && blender -b --python scripts/worker_skin/build_human.py

피부(SSS), 눈, 눈썹, 속눈썹, 머리카락, 옷, 신발, game_engine 리그(뼈대)를 붙인다.
에셋은 MakeHuman system assets (CC0) 을 사용한다.
"""
import importlib
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]  # control/
DATA = Path.home() / "Library/Application Support/Blender/5.2/extensions/.user/blender_org/mpfb/data"

# ---- 캐릭터 설정 (바꿔도 됨) ----
MACRO = {
    "gender": 1.0, "age": 0.5, "muscle": 0.55, "weight": 0.5, "proportions": 0.7, "height": 0.6,
    "cupsize": 0.5, "firmness": 0.5, "race": {"asian": 0.9, "caucasian": 0.1, "african": 0.0},
}
SKIN = "skins/young_asian_male/young_asian_male.mhmat"
HAIR = "hair/short02/short02.mhclo"
EYEBROWS = "eyebrows/eyebrow001/eyebrow001.mhclo"
EYELASHES = "eyelashes/eyelashes01/eyelashes01.mhclo"
CLOTHES = ["clothes/male_casualsuit04/male_casualsuit04.mhclo", "clothes/shoes06/shoes06.mhclo"]


def mpfb(mod):
    return importlib.import_module(f"bl_ext.blender_org.mpfb.services.{mod}")


def main():
    HumanService = mpfb("humanservice").HumanService

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module="bl_ext.blender_org.mpfb")

    base = HumanService.create_human(macro_detail_dict=MACRO)
    base.name = "Human"
    HumanService.set_character_skin(str(DATA / SKIN), base, skin_type="ENHANCED_SSS")
    HumanService.add_builtin_rig(base, "game_engine")
    HumanService.add_mhclo_asset(str(DATA / "eyes/high-poly/high-poly.mhclo"), base, asset_type="Eyes",
                                 material_type="PROCEDURAL_EYES", subdiv_levels=1)
    HumanService.add_mhclo_asset(str(DATA / EYEBROWS), base, asset_type="Eyebrows", subdiv_levels=0)
    HumanService.add_mhclo_asset(str(DATA / EYELASHES), base, asset_type="Eyelashes", subdiv_levels=0)
    HumanService.add_mhclo_asset(str(DATA / HAIR), base, asset_type="Hair", subdiv_levels=1)
    for c in CLOTHES:
        HumanService.add_mhclo_asset(str(DATA / c), base, asset_type="Clothes", subdiv_levels=1)

    out = ROOT / "outputs" / "worker" / "human.blend"
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    print("SAVED", out, "rig:", rig.name, "bones:", len(rig.data.bones),
          "objects:", [o.name for o in bpy.data.objects])


if __name__ == "__main__":
    main()
