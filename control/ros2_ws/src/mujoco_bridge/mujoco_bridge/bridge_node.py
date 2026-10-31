"""ROS 2 ↔ MuJoCo 브리지.

구독: /cmd_vel (Twist) → 바퀴 로봇
발행: /odom, /joint_states, TF, 카메라 3대 영상(Image + CameraInfo), 작업자 실제 위치(/sim/worker_pose)

    ros2 run mujoco_bridge bridge                                  # 시나리오 장면 (기본)
    ros2 run mujoco_bridge bridge --ros-args -p robot:=simple      # 단순 시험용 바퀴 로봇
    ros2 run mujoco_bridge bridge --ros-args -p worker_motion:=false -p camera_hz:=10.0
    mjpython <install>/lib/mujoco_bridge/bridge --ros-args -p viewer:=true   # 뷰어 (macOS)

물리와 렌더는 타이머를 나눈다. 물리 타이머는 시뮬 시각이 실제 시각을 따라가도록 필요한 만큼 스텝을 돌리고
(기본 timestep 0.002 s = 500Hz), 카메라 타이머는 camera_hz로 렌더한다. 모든 콜백은 rclpy.spin을 돌리는
한 스레드에서 실행되므로 렌더러(OpenGL 컨텍스트)도 그 스레드에서 만들고 쓴다.

시뮬레이션 모듈은 control/mujoco/의 simulation.py·scenario.py를 사용한다. 위치는 mujoco_dir 파라미터로
지정하며, pixi 환경에서는 기본값($PIXI_PROJECT_ROOT/mujoco)을 그대로 쓰면 된다.
"""
import array
import math
import os
import sys
import time

import rclpy
from geometry_msgs.msg import PoseStamped, TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, JointState
from std_msgs.msg import Bool
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

ANY_NUMBER = ParameterDescriptor(dynamic_typing=True)  # 15와 15.0을 모두 받는다
MAX_CATCHUP_S = 0.1  # 한 번의 물리 타이머에서 따라잡을 최대 시뮬 시간 (밀리면 실시간보다 느려짐)


def load_modules(mujoco_dir: str):
    if not os.path.isfile(os.path.join(mujoco_dir, "simulation.py")):
        raise FileNotFoundError(f"simulation.py 없음: {mujoco_dir} (mujoco_dir 파라미터 확인)")
    sys.path.insert(0, mujoco_dir)
    import scenario
    import simulation
    return simulation, scenario


def make_tf(stamp, parent, child, pos, quat):
    tf = TransformStamped()
    tf.header.stamp = stamp
    tf.header.frame_id = parent
    tf.child_frame_id = child
    t = tf.transform.translation
    t.x, t.y, t.z = (float(v) for v in pos)
    r = tf.transform.rotation
    r.x, r.y, r.z, r.w = quat
    return tf


