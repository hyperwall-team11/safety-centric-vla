from setuptools import find_packages, setup

package_name = "mujoco_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="chxisoei",
    description="ROS 2 ↔ MuJoCo 연결: /cmd_vel → 바퀴 로봇, 로봇 상태 → /odom",
    license="TBD",
    entry_points={
        "console_scripts": [
            "bridge = mujoco_bridge.bridge_node:main",
            "odom_echo = mujoco_bridge.odom_echo:main",
        ],
    },
)
