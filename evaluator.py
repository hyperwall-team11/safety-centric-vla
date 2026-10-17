"""Versioned policy over RDF relations and planar geometric predicates.

This is an MVP evaluator, not a hardware safety controller. It does not send commands.
"""
from datetime import datetime, timezone
from hashlib import sha256
from math import dist, isfinite
from time import perf_counter
import json
from uuid import uuid4
from shapely.geometry import Point, LineString, shape
from shapely.ops import unary_union
from .contracts import CheckResult, EvaluationResponse
from .base_models import ExecutionLog, RuleResult
from .graph import build_graph, related_critical_hazards, covering_sensors, node, S
from rdflib import RDF

POLICY_VERSION = 'mvp-planar-1.1.0'
MAX_AGE = 2.0
FUTURE_TOLERANCE = 0.25


def fresh(t, now, limit=MAX_AGE):
    return t is not None and -FUTURE_TOLERANCE <= (now - t).total_seconds() <= limit


def xyz(v):
    return (v.x, v.y, v.z)


def geometry_context(req, robot):
    """Only explicit world-frame, timed, piecewise-linear planar mobile paths supported."""
    p = req.proposal
    if robot is None or robot.robot_type != 'MOBILE' or p.action_type != 'MOVE_TO':
        raise ValueError('지원 범위는 MOBILE의 MOVE_TO 평면 이동입니다. STOP/RESUME는 별도 제어 정책이 필요합니다.')
    if p.frame_id != 'world' or robot.pose.frame_id != 'world':
        raise ValueError('로봇과 궤적은 world 좌표계여야 합니다.')
    if req.context.robot_radius_m is None:
        raise ValueError('로봇 외곽 반경 robot_radius_m이 필요합니다.')
    if not req.context.zones_complete or not req.zones:
        raise ValueError('완전한 구역 목록이 필요합니다.')
    polys = {}
    for z in req.zones:
        if z.frame_id != 'world': raise ValueError('모든 구역은 world 좌표계여야 합니다.')
        if z.geometry.get('type') != 'Polygon': raise ValueError('구역은 GeoJSON Polygon만 지원합니다.')
        coords = z.geometry.get('coordinates')
        if not coords or any(len(pt) != 2 or not all(isinstance(a,(int,float)) and isfinite(a) for a in pt)
                             for ring in coords for pt in ring):
            raise ValueError('Polygon에는 유한한 2D 좌표가 필요합니다.')
        poly = shape(z.geometry)
        if poly.is_empty or not poly.is_valid or poly.area <= 0:
            raise ValueError(f'구역 {z.zone_id}의 geometry가 유효하지 않습니다.')
        if z.estop_distance > z.minimum_safety_distance:
            raise ValueError('estop_distance는 minimum_safety_distance 이하여야 합니다.')
        polys[z.zone_id] = poly
    if not p.trajectory or len(p.trajectory) < 2:
        raise ValueError('시작점과 끝점을 포함하는 timed trajectory가 필요합니다.')
    points, times = [], []
    for item in p.trajectory:
        pos = item.get('position', {})
        point = tuple(pos[k] for k in ('x','y','z'))
        t = item['time_from_start_s']
        if not all(isinstance(x,(int,float)) and not isinstance(x,bool) and isfinite(x) for x in (*point,t)):
            raise ValueError('궤적 좌표와 시간은 유한한 수여야 합니다.')
        if abs(point[2]) > 1e-6: raise ValueError('이 버전은 z=0 평면 궤적만 지원합니다.')
        points.append(point); times.append(t)
    if times[0] != 0 or any(b <= a for a,b in zip(times,times[1:])):
        raise ValueError('궤적 시간은 0에서 시작하고 엄격히 증가해야 합니다.')
    if dist(points[0], xyz(robot.pose.position)) > 1e-6:
        raise ValueError('궤적 시작점과 현재 로봇 위치가 다릅니다.')
    target = p.parameters['target_pose']
    if target['type'] == 'ZONE':
        zid = target['zone_id']
        if zid not in polys or not polys[zid].covers(Point(points[-1][:2])):
            raise ValueError('궤적 끝점이 목표 구역 안에 있어야 합니다.')
    else:
        target_xyz = tuple(target['position'][k] for k in ('x','y','z'))
        if target['frame_id'] != 'world' or dist(points[-1], target_xyz) > 1e-6:
            raise ValueError('궤적 끝점과 world 목표 좌표가 일치해야 합니다.')
    path = LineString([p[:2] for p in points])
    radius = req.context.robot_radius_m
    affected = sorted(zid for zid,poly in polys.items() if path.distance(poly) <= radius)
    # Entire centerline plus clearance margin must lie in declared world geometry.
    if not unary_union(list(polys.values())).covers(path.buffer(radius + 1e-6)):
        raise ValueError('로봇 이동 영역이 정의된 구역 범위를 벗어납니다.')
    speeds = [dist(a,b)/(tb-ta) for a,b,ta,tb in zip(points,points[1:],times,times[1:])]
    return path, polys, affected, speeds


