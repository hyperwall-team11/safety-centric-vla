"""공장 현장(plant_scene)의 로봇 팔 4대 어댑터를 띄운다 (이슈 #95).

    ros2 launch robot_adapter arms.launch.py

같은 arm_adapter 노드를 robot_id·robot_model만 바꿔 4번 띄운다. 베이스 위치·방향은 어댑터가
control/mujoco/arms.PLANT_ARMS에서 robot_id로 찾는다. MuJoCo 브리지는 arms:=true로 따로 실행한다.
"""
from launch import LaunchDescription
from launch_ros.actions import Node

ARMS = [("arm-iiwa14", "iiwa14"), ("arm-panda", "panda"), ("arm-ur5e", "ur5e"), ("arm-ur10e", "ur10e")]


def generate_launch_description():
    return LaunchDescription([
        Node(package="robot_adapter", executable="arm_adapter", name=f"arm_adapter_{rid.replace('-', '_')}",
             output="screen", parameters=[{"robot_id": rid, "robot_model": model}])
        for rid, model in ARMS
    ])
