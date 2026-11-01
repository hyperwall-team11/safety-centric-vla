from safety_control.gate import SafetyGate, utc_iso

T0 = 1_000_000.0


def test_heartbeat_timeout_is_one_second():
    gate = SafetyGate()
    assert gate.heartbeat_lost(T0) and not gate.heartbeat_expired(T0)  # 받은 적 없음
    gate.on_heartbeat(T0)
    assert not gate.heartbeat_lost(T0 + 1.0)
    assert gate.heartbeat_expired(T0 + 1.01)


def test_estop_without_active_field_stops():
    gate = SafetyGate()
    assert gate.on_estop({}, T0) is not None  # 해석 불가 → 정지 (Fail-Closed)
    assert gate.estop_active


def test_estop_latency_and_release():
    gate = SafetyGate()
    trigger = gate.on_estop({"active": True, "triggered_by": "OPERATOR", "issued_at": utc_iso(T0)}, T0 + 0.05)
    assert trigger["triggered_by"] == "OPERATOR" and abs(trigger["latency_ms"] - 50) < 0.1
    assert gate.on_estop({"active": False}, T0 + 1) is None and not gate.estop_active


def test_resume_blocked_until_estop_released_and_heartbeat_alive():
    gate = SafetyGate()
    gate.on_estop({"active": True}, T0)
    assert gate.resume_blocker(T0) == "ROBOT_HELD"
    gate.on_estop({"active": False}, T0)
    assert gate.resume_blocker(T0) == "NO_HEARTBEAT"
    gate.on_heartbeat(T0)
    assert gate.resume_blocker(T0) is None
