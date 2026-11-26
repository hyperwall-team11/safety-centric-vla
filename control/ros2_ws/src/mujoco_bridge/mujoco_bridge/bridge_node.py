"""ROS 2 ↔ MuJoCo 브리지.

구독: /cmd_vel (Twist) → 바퀴 로봇
구독: /arm/command (String JSON) → TIAGo PICK·PLACE·EE_DELTA
발행: /arm/result (String JSON) → TIAGo 팔 실행 결과
발행: /odom, /joint_states, TF, 카메라 3대 영상(Image + CameraInfo), 작업자 실제 위치(/sim/worker_pose,
      추가 작업자는 cctv_renderer:=threejs일 때 /sim/worker_pose_<n>, n = 2, 3, ...)

    ros2 run mujoco_bridge bridge                                  # 시나리오 장면 (기본)
    ros2 run mujoco_bridge bridge --ros-args -p robot:=simple      # 단순 시험용 바퀴 로봇
    ros2 run mujoco_bridge bridge --ros-args -p worker_motion:=false -p camera_hz:=10.0
    mjpython <install>/lib/mujoco_bridge/bridge --ros-args -p viewer:=true   # 뷰어 (macOS)
    ros2 run mujoco_bridge bridge --ros-args -p arms:=true         # 공장 현장 + 로봇 팔 4대 (이슈 #95)
    ros2 run mujoco_bridge bridge --ros-args -p cctv_renderer:=threejs        # CCTV를 Three.js로 렌더 (#55)

arms:=true이면 plant_scene.xml에 팔 4대(arms.PLANT_ARMS)를 더한 장면을 쓰고(model_path 무시), 팔마다
/<seg>/arm/joint_command(JointState 위치 목표)·/<seg>/arm/gripper(Bool, 닫힘)를 구독해
/<seg>/joint_states를 발행한다. <seg>는 robot_id의 토픽용 이름(arm-ur5e → arm_ur5e). 팔 명령은 robot_adapter의 arm_adapter가 보낸다.

    ros2 run mujoco_bridge bridge --ros-args -p arms:=true -p amrs:=true   # + 이동형 운반 로봇(AMR) 2대

amrs:=true이면 AMR 2대(amr.PLANT_AMRS)와 팔레트·거치대를 더하고, AMR마다 /<seg>/cmd_vel(Twist)·
/<seg>/amr/load_command(String JSON, 싣기·내리기)를 구독해 /<seg>/odom·/<seg>/amr/load_result를 발행한다.
AMR 차체와 실린 팔레트는 운동학으로 움직인다(물리 접촉 없음). 명령은 robot_adapter의 amr_adapter가 보낸다.

cctv_renderer:=threejs이면 고정 CCTV(cctv_1, cctv_2)는 MuJoCo 렌더 대신 Three.js 렌더 영상을 발행한다.
브리지는 camera_hz로 MuJoCo 상태를 프레임으로 묶어 로컬 HTTP(threejs_port)로 내주고, 렌더러
(`node render.mjs --ros`)가 렌더한 RGB를 돌려받아 그 프레임의 시뮬레이션 시각으로 발행한다(threejs_live.py).

물리와 렌더는 타이머를 나눈다. 물리 타이머는 시뮬 시각이 실제 시각을 따라가도록 필요한 만큼 스텝을 돌리고
(기본 timestep 0.002 s = 500Hz), 카메라 타이머는 camera_hz로 렌더한다. 모든 콜백은 rclpy.spin을 돌리는
한 스레드에서 실행되므로 렌더러(OpenGL 컨텍스트)도 그 스레드에서 만들고 쓴다.

시뮬레이션 모듈은 control/mujoco/의 simulation.py·scenario.py를 사용한다. 위치는 mujoco_dir 파라미터로
지정하며, pixi 환경에서는 기본값($PIXI_PROJECT_ROOT/mujoco)을 그대로 쓰면 된다.
"""
import array
import json
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
from std_msgs.msg import Bool, String
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

