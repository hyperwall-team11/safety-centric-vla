from tiago_command import TiagoCommandRunner


def test_runner_completes_generator_without_blocking():
    events = []

    def task_factory(command):
        events.append(("start", command["command_id"]))
        yield 0.1
        events.append(("middle", command["command_id"]))
        yield 0.1
        events.append(("done", command["command_id"]))

    runner = TiagoCommandRunner(task_factory, lambda: events.append(("hold", None)))

    assert runner.submit({"command_id": "c1", "manipulation": "PICK"}) == []
    assert runner.tick(0.0) == []
    assert events == [("start", "c1")]
    assert runner.tick(0.05) == []
    assert runner.tick(0.05) == []
    assert events[-1] == ("middle", "c1")
    result = runner.tick(0.1)

    assert events[-1] == ("done", "c1")
    assert result == [{
        "command_id": "c1",
        "success": True,
        "reason_code": None,
        "message": "팔 조작 완료",
    }]


def test_runner_rejects_busy_and_cancel_holds_motion():
    held = []

    def task_factory(command):
        while True:
            yield 0.1

    runner = TiagoCommandRunner(task_factory, lambda: held.append(True))
    runner.submit({"command_id": "c1", "manipulation": "PICK"})

    busy = runner.submit({"command_id": "c2", "manipulation": "PLACE"})
    cancelled = runner.submit({"command_id": "c1", "manipulation": "CANCEL"})

    assert busy[0]["reason_code"] == "ROBOT_BUSY"
    assert cancelled[0]["reason_code"] == "CANCELLED"
    assert cancelled[0]["command_id"] == "c1"
    assert held == [True]
    assert runner.task is None


def test_runner_does_not_collapse_setpoints_during_catch_up():
    setpoints = []

    def task_factory(_command):
        for value in range(5):
            setpoints.append(value)
            yield 0.01

    runner = TiagoCommandRunner(task_factory, lambda: None)
    runner.submit({"command_id": "c1", "manipulation": "PICK"})

    runner.tick(0.0)
    runner.tick(0.1)

    assert setpoints == [0, 1]


def test_runner_reports_invalid_factory_and_runtime_failure():
    invalid = TiagoCommandRunner(
        lambda command: (_ for _ in () if False),
        lambda: None,
    )
    # Empty generator is a valid plan and completes on its first tick.
    invalid.submit({"command_id": "empty", "manipulation": "PICK"})
    assert invalid.tick(0)[0]["success"]

    held = []

    def broken(_command):
        yield 0.01
        raise RuntimeError("IK failed")

    runner = TiagoCommandRunner(broken, lambda: held.append(True))
    runner.submit({"command_id": "bad", "manipulation": "PICK"})
    runner.tick(0)
    result = runner.tick(0.01)

    assert result[0]["reason_code"] == "HW_ERROR"
    assert "IK failed" in result[0]["message"]
    assert held == [True]
