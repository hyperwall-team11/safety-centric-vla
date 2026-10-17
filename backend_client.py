"""Fail-closed HTTP client; no robot commands and no state mutation."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import httpx
from .contracts import EvaluationRequest, EvaluationResponse
from .evaluator import POLICY_VERSION


class SafetyServiceUnavailable(RuntimeError):
    pass


def request_evaluation(snapshot: dict, url='http://127.0.0.1:8001/evaluate') -> EvaluationResponse:
    try:
        req = EvaluationRequest.model_validate(snapshot)
        payload = req.model_dump(mode='json')
        expected_hash = sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        response = httpx.post(url,json=payload,timeout=2.0)
        response.raise_for_status()
        result = EvaluationResponse.model_validate(response.json())
        age = (datetime.now(timezone.utc)-result.evaluated_at).total_seconds()
        if result.proposal_id != req.proposal.proposal_id or result.input_sha256 != expected_hash or not -0.25 <= age <= 0.5:
            raise ValueError('Mismatched or expired evaluation response')
        if result.policy_version != POLICY_VERSION:
            raise ValueError('Unexpected policy version')
        if result.can_execute != (result.decision=='APPROVE') or (result.requires_estop and result.can_execute):
            raise ValueError('Inconsistent evaluation response')
        if result.can_execute:
            required = {f'R{i:02}' for i in range(1,9)} | {'DATA_FRESHNESS','PROPOSAL_TIMESTAMP','SUPPORTED_ACTION'}
            ids = [r.rule_id for r in result.rule_results]
            if len(ids) != len(set(ids)) or not required.issubset(ids) or any(r.status!='PASS' for r in result.rule_results):
                raise ValueError('Approval without complete passing evidence')
        if result.execution_log.proposal_id != req.proposal.proposal_id or result.execution_log.robot_id != req.proposal.robot_id:
            raise ValueError('Mismatched log identifiers')
        return result
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise SafetyServiceUnavailable('안전검사 실패: 실행 차단 필요') from exc
