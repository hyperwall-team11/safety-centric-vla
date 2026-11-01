import sys
from pathlib import Path

CONTROL = Path(__file__).resolve().parents[1]
for path in [CONTROL / "mujoco", *sorted((CONTROL / "ros2_ws" / "src").iterdir())]:
    sys.path.insert(0, str(path))
