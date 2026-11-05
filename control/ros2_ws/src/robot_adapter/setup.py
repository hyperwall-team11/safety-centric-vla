from setuptools import find_packages, setup

package_name = "robot_adapter"

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
    description="공통 Action(MOVE_TO·MANIPULATE·STOP·RESUME)을 로봇 명령으로 변환하는 어댑터",
    license="TBD",
    entry_points={
        "console_scripts": [
            "mock_adapter = robot_adapter.mock_adapter_node:main",
            "tiago_adapter = robot_adapter.tiago_adapter_node:main",
        ],
    },
)
