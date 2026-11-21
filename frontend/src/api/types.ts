// backend/app/models.py(스키마 v1.1)와 같은 이름·값을 쓴다. 새 상태값을 여기서 만들지 않는다.

export type Vector3 = { x: number; y: number; z: number };
export type Pose = { frame_id: string; position: Vector3; orientation?: { x: number; y: number; z: number; w: number } };

export type RobotStatus = "IDLE" | "MOVING" | "HOLD" | "ESTOP" | "ERROR";
export type Robot = {
  robot_id: string;
  robot_type: "HUMANOID" | "QUADRUPED" | "MANIPULATOR" | "MOBILE";
  control_adapter: string;
  status: RobotStatus;
  pose: Pose;
  velocity: Vector3;
  motion_limits: { max_speed: number; max_acceleration: number };
  updated_at: string;
  last_heartbeat_at: string | null;
  battery_level: number | null;
};

export type ZoneType = "WORK" | "KEEP_OUT" | "SAFE" | "TRANSIT";
export type Zone = {
  zone_id: string;
  zone_type: ZoneType;
  geometry: Record<string, unknown>;
  frame_id: string;
  minimum_safety_distance: number;
  max_speed: number | null;
  estop_distance: number;
};

export type TaskStatus = "CREATED" | "PLANNING" | "RUNNING" | "HELD" | "COMPLETED" | "FAILED";
export type TaskDestination =
  | { type: "COORDINATE"; frame_id: string; position: Vector3 }
  | { type: "ZONE"; zone_id: string };
export type ActionType = "MOVE_TO" | "MANIPULATE" | "STOP" | "RESUME";

export type TaskOrder = {
  task_id: string;
  instruction: string;
  target_robot_id: string;
  target_object_id: string | null;
  destination: TaskDestination;
  status: TaskStatus;
  created_at: string;
};

export type TaskOrderPreview = Omit<TaskOrder, "task_id" | "status" | "created_at"> & { action_type: ActionType };

export type ProposalStatus = "PROPOSED" | "APPROVED" | "REJECTED" | "EXECUTING" | "SUCCEEDED" | "FAILED" | "HELD";
export type ActionProposal = {
  proposal_id: string;
  task_id: string;
  robot_id: string;
  action_type: ActionType;
  parameters: Record<string, unknown>;
  trajectory: Record<string, unknown>[] | null;
  frame_id: string;
  speed_limit: number;
  model_name: string;
  model_version: string;
  status: ProposalStatus;
  proposed_at: string;
};

export type HazardSeverity = "WARNING" | "CRITICAL";
export type HazardStatus = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
export type HazardEvent = {
  hazard_id: string;
  hazard_type: "WORKER_INTRUSION" | "COLLISION_RISK" | "FIRE" | "SMOKE" | "FALLING_OBJECT" | "UNKNOWN";
  severity: HazardSeverity;
  source_observation_ids: string[];
  related_robot_ids: string[];
  related_worker_ids: string[];
  zone_id: string;
  status: HazardStatus;
  confidence: number;
  detected_at: string;
  resolved_at: string | null;
  last_observed_at: string | null;
  resolved_by: "SENSOR_AUTO" | "OPERATOR" | null;
};

export type Worker = {
  worker_id: string;
  pose: Pose;
  current_zone_id: string;
  safety_status: string;
  last_seen_at: string;
  confidence: number;
  source_sensor_ids: string[];
};
export type WorkerSnapshot = { observed_at: string; workers: Worker[] };

export type RuleResult = { rule_id: string; passed: boolean; reason: string };
export type Decision = "APPROVE" | "REJECT" | "ESTOP" | "EXECUTE" | "COMPLETE" | "FAIL";
export type ExecutionLog = {
  log_id: string;
  proposal_id: string;
  decision: Decision;
  rule_results: RuleResult[];
  hazard_ids: string[];
  robot_id: string;
  timestamp: string;
  result_message: string;
  evaluated_in_ms: number | null;
  triggered_by: "RULE_ENGINE" | "OPERATOR" | "HEARTBEAT_LOSS" | null;
};