class MujocoBridge(Node):
    def __init__(self):
        super().__init__("mujoco_bridge")
        default_dir = os.path.join(os.environ.get("PIXI_PROJECT_ROOT", ""), "mujoco")
        mujoco_dir = self.declare_parameter("mujoco_dir", default_dir).value
        simulation, self.scn = load_modules(mujoco_dir)
        robot = self.declare_parameter("robot", simulation.DEFAULT_ROBOT).value
        model_path = self.declare_parameter("model_path", "").value or None  # 비우면 로봇 기본 장면
        physics_hz = float(self.declare_parameter("physics_hz", 100.0, ANY_NUMBER).value)  # 물리 타이머 주기 (스텝은 timestep 단위)
        state_hz = float(self.declare_parameter("state_hz", 50.0, ANY_NUMBER).value)      # /odom·/joint_states·TF
        self.camera_hz = float(self.declare_parameter("camera_hz", 15.0, ANY_NUMBER).value)
        self.image_width = int(self.declare_parameter("image_width", 640, ANY_NUMBER).value)
        self.image_height = int(self.declare_parameter("image_height", 480, ANY_NUMBER).value)
        self.cmd_timeout = float(self.declare_parameter("cmd_timeout", 0.5, ANY_NUMBER).value)
        self.world_frame = self.declare_parameter("world_frame", "world").value
        self.odom_frame = self.declare_parameter("odom_frame", "odom").value
        self.base_frame = self.declare_parameter("base_frame", "base_link").value
        use_cameras = self.declare_parameter("cameras", True).value
        use_worker = self.declare_parameter("worker_motion", True).value
        use_viewer = self.declare_parameter("viewer", False).value

        self.sim = simulation.DiffDriveSim(robot, model_path)
        m = self.sim.model
        self.last_cmd_at = None
        self.wall_start = time.monotonic()
        self.sim_start = self.sim.time
        self.last_rtf_log = self.wall_start

        names = {m.joint(i).name for i in range(m.njnt)}
        self.joint_names = [n for n in self.scn.JOINT_STATE_NAMES if n in names]
        self.joint_adr = [(m.joint(n).qposadr[0], m.joint(n).dofadr[0]) for n in self.joint_names]
        self.base_body = m.joint(self.sim.spec.base_joint).bodyid[0]  # freejoint가 달린 로봇 베이스

        self.worker = None
        bodies = {m.body(i).name for i in range(m.nbody)}
        if use_worker and "worker" in bodies and m.body_mocapid[m.body("worker").id] >= 0:
            self.worker = self.scn.WorkerScript()
            self.has_keep_out = "keep_out_zone" in {m.geom(i).name for i in range(m.ngeom)}
            self.worker_pub = self.create_publisher(PoseStamped, "/sim/worker_pose", 10)
            self.worker_zone_pub = self.create_publisher(Bool, "/sim/worker_in_keep_out", 10)
            self.worker.apply(m, self.sim.data)

        cams = {m.camera(i).name for i in range(m.ncam)}
        self.sensors = [s for s, (cam, _, _) in self.scn.CAMERAS.items() if cam in cams] if use_cameras else []
        self.rig = None
        self.image_pubs, self.info_msgs, self.render_counts = {}, {}, {}
        if self.sensors:
            # 렌더러는 이 스레드(= spin 스레드)에서 생성
            self.rig = self.scn.CameraRig(m, self.image_width, self.image_height, self.sensors)
            for sensor in self.sensors:
                prefix = self.scn.CAMERAS[sensor][1]
                self.image_pubs[sensor] = (
                    self.create_publisher(Image, f"{prefix}/image_raw", qos_profile_sensor_data),
                    self.create_publisher(CameraInfo, f"{prefix}/camera_info", qos_profile_sensor_data))
                self.info_msgs[sensor] = self._camera_info(sensor)
            # 카메라를 한 틱에 한 대씩 돌아가며 렌더 → 한 번에 막히는 시간이 짧아 상태 토픽 주기가 덜 흔들린다
            self.next_camera = 0
            self.create_timer(1.0 / (self.camera_hz * len(self.sensors)), self._render)

        self.viewer = None
        if use_viewer:
            import mujoco.viewer
            self.viewer = mujoco.viewer.launch_passive(m, self.sim.data)
            # 물리 타이머마다 그리면 카메라 렌더가 밀리므로 화면 갱신은 30Hz로 따로
            self.create_timer(1.0 / 30.0, self._sync_viewer)

        self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 10)
        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.joint_pub = self.create_publisher(JointState, "/joint_states", 10)
        self.tf_pub = TransformBroadcaster(self)
        self.static_tf_pub = StaticTransformBroadcaster(self)
        self._publish_static_tf()
        self.create_timer(1.0 / physics_hz, self._physics)
        self.create_timer(1.0 / state_hz, self._publish_state)

        max_linear = self.sim.max_wheel_speed * self.sim.wheel_radius
        self.get_logger().info(
            f"MuJoCo bridge ready: {robot} ({model_path or self.sim.spec.model_path.name}), "
            f"max linear {max_linear:.2f} m/s, joints {len(self.joint_names)}, "
            f"cameras {self.sensors or 'off'} @ {self.camera_hz} Hz {self.image_width}x{self.image_height}, "
            f"worker motion {'on' if self.worker else 'off'}")

    # ---- 입력 ----
    def _on_cmd_vel(self, msg: Twist):
        self.sim.set_velocity(msg.linear.x, msg.angular.z)
        self.last_cmd_at = time.monotonic()

    # ---- 물리 ----
    def _physics(self):
        now = time.monotonic()
        # 명령이 끊기면 정지 (일반적인 cmd_vel 안전 규칙)
        if self.last_cmd_at is not None and now - self.last_cmd_at > self.cmd_timeout:
            self.sim.stop()
            self.last_cmd_at = None
        lag = (self.sim_start + (now - self.wall_start)) - self.sim.time
        if lag > 0:
            if self.worker:
                self.worker.apply(self.sim.model, self.sim.data)
            self.sim.step(min(lag, MAX_CATCHUP_S))
        if now - self.last_rtf_log >= 10.0:
            rtf = (self.sim.time - self.sim_start) / (now - self.wall_start)
            self.get_logger().info(f"sim {self.sim.time:.1f} s / wall {now - self.wall_start:.1f} s "
                                   f"(실시간 대비 {rtf:.2f}배)")
            self.last_rtf_log = now

    def _sync_viewer(self):
        if not self.viewer.is_running():
            raise SystemExit
        self.viewer.sync()

    # ---- 상태 발행 ----
    def _publish_state(self):
        stamp = self.get_clock().now().to_msg()
        x, y, _ = self.sim.pose()
        quat = self.sim.orientation()
        v, w = self.sim.twist()

        odom = Odometry()
        odom.header.stamp = stamp
        odom.header.frame_id = self.odom_frame
        odom.child_frame_id = self.base_frame
        odom.pose.pose.position.x, odom.pose.pose.position.y = x, y
        o = odom.pose.pose.orientation
        o.x, o.y, o.z, o.w = quat
        odom.twist.twist.linear.x = v
        odom.twist.twist.angular.z = w
        self.odom_pub.publish(odom)
        self.tf_pub.sendTransform(make_tf(stamp, self.odom_frame, self.base_frame, (x, y, 0.0), quat))

        js = JointState()
        js.header.stamp = stamp
        js.name = self.joint_names
        d = self.sim.data
        js.position = [float(d.qpos[q]) for q, _ in self.joint_adr]
        js.velocity = [float(d.qvel[v_]) for _, v_ in self.joint_adr]
        self.joint_pub.publish(js)

        if self.worker:
            m = self.sim.model
            wx, wy, wyaw = self.worker.position(d.time)
            pose = PoseStamped()
            pose.header.stamp = stamp
            pose.header.frame_id = self.world_frame
            pose.pose.position.x, pose.pose.position.y = wx, wy
            q = pose.pose.orientation
            q.z, q.w = math.sin(wyaw / 2), math.cos(wyaw / 2)
            self.worker_pub.publish(pose)
            if self.has_keep_out:
                self.worker_zone_pub.publish(Bool(data=self.scn.in_zone(m, "keep_out_zone", wx, wy)))

    # ---- 카메라 ----
    def _render(self):
        stamp = self.get_clock().now().to_msg()
        m, d = self.sim.model, self.sim.data
        sensor = self.sensors[self.next_camera]
        self.next_camera = (self.next_camera + 1) % len(self.sensors)
        pixels = self.rig.render(d, sensor)
        img = Image()
        img.header.stamp = stamp
        img.header.frame_id = f"camera_{sensor}"
        img.height, img.width = pixels.shape[:2]
        img.encoding = "rgb8"
        img.is_bigendian = 0
        img.step = img.width * 3
        # bytes를 그대로 넣으면 원소 단위 변환으로 장당 약 70 ms가 걸린다. array('B')는 복사만 한다.
        img.data = array.array("B", pixels.tobytes())
        info = self.info_msgs[sensor]
        info.header.stamp = stamp
        image_pub, info_pub = self.image_pubs[sensor]
        image_pub.publish(img)
        info_pub.publish(info)
        if self.scn.CAMERAS[sensor][2] == "base_link":
            # 로봇 카메라: 머리 관절에 따라 움직이므로 렌더할 때마다 base_link 기준 TF 갱신
            cam_pos, cam_rot = self.scn.camera_pose_world(d, m, self.scn.CAMERAS[sensor][0])
            base_pos, base_rot = d.xpos[self.base_body], d.xmat[self.base_body].reshape(3, 3)
            pos, rot = self.scn.relative_pose(base_pos, base_rot, cam_pos, cam_rot)
            self.tf_pub.sendTransform(make_tf(stamp, self.base_frame, f"camera_{sensor}",
                                              pos, self.scn.rot_to_quat(rot)))

    def _camera_info(self, sensor):
        fx, fy, cx, cy = self.scn.camera_intrinsics(self.sim.model, self.scn.CAMERAS[sensor][0],
                                                     self.image_width, self.image_height)
        info = CameraInfo()
        info.header.frame_id = f"camera_{sensor}"
        info.width, info.height = self.image_width, self.image_height
        info.distortion_model = "plumb_bob"
        info.d = [0.0] * 5
        info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        return info

    def _publish_static_tf(self):
        """world → odom(시작 위치 = 원점), world → 고정 CCTV 카메라."""
        import mujoco
        m, d = self.sim.model, self.sim.data
        mujoco.mj_forward(m, d)
        stamp = self.get_clock().now().to_msg()
        tfs = [make_tf(stamp, self.world_frame, self.odom_frame, (0, 0, 0), (0.0, 0.0, 0.0, 1.0))]
        for sensor in self.sensors:
            cam, _, mount = self.scn.CAMERAS[sensor]
            if mount == "world":
                pos, rot = self.scn.camera_pose_world(d, m, cam)
                tfs.append(make_tf(stamp, self.world_frame, f"camera_{sensor}", pos, self.scn.rot_to_quat(rot)))
        self.static_tf_pub.sendTransform(tfs)

    def close(self):
        if self.rig is not None:
            self.rig.close()
        if self.viewer is not None:
            self.viewer.close()


def main():
    rclpy.init()
    node = MujocoBridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException, SystemExit):
        pass
    except Exception:
        # 종료 신호(SIGTERM)로 컨텍스트가 닫히는 순간 발행 중이던 콜백의 오류는 무시
        if rclpy.ok():
            raise
    finally:
        node.close()
        node.destroy_node()
        rclpy.try_shutdown()
