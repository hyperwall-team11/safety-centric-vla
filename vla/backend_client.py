"""Small fail-closed HTTP client for the OpenVLA execution loop."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from urllib import error, request


TERMINAL_PROPOSAL_STATUSES = {"SUCCEEDED", "FAILED", "HELD", "REJECTED"}
TERMINAL_TASK_STATUSES = {"COMPLETED", "FAILED", "HELD"}


class BackendApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BackendClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 10.0,
        urlopen: Callable = request.urlopen,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen

    def _request(self, method: str, path: str, payload: dict | None = None):
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        http_request = request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self.urlopen(http_request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BackendApiError(
                f"Backend {method} {path} rejected ({exc.code}): {detail}",
                status_code=exc.code,
            ) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise BackendApiError(f"Backend {method} {path} unavailable: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendApiError(f"Backend {method} {path} returned invalid JSON") from exc

    @staticmethod
    def _require_object(response, context: str, *required_keys: str) -> dict:
        if not isinstance(response, dict):
            raise BackendApiError(f"Backend {context} response is not an object")
        missing = [key for key in required_keys if not response.get(key)]
        if missing:
            raise BackendApiError(
                f"Backend {context} response is missing {', '.join(missing)}"
            )
        return response

    def get_task(self, task_id: str) -> dict:
        response = self._request("GET", f"/task-orders/{task_id}")
        return self._require_object(response, "task", "task_id", "status")

    def submit_openvla_action(
        self,
        task_id: str,
        action_7d: list[float],
        *,
        manipulation: str,
        speed_limit: float,
        phase_complete: bool,
        model_name: str,
        model_version: str,
    ) -> dict:
        response = self._request(
            "POST",
            f"/task-orders/{task_id}/openvla-actions",
            {
                "action_7d": action_7d,
                "manipulation": manipulation,
                "speed_limit": speed_limit,
                "phase_complete": phase_complete,
                "model_name": model_name,
                "model_version": model_version,
            },
        )
        return self._require_object(response, "proposal", "proposal_id", "status")

    def evaluate(self, proposal_id: str) -> dict:
        response = self._request("POST", f"/action-proposals/{proposal_id}/evaluate")
        return self._require_object(response, "evaluation", "proposal_id", "status")

    def execute(self, proposal_id: str) -> dict:
        response = self._request("POST", f"/action-proposals/{proposal_id}/execute")
        return self._require_object(response, "execution", "proposal_id", "status")

    def list_proposals(self, task_id: str) -> list[dict]:
        result = self._request("GET", f"/task-orders/{task_id}/action-proposals")
        if not isinstance(result, list):
            raise BackendApiError("Backend proposal list response is not an array")
        if any(not isinstance(item, dict) for item in result):
            raise BackendApiError("Backend proposal list contains a non-object item")
        return result

    def wait_for_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.2,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("wait timeouts must be positive")
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            proposal = next(
                (
                    item
                    for item in self.list_proposals(task_id)
                    if item.get("proposal_id") == proposal_id
                ),
                None,
            )
            if proposal is None:
                raise BackendApiError(f"Proposal {proposal_id} disappeared from task {task_id}")
            if proposal.get("status") in TERMINAL_PROPOSAL_STATUSES:
                return proposal
            sleep(poll_interval_seconds)
        raise BackendApiError(
            f"Proposal {proposal_id} result timed out after {timeout_seconds:g}s"
        )
