from setuptools import find_packages, setup

package_name = "safety_control"

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
    description="HOLD / E-Stop 판단, 안전 경로 토픽·QoS, 검증 서버 대역 시험 하네스",
    license="TBD",
    entry_points={
        "console_scripts": [
            "test_harness = safety_control.test_harness:main",
        ],
    },
)
