from copy import deepcopy
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from examples.make_examples import sample, hazard
from ontology_safety.contracts import EvaluationRequest
from ontology_safety.evaluator import evaluate
from ontology_safety.base_models import Robot, TaskOrder, ActionProposal, HazardEvent
from ontology_safety.backend_bridge import build_backend_snapshot, map_backend_result, assess_for_backend
from ontology_safety.backend_client import SafetyServiceUnavailable
from ontology_safety.graph import S, node


def result(d):
    return evaluate(EvaluationRequest.model_validate(d))


def kwargs(d):
    return dict(proposal=d['proposal'],robot=d['robots'][0],task=d['tasks'][0],hazards=d['hazards'])


def scene(d):
    return {k:deepcopy(d[k]) for k in ('sensors','zones','workers','adapters','context')}


def test_global_hazard_blocks_even_outside_all_known_zones():
    d=sample();h=hazard(d);h['zone_id']='elsewhere';d['hazards']=[h]
    r,g=evaluate(EvaluationRequest.model_validate(d),return_graph=True)
    assert r.decision=='REJECT' and r.hazard_ids==['hazard-001']
    assert (node('HazardEvent','hazard-001'),S.globalScope,None) in g


def test_old_proposal_with_fresh_scene_can_be_evaluated():
    d=sample();d['proposal']['proposed_at']='2026-01-01T00:00:00Z'
    assert result(d).can_execute
    d['proposal']['status']='APPROVED'
    assert result(d).can_execute


def test_future_proposal_not_approved():
    d=sample();d['proposal']['proposed_at']='2099-01-01T00:00:00Z'
    assert not result(d).can_execute


def test_v02_only_objects_parse_but_do_not_get_fabricated_scene():
    d=sample();h=hazard(d);h.pop('resolved_by')
    args=dict(proposal=ActionProposal.model_validate(d['proposal']),robot=Robot.model_validate(d['robots'][0]),
        task=TaskOrder.model_validate(d['tasks'][0]),hazards=[HazardEvent.model_validate(h)])
    snapshot=build_backend_snapshot(**args)
    assert not snapshot['context']['hazards_complete'] and not snapshot['context']['workers_complete']
    assert snapshot['sensors']==[] and snapshot['zones']==[] and snapshot['adapters']==[]
    r=result(snapshot)
    assert not r.can_execute and r.hazard_ids==['hazard-001']


def test_bridge_does_not_mutate_existing_models_or_fill_fake_timestamps():
    d=sample();d['robots'][0]['updated_at']='2000-01-01T00:00:00Z'
    before=deepcopy(d)
    snapshot=build_backend_snapshot(**kwargs(d),scene=scene(d))
    assert d==before and snapshot['robots'][0]['updated_at'].startswith('2000-')
    assert not result(snapshot).can_execute


def test_bridge_maps_indeterminate_to_existing_statuses():
    d=sample();d['proposal']['trajectory']=None;r=result(d)
    assert r.decision=='INDETERMINATE'
    a=map_backend_result(r,'evaluate')
    assert a.proposal_status=='REJECTED' and a.http_status==200 and a.execution_log.decision=='REJECT'
    a=map_backend_result(r,'execute')
    assert a.proposal_status=='HELD' and a.http_status==409 and a.execution_log.decision=='ESTOP'


def test_bridge_does_not_claim_execution_before_dispatch():
    r=result(sample());a=map_backend_result(r,'execute')
    assert a.can_execute and a.proposal_status=='APPROVED'
    assert a.execution_log.decision=='APPROVE'


def test_evaluation_log_is_not_estop_dispatch():
    d=sample();d['hazards']=[hazard(d)];r=result(d)
    assert r.requires_estop and r.execution_log.decision=='REJECT'
    assert map_backend_result(r,'evaluate').execution_log.decision=='REJECT'


def test_map_frame_and_null_trajectory_never_auto_converted():
    d=sample();d['robots'][0]['pose']['frame_id']='map';d['proposal']['trajectory']=None
    snap=build_backend_snapshot(**kwargs(d),scene=scene(d))
    assert snap['robots'][0]['pose']['frame_id']=='map' and snap['proposal']['trajectory'] is None
    assert not result(snap).can_execute


def test_resolved_v02_critical_without_operator_proof_stays_blocked():
    d=sample();h=hazard(d);h.update(status='RESOLVED',resolved_at=d['context']['snapshot_at']);d['hazards']=[h]
    assert not result(build_backend_snapshot(**kwargs(d),scene=scene(d))).can_execute
    sc=scene(d);sc['hazard_metadata']={'hazard-001':{'resolved_by':'OPERATOR'}}
    assert result(build_backend_snapshot(**kwargs(d),scene=sc)).can_execute


def test_hazard_metadata_cannot_override_scope():
    d=sample();d['hazards']=[hazard(d)]
    sc=scene(d);sc['hazard_metadata']={'hazard-001':{'related_robot_ids':['other']}}
    with pytest.raises(ValueError): build_backend_snapshot(**kwargs(d),scene=sc)


def test_phase_preconditions_and_errors_fail_closed(monkeypatch):
    d=sample()
    with pytest.raises(SafetyServiceUnavailable): assess_for_backend(**kwargs(d),phase='execute')
    def timeout(*args,**kw): raise httpx.TimeoutException('offline')
    monkeypatch.setattr(httpx,'post',timeout)
    with pytest.raises(SafetyServiceUnavailable): assess_for_backend(**kwargs(d),scene=scene(d))


def test_bridge_roundtrip_http_serialization(monkeypatch):
    d=sample()
    def reply(url,*,json,timeout):
        return httpx.Response(200,json=result(json).model_dump(mode='json'),request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',reply)
    a=assess_for_backend(**kwargs(d),scene=scene(d))
    assert a.proposal_status=='APPROVED'
    d['proposal']['status']='APPROVED'
    a=assess_for_backend(**kwargs(d),scene=scene(d),phase='execute')
    assert a.can_execute and a.proposal_status=='APPROVED'


def test_forged_approval_without_checks_is_rejected(monkeypatch):
    from ontology_safety.backend_client import request_evaluation
    d=sample()
    def reply(url,*,json,timeout):
        r=result(json).model_dump(mode='json');r['rule_results']=[]
        return httpx.Response(200,json=r,request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',reply)
    with pytest.raises(SafetyServiceUnavailable): request_evaluation(d)