def evaluate(req, *, now=None, return_graph=False):
    start = perf_counter()
    now = now or datetime.now(timezone.utc)
    checks = []
    def add(rule, status, reason, evidence=()):
        checks.append(CheckResult(rule_id=rule, status=status, reason=reason, evidence_ids=list(evidence)))
    p = req.proposal
    robots = {r.robot_id:r for r in req.robots}
    tasks = {t.task_id:t for t in req.tasks}
    robot, task = robots.get(p.robot_id), tasks.get(p.task_id)
    current = fresh(req.context.snapshot_at, now)
    # Proposal age is not observation age. Old proposals may be re-evaluated on fresh state.
    proposal_time_valid = (p.proposed_at - now).total_seconds() <= FUTURE_TOLERANCE
    add('PROPOSAL_TIMESTAMP', 'PASS' if proposal_time_valid else 'UNKNOWN',
        '제안 생성 시각 유효 (연령만으로 만료하지 않음)' if proposal_time_valid else '제안 생성 시각이 미래입니다.')
    if robot: current = current and fresh(robot.updated_at, now)
    add('DATA_FRESHNESS', 'PASS' if current else 'UNKNOWN',
        '스냅샷·로봇 상태의 시각 유효' if current else '관측 스냅샷·로봇 정보가 오래되었거나 미래 시각입니다.')
    assigned = robot is not None and task is not None and task.target_robot_id == p.robot_id
    valid_task = assigned and task.status in ('CREATED','PLANNING','RUNNING') and p.status in ('PROPOSED','APPROVED')
    add('R01', 'PASS' if valid_task else 'FAIL',
        '작업·로봇 연결 및 심사 가능 상태 확인' if valid_task else '작업·로봇 불일치, 미등록 또는 심사 불가 상태')
    ready = robot and (robot.status == 'IDLE' or (robot.status == 'HOLD' and req.context.hold_replan_allowed))
    add('R02', 'PASS' if ready else 'FAIL', '로봇 상태 실행 계획 가능' if ready else '로봇 상태가 실행 계획에 부적합')
    path = polys = speeds = None
    affected = []
    try:
        path, polys, affected, speeds = geometry_context(req, robot)
        geometry_reason = ''
    except (ValueError, KeyError, TypeError, IndexError) as e:
        geometry_reason = str(e)
        # Preserve known target-zone evidence even when the path cannot be assessed.
        target = p.parameters.get('target_pose', {})
        if target.get('type') == 'ZONE': affected = [target['zone_id']]
    add('SUPPORTED_ACTION', 'PASS' if path is not None else 'UNKNOWN',
        '평면 이동 경로 해석 완료' if path is not None else geometry_reason)
    g = build_graph(req, affected)
    zone_map = {z.zone_id:z for z in req.zones}
    if path is None:
        if robot and p.speed_limit > robot.motion_limits.max_speed:
            add('R03','FAIL','제안 속도가 로봇 최대 속도를 초과합니다.')
        else:
            add('R03','UNKNOWN','전체 속도 검사에는 경로·구역 정보가 필요합니다.')
        for rule in ('R04','R05'): add(rule, 'UNKNOWN', '경로·구역·로봇 형상을 검증할 정보가 부족합니다.')
        distance_estop = False
    else:
        cap = min([robot.motion_limits.max_speed, p.speed_limit] +
                  [zone_map[z].max_speed for z in affected if zone_map[z].max_speed is not None])
        passed = p.speed_limit <= cap and max(speeds) <= cap + 1e-9
        add('R03','PASS' if passed else 'FAIL',
            f'제안 상한={p.speed_limit}, 궤적 최대 구간속도={max(speeds):.3f}, 적용 상한={cap:.3f} m/s')
        crossed = [z.zone_id for z in req.zones if z.zone_type == 'KEEP_OUT' and
                   path.distance(polys[z.zone_id]) <= req.context.robot_radius_m]
        add('R04','FAIL' if crossed else 'PASS',
            '로봇 외곽이 금지구역에 접촉/교차' if crossed else '로봇 외곽의 금지구역 접촉 없음', crossed)
        distance_estop = False
        if not req.context.workers_complete or any(w.pose.frame_id != 'world' or
             not fresh(w.last_seen_at,now) or w.safety_status == 'UNKNOWN' for w in req.workers):
            add('R05','UNKNOWN','완전하고 최신인 world 좌표 작업자 목록이 필요합니다.')
        else:
            warning = max([zone_map[z].minimum_safety_distance for z in affected], default=1.2)
            emergency = max([zone_map[z].estop_distance for z in affected], default=0.6)
            close = []
            for w in req.workers:
                # Planar projection deliberately conservative for point worker locations.
                gap = path.distance(Point(w.pose.position.x,w.pose.position.y)) - req.context.robot_radius_m
                if gap < warning: close.append(w.worker_id)
                if gap < emergency: distance_estop = True
            add('R05','FAIL' if close else 'PASS',
                '예상 경로의 작업자 안전거리 미달' if close else '현재 작업자 위치 기준 경로 안전거리 충족',close)
    blocked = related_critical_hazards(g,p.proposal_id)
    # Unknown zones/empty relation on unresolved critical events must not silently disappear.
    ambiguous = [h.hazard_id for h in req.hazards if h.severity == 'CRITICAL' and
                 h.status != 'RESOLVED' and h.zone_id not in zone_map]
    invalid_resolutions = [h.hazard_id for h in req.hazards if h.severity == 'CRITICAL' and
                          h.status == 'RESOLVED' and (h.resolved_by != 'OPERATOR' or h.resolved_at is None)]
    hazard_times_valid = all(
        h.detected_at <= now and
        (h.last_observed_at is None or h.detected_at <= h.last_observed_at <= now) and
        (h.resolved_at is None or h.detected_at <= h.resolved_at <= now)
        for h in req.hazards)
    if blocked:
        add('R06','FAIL','전체 로봇 차단 또는 관련 로봇·영향 구역의 미해결 CRITICAL 위험 존재',blocked)
    elif not req.context.hazards_complete or ambiguous or invalid_resolutions or not hazard_times_valid or path is None:
        add('R06','UNKNOWN','위험 목록·관련 구역·시각·관리자 해제 근거를 확인할 수 없습니다.', ambiguous+invalid_resolutions)
    else: add('R06','PASS','관련 미해결 CRITICAL 위험 없음')
    adapters = {a.adapter_id:a for a in req.adapters}
    adapter = adapters.get(robot.control_adapter) if robot else None
    adapter_ok = adapter and adapter.ready and adapter.robot_type == robot.robot_type and p.action_type in adapter.supported_actions and fresh(adapter.last_heartbeat_at,now,1.0)
    add('R07','PASS' if adapter_ok else 'FAIL','어댑터 등록·준비·동작 지원·하트비트 확인' if adapter_ok else '사용 가능한 제어 어댑터를 확인할 수 없습니다.')
    sensor_map = {s.sensor_id:s for s in req.sensors}
    uncovered = [z for z in affected if not any(
        fresh(sensor_map[sid].last_frame_at,now) and fresh(sensor_map[sid].last_processed_at,now)
        and sensor_map[sid].last_processed_at >= sensor_map[sid].last_frame_at
        for sid in covering_sensors(g,z))]
    if uncovered: add('R08','FAIL','정상 최신 관측 센서가 없는 영향 구역',uncovered)
    elif path is None or not affected: add('R08','UNKNOWN','전체 행동 영향 구역을 확인할 수 없습니다.')
    else: add('R08','PASS','모든 영향 구역에 ONLINE 센서와 최신 처리 프레임 존재',affected)
    failed = any(c.status == 'FAIL' for c in checks)
    unknown = any(c.status == 'UNKNOWN' for c in checks)
    decision = 'REJECT' if failed else 'INDETERMINATE' if unknown else 'APPROVE'
    estop = bool(blocked or distance_estop or (robot and robot.status == 'MOVING' and
        any(c.rule_id in ('R04','R05','R06','R08') and c.status != 'PASS' for c in checks)))
    evaluation_id = f'eval-{uuid4()}'
    for c in checks:
        g.add((node('ActionProposal',p.proposal_id), S.EVALUATED_BY,node('SafetyRule',c.rule_id)))
        g.add((node('SafetyRule',c.rule_id), RDF.type,S.SafetyRule))
    for hid in blocked: g.add((node('ActionProposal',p.proposal_id),S.BLOCKED_BY,node('HazardEvent',hid)))
    g.add((node('ActionProposal',p.proposal_id),S.RECORDED_AS,node('ExecutionLog',evaluation_id)))
    elapsed = (perf_counter()-start)*1000
    payload_hash = sha256(json.dumps(req.model_dump(mode='json'),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    log = ExecutionLog(log_id=evaluation_id,proposal_id=p.proposal_id,
        decision='APPROVE' if decision == 'APPROVE' else 'REJECT',
        rule_results=[RuleResult(rule_id=c.rule_id,passed=c.status=='PASS',reason=f'[{c.status}] {c.reason}') for c in checks],
        hazard_ids=blocked,robot_id=p.robot_id,timestamp=now,
        result_message=f'{decision}; requires_estop={estop}; policy={POLICY_VERSION}')
    result = EvaluationResponse(evaluation_id=evaluation_id,proposal_id=p.proposal_id,decision=decision,
        can_execute=decision=='APPROVE',requires_estop=estop,rule_results=checks,hazard_ids=blocked,
        affected_zone_ids=affected,evaluated_at=now,evaluated_in_ms=elapsed,policy_version=POLICY_VERSION,
        input_sha256=payload_hash,execution_log=log)
    return (result,g) if return_graph else result