ANY_NUMBER = ParameterDescriptor(dynamic_typing=True)  # 15와 15.0을 모두 받는다
SLOW_CALLBACK_S = 0.2  # 이보다 오래 걸린 물리 콜백은 경고
DUPLICATE_CHECK_S = 5.0  # 카메라 토픽에 다른 발행자가 있는지 확인하는 주기
MAX_CATCHUP_S = 0.1  # 한 번의 물리 타이머에서 따라잡을 최대 시뮬 시간 (밀리면 실시간보다 느려짐)


def load_modules(mujoco_dir: str):
    if not os.path.isfile(os.path.join(mujoco_dir, "simulation.py")):
        raise FileNotFoundError(f"simulation.py 없음: {mujoco_dir} (mujoco_dir 파라미터 확인)")
    sys.path.insert(0, mujoco_dir)
    import scenario
    import simulation
    return simulation, scenario


def load_arms(mujoco_dir: str):
    sys.path.insert(0, mujoco_dir)
    import arms
    return arms


def load_amr(mujoco_dir: str):
    sys.path.insert(0, mujoco_dir)
    import amr
    return amr


def load_tiago_control(mujoco_dir: str):
    sys.path.insert(0, mujoco_dir)
    import tiago_command
    import tiago_manipulation
    return tiago_command, tiago_manipulation


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
        use_arms = self.declare_parameter("arms", False).value
        use_amrs = self.declare_parameter("amrs", False).value
        cctv_renderer = self.declare_parameter("cctv_renderer", "mujoco").value
        if cctv_renderer not in ("mujoco", "threejs"):
            raise ValueError(f"cctv_renderer must be mujoco or threejs, got {cctv_renderer!r}")
        root = os.environ.get("PIXI_PROJECT_ROOT", "")
        threejs_dir = self.declare_parameter("threejs_dir", os.path.join(root, "scripts", "threejs_cctv")).value
        threejs_data = self.declare_parameter("threejs_data", os.path.join(root, "outputs", "threejs_live", "data")).value
        threejs_port = int(self.declare_parameter("threejs_port", 8765, ANY_NUMBER).value)

        self.plant_arms = None
        self.plant_amrs = None
        warehouse_demo = self.declare_parameter("warehouse_demo", False).value
        self.crossing_amr_id = None
        self.warehouse_vehicles = None
        if warehouse_demo:
            amr = load_amr(mujoco_dir)
            import warehouse
            from warehouse_vehicles import WarehouseVehicles

            model, data = warehouse.build_warehouse()
            self.sim = simulation.MujocoSim(model, data)
            self.plant_amrs = amr.PlantAmrs(
                model, data, warehouse.WAREHOUSE_AMRS, self.cmd_timeout, preloaded=True,
            )
            self.crossing_amr_id = warehouse.AMR.robot_id
            autoplay = self.declare_parameter("warehouse_vehicles_autoplay", True).value
            self.warehouse_vehicles = WarehouseVehicles(model, data, autoplay=autoplay)
            model_path = "warehouse_demo"
        elif use_arms or use_amrs:
            arms = load_arms(mujoco_dir)
            amr = load_amr(mujoco_dir) if use_amrs else None
            model, data, kins = arms.build_plant_with_arms(arms.PLANT_ARMS if use_arms else [],
                                                           extras=(amr.add_plant_amrs,) if amr else ())
            self.sim = simulation.DiffDriveSim(robot, model=model, data=data)
            if use_arms:
                self.plant_arms = arms.PlantArms(model, data, kins)
            if amr:
                self.plant_amrs = amr.PlantAmrs(model, data, cmd_timeout=self.cmd_timeout)
            model_path = arms.PLANT_SCENE.name + (" + arms" if use_arms else "") + (" + amrs" if amr else "")
        else:
            self.sim = simulation.DiffDriveSim(robot, model_path)
        m = self.sim.model
        self.tiago_manipulator = None
        self.tiago_command_runner = None
        joint_names = {m.joint(i).name for i in range(m.njnt)}
        if not warehouse_demo and set(f"arm_{i}_joint" for i in range(1, 8)) <= joint_names:
            tiago_command, tiago_manipulation = load_tiago_control(mujoco_dir)
            self.tiago_manipulation_module = tiago_manipulation
            self.tiago_manipulator = tiago_manipulation.TiagoManipulator(
                self.sim,
                log=lambda message: self.get_logger().info(message),
            )
            self.tiago_command_runner = tiago_command.TiagoCommandRunner(
                self._make_tiago_arm_task,
                self.tiago_manipulator.hold,
            )
        self.last_cmd_at = None
        self.wall_start = time.monotonic()
        self.sim_start = self.sim.time
        self.last_rtf_log = self.wall_start

        names = {m.joint(i).name for i in range(m.njnt)}
        self.joint_names = [n for n in self.scn.JOINT_STATE_NAMES if n in names]
        self.joint_adr = [(m.joint(n).qposadr[0], m.joint(n).dofadr[0]) for n in self.joint_names]
        if not warehouse_demo:
            self.base_body = m.joint(self.sim.spec.base_joint).bodyid[0]

        self.worker = None
        bodies = {m.body(i).name for i in range(m.nbody)}
        if use_worker and "worker" in bodies and m.body_mocapid[m.body("worker").id] >= 0:
            self.worker = warehouse.CrossingWorker() if warehouse_demo else self.scn.WorkerScript()
            self.has_keep_out = "keep_out_zone" in {m.geom(i).name for i in range(m.ngeom)}
            self.worker_pub = self.create_publisher(PoseStamped, "/sim/worker_pose", 10)
            self.worker_zone_pub = self.create_publisher(Bool, "/sim/worker_in_keep_out", 10)
            self.worker.apply(m, self.sim.data)

        cams = {m.camera(i).name for i in range(m.ncam)}
        self.sensors = [s for s, (cam, _, _) in self.scn.CAMERAS.items() if cam in cams] if use_cameras else []
        if warehouse_demo:
            self.sensors = [s for s in self.sensors if s != "head_camera"]
            if cctv_renderer != "threejs":
                raise ValueError("warehouse_demo requires cctv_renderer:=threejs")
        # Three.js 모드: 고정 CCTV는 Three.js가, 로봇 카메라는 그대로 MuJoCo가 렌더한다
        self.threejs_sensors = ([s for s in self.sensors if self.scn.CAMERAS[s][2] == "world" or s == "amr_camera"]
                                if cctv_renderer == "threejs" else [])
        self.rig_sensors = [s for s in self.sensors if s not in self.threejs_sensors]
        self.rig = None
        self.live = self.live_server = None
        self.image_pubs, self.info_msgs, self.render_counts = {}, {}, {}
        if self.sensors:
            for sensor in self.sensors:
                prefix = self.scn.CAMERAS[sensor][1]
                self.image_pubs[sensor] = (
                    self.create_publisher(Image, f"{prefix}/image_raw", qos_profile_sensor_data),
                    self.create_publisher(CameraInfo, f"{prefix}/camera_info", qos_profile_sensor_data))
                self.info_msgs[sensor] = self._camera_info(sensor)
        if self.rig_sensors:
            # 렌더러는 이 스레드(= spin 스레드)에서 생성
            self.rig = self.scn.CameraRig(m, self.image_width, self.image_height, self.rig_sensors)
            # 카메라를 한 틱에 한 대씩 돌아가며 렌더 → 한 번에 막히는 시간이 짧아 상태 토픽 주기가 덜 흔들린다
            self.next_camera = 0
            self.create_timer(1.0 / (self.camera_hz * len(self.rig_sensors)), self._render)
        if self.threejs_sensors:
            self._start_threejs(threejs_dir, threejs_data, threejs_port)
        if self.sensors:
            # 다른 브리지가 같은 도메인에서 같은 카메라 토픽을 발행하면 CV 입력에 두 영상이 섞인다
            self.duplicate_topics = set()
            self.create_timer(DUPLICATE_CHECK_S, self._check_duplicate_publishers)
        elif cctv_renderer == "threejs":
            raise RuntimeError("cctv_renderer:=threejs needs cameras:=true and fixed CCTV cameras in the scene")

        self.viewer = None
        if use_viewer:
            import mujoco.viewer
            self.viewer = mujoco.viewer.launch_passive(m, self.sim.data)
            # 물리 타이머마다 그리면 카메라 렌더가 밀리므로 화면 갱신은 30Hz로 따로
            self.create_timer(1.0 / 30.0, self._sync_viewer)

        self.arm_joint_pubs, self.grasp_pubs = {}, {}
        if self.plant_arms:
            from safety_control.interfaces import topic_segment
            for rid in self.plant_arms.kins:
                seg = topic_segment(rid)  # arm-ur5e → arm_ur5e (ROS 토픽 이름 규칙)
                self.create_subscription(JointState, f"/{seg}/arm/joint_command",
                                         lambda msg, r=rid: self._on_arm_command(r, msg), 10)
                self.create_subscription(Bool, f"/{seg}/arm/gripper",
                                         lambda msg, r=rid: self._on_gripper(r, msg), 10)
                self.grasp_pubs[rid] = self.create_publisher(String, f"/{seg}/arm/grasp_state", 10)
                self.arm_joint_pubs[rid] = self.create_publisher(JointState, f"/{seg}/joint_states", 10)

        self.amr_odom_pubs, self.amr_result_pubs = {}, {}
        if self.plant_amrs:
            from safety_control.interfaces import topic_segment
            for rid in self.plant_amrs.mocap:
                seg = topic_segment(rid)  # amr-01 → amr_01
                self.create_subscription(Twist, f"/{seg}/cmd_vel",
                                         lambda msg, r=rid: self.plant_amrs.set_velocity(r, msg.linear.x, msg.angular.z), 10)
                self.create_subscription(String, f"/{seg}/amr/load_command",
                                         lambda msg, r=rid: self._on_amr_load(r, msg), 10)
                self.amr_odom_pubs[rid] = self.create_publisher(Odometry, f"/{seg}/odom", 10)
                self.amr_result_pubs[rid] = self.create_publisher(String, f"/{seg}/amr/load_result", 10)

        if warehouse_demo:
            self._connect_warehouse_vehicles()
        else:
            self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 10)
            self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
            self.joint_pub = self.create_publisher(JointState, "/joint_states", 10)
            if self.tiago_command_runner is not None:
                self.arm_result_pub = self.create_publisher(String, "/arm/result", 10)
                self.create_subscription(String, "/arm/command", self._on_tiago_arm_command, 10)
        self.tf_pub = TransformBroadcaster(self)
        self.static_tf_pub = StaticTransformBroadcaster(self)
        self._publish_static_tf()
        self.create_timer(1.0 / physics_hz, self._physics)
        self.create_timer(1.0 / state_hz, self._publish_state)

        # 실시간 기준점은 초기화(렌더러·Three.js 장면 준비)가 끝난 뒤로 잡는다. 아니면 시작하자마자 시뮬 시각이 한꺼번에 뛴다
        self.wall_start = self.last_rtf_log = time.monotonic()
        self.sim_start = self.sim.time
        max_linear = amr.MAX_SPEED if warehouse_demo else self.sim.max_wheel_speed * self.sim.wheel_radius
        self.get_logger().info(
            f"MuJoCo bridge ready: {robot} ({model_path or self.sim.spec.model_path.name}), "
            f"max linear {max_linear:.2f} m/s, joints {len(self.joint_names)}, "
            f"cameras {self.sensors or 'off'} @ {self.camera_hz} Hz {self.image_width}x{self.image_height}, "
            f"worker motion {'on' if self.worker else 'off'}, "
            f"arms {sorted(self.plant_arms.kins) if self.plant_arms else 'off'}, "
            f"amrs {sorted(self.plant_amrs.mocap) if self.plant_amrs else 'off'}")

    # ---- 입력 ----
    def _on_cmd_vel(self, msg: Twist):
        self.sim.set_velocity(msg.linear.x, msg.angular.z)
        self.last_cmd_at = time.monotonic()

    def _on_arm_command(self, robot_id, msg: JointState):
        try:
            self.plant_arms.set_target(self.sim.data, robot_id, list(msg.position))
        except ValueError as exc:
            self.get_logger().warning(str(exc))

    def _on_gripper(self, robot_id, msg: Bool):
        held = self.plant_arms.set_gripper(self.sim.data, robot_id, bool(msg.data))
        # 실제로 잡았는지 어댑터에 알린다. 어댑터는 이 결과를 받기 전에는 파지 성공으로 보지 않는다
        name = self.plant_arms.box_name(held) if held is not None else None
        self.grasp_pubs[robot_id].publish(String(data=json.dumps(
            {"closed": bool(msg.data), "held": name}, ensure_ascii=False)))

    @staticmethod
    def _command_position(target_pose):
        position = (target_pose or {}).get("position")
        if isinstance(position, dict):
            position = [position.get(axis) for axis in ("x", "y", "z")]
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            return None
        try:
            values = [float(value) for value in position]
        except (TypeError, ValueError, OverflowError):
            return None
        return values if all(math.isfinite(value) for value in values) else None

    def _tiago_object_position(self, object_id):
        candidates = [object_id, str(object_id).replace("-", "_")]
        if object_id == "box-01":
            candidates.append("part_box")
        for name in candidates:
            try:
                body_id = self.sim.model.body(name).id
            except KeyError:
                continue
            return self.sim.data.xpos[body_id].copy()
        raise ValueError(f"물체 {object_id!r}를 MuJoCo 장면에서 찾을 수 없음")

    def _make_tiago_arm_task(self, command):
        ee_delta = command.get("ee_delta")
        if ee_delta is not None:
            if command.get("frame_id") != self.world_frame:
                raise ValueError(f"EE_DELTA frame_id {command.get('frame_id')!r} != {self.world_frame!r}")
            translation = ee_delta.get("translation") or {}
            rotation = ee_delta.get("rotation_rpy") or {}
            return self.tiago_manipulator.ee_delta(
                [translation.get(axis) for axis in ("x", "y", "z")],
                [rotation.get(axis) for axis in ("x", "y", "z")],
                ee_delta.get("gripper"),
            )

        kind = command.get("manipulation")
        if kind not in ("PICK", "PLACE"):
            raise ValueError(f"지원하지 않는 manipulation: {kind!r}")
        target = self._command_position(command.get("target_pose"))
        if kind == "PICK" and target is None:
            target = self._tiago_object_position(command.get("object_id"))
        if target is None:
            raise ValueError(f"{kind}에는 target_pose.position이 필요함")
        approach_yaw = command.get("approach_yaw")
        if approach_yaw is None:
            approach_yaw = self.tiago_manipulation_module.choose_approach_yaw(
                self.sim.model,
                target[:2],
                self.sim.pose()[:2],
            )
        approach_yaw = float(approach_yaw)
        if not math.isfinite(approach_yaw):
            raise ValueError("approach_yaw는 유한한 숫자여야 함")
        return (
            self.tiago_manipulator.pick(target, approach_yaw)
            if kind == "PICK"
            else self.tiago_manipulator.place(target, approach_yaw)
        )

    def _publish_tiago_arm_results(self, results):
        for result in results:
            self.arm_result_pub.publish(String(data=json.dumps(result, ensure_ascii=False)))

    def _on_tiago_arm_command(self, msg: String):
        try:
            command = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warning("JSON이 아닌 TIAGo 팔 명령 무시")
            return
        if not isinstance(command, dict):
            self.get_logger().warning("객체가 아닌 TIAGo 팔 명령 무시")
            return
        self._publish_tiago_arm_results(self.tiago_command_runner.submit(command))

    def _on_amr_load(self, robot_id, msg: String):
        try:
            cmd = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warning("JSON이 아닌 AMR 싣기·내리기 명령 무시")
            return
        self.plant_amrs.load(self.sim.data, robot_id, cmd)
        self._publish_amr_results()

    def _publish_amr_results(self):
        for rid, result in self.plant_amrs.pop_results():
            self.amr_result_pubs[rid].publish(String(data=json.dumps(result, ensure_ascii=False)))

    def _connect_warehouse_vehicles(self):
        from safety_control.interfaces import ESTOP_QOS, ESTOP_TOPIC
        from std_msgs.msg import Float64
        from std_srvs.srv import SetBool

        self.vehicle_odometry = {}
        for vehicle in self.warehouse_vehicles.running:
            prefix = f"/warehouse/{vehicle}"
            self.vehicle_odometry[vehicle] = self.create_publisher(Odometry, f"{prefix}/odom", 10)
            self.create_service(SetBool, f"{prefix}/set_running",
                                lambda req, resp, v=vehicle: self._set_warehouse_running(v, req, resp))
        self.fork_height_pub = self.create_publisher(Float64, "/warehouse/forklift/fork_height", 10)
        self.create_subscription(Float64, "/warehouse/forklift/fork_height_command", self._on_fork_height, 10)
        self.create_subscription(String, ESTOP_TOPIC, self._on_warehouse_estop, ESTOP_QOS)

    def _set_warehouse_running(self, vehicle, request, response):
        response.success = self.warehouse_vehicles.set_running(vehicle, request.data)
        response.message = ("운행" if request.data else "정지") if response.success else "E-Stop 활성 상태"
        return response

    def _on_fork_height(self, msg):
        if not self.warehouse_vehicles.set_fork_height(msg.data):
            self.get_logger().warn("정지 상태의 포크에 0.03~0.28 m 명령만 허용합니다.",
                                   throttle_duration_sec=2.0)

    def _on_warehouse_estop(self, msg):
        try:
            command = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if isinstance(command, dict) and isinstance(command.get("active"), bool):
            self.warehouse_vehicles.set_estop(command["active"])

    def _publish_warehouse_vehicles(self, stamp):
        from std_msgs.msg import Float64

        for vehicle, pub in self.vehicle_odometry.items():
            x, y, yaw, speed, angular = self.warehouse_vehicles.odometry[vehicle]
            odom = Odometry()
            odom.header.stamp = stamp
            odom.header.frame_id = self.world_frame
            odom.child_frame_id = f"warehouse/{vehicle}/base_link"
            odom.pose.pose.position.x, odom.pose.pose.position.y = x, y
            odom.pose.pose.orientation.z, odom.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
            odom.twist.twist.linear.x = speed
            odom.twist.twist.angular.z = angular
            pub.publish(odom)
        self.fork_height_pub.publish(Float64(data=self.warehouse_vehicles.fork_height))

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
                if self.crossing_amr_id is not None:
                    x, _, _, speed, _ = self.plant_amrs.odom(self.crossing_amr_id)
                    self.worker.observe_amr(x, speed, self.sim.time)
                self.worker.apply(self.sim.model, self.sim.data)
            before = self.sim.time
            step_seconds = min(lag, MAX_CATCHUP_S)
            if self.tiago_command_runner is not None:
                self._publish_tiago_arm_results(self.tiago_command_runner.tick(step_seconds))
            self.sim.step(step_seconds)
            if self.warehouse_vehicles:
                self.warehouse_vehicles.update(self.sim.data)
            if self.plant_arms:
                self.plant_arms.after_step(self.sim.data)
            if self.plant_amrs:
                self.plant_amrs.step(self.sim.data, self.sim.time - before)
                self._publish_amr_results()
        spent = time.monotonic() - now
        if spent > SLOW_CALLBACK_S:
            # 콜백이 길면 그동안 /odom 등 다른 발행이 멈춘다 (어댑터는 /odom 0.5초 끊김을 HW_ERROR로 본다)
            self.get_logger().warning(
                f"물리 콜백 {spent * 1000:.0f} ms (lag {lag * 1000:.0f} ms)", throttle_duration_sec=5.0
            )
        if now - self.last_rtf_log >= 10.0:
            rtf = (self.sim.time - self.sim_start) / (now - self.wall_start)
            self.get_logger().info(f"sim {self.sim.time:.1f} s / wall {now - self.wall_start:.1f} s "
                                   f"(실시간 대비 {rtf:.2f}배)")
            self.last_rtf_log = now

    def destroy_node(self):
        if self.tiago_manipulator is not None:
            self.tiago_manipulator.close()
        return super().destroy_node()

    def _sync_viewer(self):
        if not self.viewer.is_running():
            raise SystemExit
        self.viewer.sync()

    # ---- 상태 발행 ----
    def _publish_base_state(self, stamp, d):
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
        js.position = [float(d.qpos[q]) for q, _ in self.joint_adr]
        js.velocity = [float(d.qvel[v_]) for _, v_ in self.joint_adr]
        # 액추에이터가 낸 힘(직선 관절, N) 또는 토크(회전 관절, N·m). 액추에이터가 없는 관절은 0 (#96)
        js.effort = [float(d.qfrc_actuator[v_]) for _, v_ in self.joint_adr]
        self.joint_pub.publish(js)

    def _publish_state(self):
        stamp = self.get_clock().now().to_msg()
        d = self.sim.data
        if self.warehouse_vehicles:
            self._publish_warehouse_vehicles(stamp)
        else:
            self._publish_base_state(stamp, d)

        for rid, pub in self.arm_joint_pubs.items():
            q, dq, tau = self.plant_arms.joint_state(d, rid)
            kin = self.plant_arms.kins[rid]
            arm_js = JointState()
            arm_js.header.stamp = stamp
            arm_js.name = kin.joint_names
            arm_js.position = [float(v) for v in q]
            arm_js.velocity = [float(v) for v in dq]
            arm_js.effort = [float(v) for v in tau]
            pub.publish(arm_js)

        for rid, pub in self.amr_odom_pubs.items():
            ax, ay, ayaw, av, aw = self.plant_amrs.odom(rid)
            amr_odom = Odometry()
            amr_odom.header.stamp = stamp
            amr_odom.header.frame_id = self.world_frame
            amr_odom.child_frame_id = f"{rid}/base_link"
            amr_odom.pose.pose.position.x, amr_odom.pose.pose.position.y = ax, ay
            ao = amr_odom.pose.pose.orientation
            ao.z, ao.w = math.sin(ayaw / 2), math.cos(ayaw / 2)
            amr_odom.twist.twist.linear.x, amr_odom.twist.twist.angular.z = av, aw
            pub.publish(amr_odom)

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
        sensor = self.rig_sensors[self.next_camera]
        self.next_camera = (self.next_camera + 1) % len(self.rig_sensors)
        pixels = self.rig.render(d, sensor)
        self._publish_image(sensor, stamp, pixels.shape[1], pixels.shape[0], pixels.tobytes())
        if self.scn.CAMERAS[sensor][2] == "base_link":
            # 로봇 카메라: 머리 관절에 따라 움직이므로 렌더할 때마다 base_link 기준 TF 갱신
            cam_pos, cam_rot = self.scn.camera_pose_world(d, m, self.scn.CAMERAS[sensor][0])
            base_pos, base_rot = d.xpos[self.base_body], d.xmat[self.base_body].reshape(3, 3)
            pos, rot = self.scn.relative_pose(base_pos, base_rot, cam_pos, cam_rot)
            self.tf_pub.sendTransform(make_tf(stamp, self.base_frame, f"camera_{sensor}",
                                              pos, self.scn.rot_to_quat(rot)))

    def _publish_image(self, sensor, stamp, width, height, rgb: bytes):
        img = Image()
        img.header.stamp = stamp
        img.header.frame_id = f"camera_{sensor}"
        img.height, img.width = height, width
        img.encoding = "rgb8"
        img.is_bigendian = 0
        img.step = width * 3
        # bytes를 그대로 넣으면 원소 단위 변환으로 장당 약 70 ms가 걸린다. array('B')는 복사만 한다.
        img.data = array.array("B", rgb)
        info = self.info_msgs[sensor]
        info.header.stamp = stamp
        image_pub, info_pub = self.image_pubs[sensor]
        image_pub.publish(img)
        info_pub.publish(info)

    def _check_duplicate_publishers(self):
        for sensor in self.sensors:
            topic = f"{self.scn.CAMERAS[sensor][1]}/image_raw"
            count = self.count_publishers(topic)
            if count > 1 and topic not in self.duplicate_topics:
                self.get_logger().error(
                    f"{topic}에 이 브리지 말고도 발행자가 {count - 1}개 더 있습니다. 영상이 섞이므로 다른 브리지를 끄거나 "
                    f"ROS_DOMAIN_ID를 나누세요")
                self.duplicate_topics.add(topic)
            elif count <= 1 and topic in self.duplicate_topics:
                self.get_logger().info(f"{topic}: 다른 발행자가 없어졌습니다")
                self.duplicate_topics.discard(topic)

    # ---- Three.js CCTV ----
    def _start_threejs(self, threejs_dir, data_dir, port):
        from pathlib import Path

        from builtin_interfaces.msg import Time

        from mujoco_bridge.threejs_live import LiveFrames, stamp_parts, start_server

        if not os.path.isfile(os.path.join(threejs_dir, "export_scene.py")):
            raise FileNotFoundError(f"export_scene.py 없음: {threejs_dir} (threejs_dir 파라미터 확인)")
        sys.path.insert(0, threejs_dir)
        import export_scene

        m, d = self.sim.model, self.sim.data
        if "worker_head" not in {m.body(i).name for i in range(m.nbody)}:
            raise RuntimeError("cctv_renderer:=threejs needs a scene with the worker model")
        # 렌더러가 읽을 정적 장면(scene.json·텍스처). 작업자 1은 브리지의 작업자 스크립트 위치 그대로 둔다
        workers_cfg = export_scene.WORKERS
        worker_scripts = None
        if self.crossing_amr_id is not None:
            import warehouse

            workers_cfg = warehouse.DEMO_WORKERS
            i = warehouse.FALL_WORKER_INDEX
            worker_scripts = {i: warehouse.FallenWorker(waypoints=workers_cfg[i]["waypoints"],
                                                         speed=workers_cfg[i]["speed"])}
        meta, self.packer = export_scene.prepare(
            m, d, Path(data_dir), self.worker or False, workers_cfg=workers_cfg, worker_scripts=worker_scripts,
            live_moving_cameras=[s for s in self.threejs_sensors if self.scn.CAMERAS[s][2] != "world"],
        )
        meta.update({"frames": 1, "fps": self.camera_hz, "live": True})
        if self.crossing_amr_id is not None:
            meta["scene"] = "warehouse_demo"
        (Path(data_dir) / "scene.json").write_text(__import__("json").dumps(meta))

        def publish(sensor, sim_time, rgb):
            sec, nanosec = stamp_parts(sim_time)
            self._publish_image(sensor, Time(sec=sec, nanosec=nanosec), self.image_width, self.image_height, rgb)

        self.live = LiveFrames(self.threejs_sensors, self.image_width, self.image_height, publish)
        self.live_server = start_server(self.live, port=port)
        self.create_timer(1.0 / self.camera_hz, self._pack_threejs_frame)
        self.get_logger().info(f"Three.js CCTV {self.threejs_sensors}: 장면 {data_dir}, 프레임 http://127.0.0.1:{port}/frame")

    def _pack_threejs_frame(self):
        frame, states = self.packer.pack(self.sim.model, self.sim.data, self.sim.time)
        self.live.set_frame(frame, self.sim.time)
        # 추가 작업자(worker_2 이후 mocap) 실제 위치. 작업자 1의 /sim/worker_pose와 같은 형식
        if not hasattr(self, "extra_worker_pubs"):
            self.extra_worker_pubs = {i: self.create_publisher(PoseStamped, f"/sim/worker_pose_{i + 1}", 10)
                                      for i, _ in self.packer.extra}
        stamp = self.get_clock().now().to_msg()
        for (i, _), (x, y, yaw) in zip(self.packer.extra, states):
            pose = PoseStamped()
            pose.header.stamp = stamp
            pose.header.frame_id = self.world_frame
            pose.pose.position.x, pose.pose.position.y = float(x), float(y)
            q = pose.pose.orientation
            q.z, q.w = math.sin(yaw / 2), math.cos(yaw / 2)
            self.extra_worker_pubs[i].publish(pose)

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
        if self.live_server is not None:
            self.live_server.shutdown()
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
