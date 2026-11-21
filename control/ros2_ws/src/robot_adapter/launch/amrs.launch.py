"""공장 현장(plant_scene)의 이동형 운반 로봇(AMR) 2대 어댑터를 띄운다.

    ros2 launch robot_adapter amrs.launch.py

같은 amr_adapter 노드를 robot_id만 바꿔 띄운다. MuJoCo 브리지는 amrs:=true로 따로 실행한다.
"""
from launch import LaunchDescription
from launch_ros.actions import Node

AMRS = ["amr-01", "amr-02"]  # control/mujoco/amr.PLANT_AMRS


def generate_launch_description():
    return LaunchDescription([
        Node(package="robot_adapter", executable="amr_adapter", name=f"amr_adapter_{rid.replace('-', '_')}",
             output="screen", parameters=[{"robot_id": rid}])
        for rid in AMRS
    ])
