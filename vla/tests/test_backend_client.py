import io
import json
from urllib import error

import pytest

from backend_client import BackendApiError, BackendClient


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_submit_builds_trusted_boundary_request_with_auth():
    calls = []

    def urlopen(http_request, timeout):
        calls.append((http_request, timeout))
        return Response({"proposal_id": "p1", "status": "PROPOSED"})

    client = BackendClient("http://backend/", token="secret", urlopen=urlopen)
    proposal = client.submit_openvla_action(
        "task-1",
        [0.0] * 7,
        manipulation="PICK",
        speed_limit=0.2,
        phase_complete=False,
        model_name="openvla",
        model_version="bridge_orig",
    )

    http_request, timeout = calls[0]
    assert proposal["proposal_id"] == "p1"
    assert http_request.full_url == "http://backend/task-orders/task-1/openvla-actions"
    assert http_request.method == "POST" and timeout == 10.0
    assert http_request.headers["Authorization"] == "Bearer secret"
    assert json.loads(http_request.data)["action_7d"] == [0.0] * 7


def test_http_error_is_normalized_with_status_code():
    def urlopen(http_request, timeout):
        raise error.HTTPError(
            http_request.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"detail":"active proposal"}'),
        )

    client = BackendClient("http://backend", urlopen=urlopen)
    with pytest.raises(BackendApiError, match="active proposal") as caught:
        client.get_task("task-1")
    assert caught.value.status_code == 409


def test_wait_for_proposal_returns_terminal_status():
    client = BackendClient("http://backend")
    states = iter([
        [{"proposal_id": "p1", "status": "EXECUTING"}],
        [{"proposal_id": "p1", "status": "SUCCEEDED"}],
    ])
    client.list_proposals = lambda _task_id: next(states)
    clock = iter([0.0, 0.0, 0.1, 0.2])

    result = client.wait_for_proposal(
        "task-1",
        "p1",
        timeout_seconds=1.0,
        poll_interval_seconds=0.1,
        monotonic=lambda: next(clock),
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "SUCCEEDED"


def test_wait_for_proposal_fails_if_record_disappears():
    client = BackendClient("http://backend")
    client.list_proposals = lambda _task_id: []
    clock = iter([0.0, 0.0])

    with pytest.raises(BackendApiError, match="disappeared"):
        client.wait_for_proposal(
            "task-1",
            "p1",
            timeout_seconds=1.0,
            monotonic=lambda: next(clock),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "not an object"),
        ({"task_id": "task-1"}, "missing status"),
    ],
)
def test_get_task_rejects_malformed_success_response(payload, message):
    client = BackendClient(
        "http://backend",
        urlopen=lambda _request, timeout: Response(payload),
    )

    with pytest.raises(BackendApiError, match=message):
        client.get_task("task-1")


def test_submit_rejects_success_response_without_proposal_id():
    client = BackendClient(
        "http://backend",
        urlopen=lambda _request, timeout: Response({"status": "PROPOSED"}),
    )

    with pytest.raises(BackendApiError, match="missing proposal_id"):
        client.submit_openvla_action(
            "task-1",
            [0.0] * 7,
            manipulation="PICK",
            speed_limit=0.2,
            phase_complete=False,
            model_name="openvla",
            model_version="bridge_orig",
        )


def test_list_proposals_rejects_non_object_items():
    client = BackendClient(
        "http://backend",
        urlopen=lambda _request, timeout: Response(["p1"]),
    )

    with pytest.raises(BackendApiError, match="non-object"):
        client.list_proposals("task-1")
