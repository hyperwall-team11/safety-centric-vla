from pathlib import Path

from closed_loop import OpenVlaClosedLoop, StopReason


class Engine:
    model_id = "openvla-test"
    unnorm_key = "bridge_orig"

    def __init__(self):
        self.calls = []

    def predict(self, frame, instruction):
        self.calls.append((frame, instruction))
        return [0.0] * 7


class Frames:
    def __init__(self):
        self.calls = []

    def next_frame(self, step):
        self.calls.append(step)
        return Path(f"frame-{step}.png")


class Completion:
    def __init__(self, completed_step=None):
        self.completed_step = completed_step

    def is_complete(self, manipulation, frame, step):
        return step == self.completed_step


class Backend:
    def __init__(self, *, evaluate_status="APPROVED", result_status="SUCCEEDED"):
        self.evaluate_status = evaluate_status
        self.result_status = result_status
        self.submissions = []
        self.task_status = "PLANNING"

    def get_task(self, task_id):
        return {"task_id": task_id, "status": self.task_status}

    def submit_openvla_action(self, task_id, action, **kwargs):
        proposal_id = f"p{len(self.submissions) + 1}"
        self.submissions.append((task_id, action, kwargs))
        return {"proposal_id": proposal_id, "status": "PROPOSED"}

    def evaluate(self, proposal_id):
        return {"proposal_id": proposal_id, "status": self.evaluate_status}

    def execute(self, proposal_id):
        return {"proposal_id": proposal_id, "status": "EXECUTING"}

    def wait_for_proposal(self, task_id, proposal_id, timeout_seconds):
        return {"proposal_id": proposal_id, "status": self.result_status}


def make_loop(backend=None, completion=None):
    backend = backend or Backend()
    engine = Engine()
    frames = Frames()
    loop = OpenVlaClosedLoop(
        backend,
        engine,
        frames,
        completion=completion,
        result_timeout_seconds=5,
    )
    return loop, backend, engine, frames


def test_repeats_observe_infer_evaluate_execute_until_step_limit():
    loop, backend, engine, frames = make_loop()

    result = loop.run(
        "task-1",
        "grasp the box",
        manipulation="PICK",
        speed_limit=0.2,
        max_steps=3,
    )

    assert result.reason == StopReason.STEP_LIMIT and result.completed_steps == 3
    assert frames.calls == [0, 1, 2] and len(engine.calls) == 3
    assert len(backend.submissions) == 3


def test_safety_rejection_stops_before_execute_or_next_inference():
    backend = Backend(evaluate_status="REJECTED")
    backend.execute = lambda _proposal_id: (_ for _ in ()).throw(AssertionError("must not execute"))
    loop, backend, engine, frames = make_loop(backend)

    result = loop.run(
        "task-1",
        "grasp",
        manipulation="PICK",
        speed_limit=0.2,
        max_steps=5,
    )

    assert result.reason == StopReason.SAFETY_REJECTED
    assert result.completed_steps == 0 and len(engine.calls) == 1


def test_execution_failure_stops_loop():
    loop, _backend, engine, _frames = make_loop(Backend(result_status="FAILED"))

    result = loop.run(
        "task-1",
        "grasp",
        manipulation="PICK",
        speed_limit=0.2,
        max_steps=5,
    )

    assert result.reason == StopReason.EXECUTION_FAILED
    assert result.completed_steps == 0 and len(engine.calls) == 1


def test_phase_completion_stops_after_confirmed_success():
    loop, backend, _engine, _frames = make_loop(completion=Completion(completed_step=1))

    result = loop.run(
        "task-1",
        "grasp",
        manipulation="PICK",
        speed_limit=0.2,
        max_steps=5,
    )

    assert result.reason == StopReason.PHASE_COMPLETE
    assert result.completed_steps == 2
    assert backend.submissions[-1][2]["phase_complete"] is True


def test_terminal_task_stops_before_observation():
    backend = Backend()
    backend.task_status = "HELD"
    loop, _backend, engine, frames = make_loop(backend)

    result = loop.run(
        "task-1",
        "grasp",
        manipulation="PICK",
        speed_limit=0.2,
        max_steps=5,
    )

    assert result.reason == StopReason.TASK_TERMINAL
    assert not engine.calls and not frames.calls
