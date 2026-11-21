"""Check the Python environment before loading the ROS camera detector model."""

import importlib
import json
import os
import platform


def main():
    report = {"python": platform.python_version(), "modules": {},
              "ros_environment": {key: os.environ.get(key) for key in
                                  ("ROS_DISTRO", "ROS_DOMAIN_ID", "ROS_LOCALHOST_ONLY")}}
    errors = []
    for name in ("rclpy", "sensor_msgs.msg", "PIL", "torch", "transformers", "timm"):
        try:
            module = importlib.import_module(name)
            report["modules"][name] = getattr(module, "__version__", "import OK")
            if name == "torch":
                report["cuda_available"] = module.cuda.is_available()
                if report["cuda_available"]:
                    report["gpu"] = module.cuda.get_device_name(0)
                else:
                    errors.append("CUDA is unavailable in this Python environment")
        except Exception as error:
            report["modules"][name] = f"{type(error).__name__}: {error}"
            errors.append(f"Cannot import {name}")
    report["errors"] = errors
    report["note"] = "Imports/CUDA only; does not verify ROS discovery, topic delivery or inference."
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
