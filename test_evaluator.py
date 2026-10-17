from copy import deepcopy
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from examples.make_examples import sample, hazard
from ontology_safety.contracts import EvaluationRequest
from ontology_safety.evaluator import evaluate
from ontology_safety.graph import S, node
from ontology_safety.api import app


def run(data, graph=False):
    return evaluate(EvaluationRequest.model_validate(data),
        now=datetime.fromisoformat(data['context']['snapshot_at']),return_graph=graph)


def statuses(result):
    return {r.rule_id:r.status for r in result.rule_results}


def test_normal_approval_and_graph():
    d = sample(); r,g = run(d,True)
    assert r.decision == 'APPROVE' and r.can_execute
    assert all(x.status=='PASS' for x in r.rule_results)
    assert (node('ActionProposal','proposal-001'),S.AFFECTS,node('Zone','zone-A')) in g
    assert r.execution_log.decision == 'APPROVE'


@pytest.mark.parametrize('state',['OPEN','ACKNOWLEDGED'])
def test_zone_relation_blocks_even_without_robot_id(state):
    d=sample(); h=hazard(d); h['status']=state; d['hazards']=[h]
    r,g=run(d,True)
    assert r.decision=='REJECT' and r.requires_estop
    assert r.hazard_ids==['hazard-001']
    assert (node('ActionProposal','proposal-001'),S.BLOCKED_BY,node('HazardEvent','hazard-001')) in g


def test_unrelated_zone_does_not_block():
    d=sample(); h=hazard(d); h['zone_id']='zone-B'; h['related_robot_ids']=['robot-other']; d['hazards']=[h]
    z=deepcopy(d['zones'][0]); z['zone_id']='zone-B'
    z['geometry']['coordinates']=[[[20,20],[25,20],[25,25],[20,25],[20,20]]]
    d['zones'].append(z)
    assert run(d).decision=='APPROVE'


def test_direct_robot_relation_blocks_other_zone():
    d=sample(); h=hazard(d); h.update(zone_id='elsewhere',related_robot_ids=['robot-001']);d['hazards']=[h]
    assert run(d).requires_estop


def test_resolved_critical_requires_operator():
    d=sample();h=hazard(d);h.update(status='RESOLVED',resolved_at=d['context']['snapshot_at']);d['hazards']=[h]
    assert run(d).decision=='INDETERMINATE'
    h['resolved_by']='OPERATOR'
    assert run(d).decision=='APPROVE'


@pytest.mark.parametrize('mutate',[
    lambda d:d['context'].update(hazards_complete=False),
    lambda d:d['context'].update(workers_complete=False),
    lambda d:d['context'].update(zones_complete=False),
    lambda d:d['context'].update(robot_radius_m=None),
    lambda d:d['proposal'].update(trajectory=None),
    lambda d:d['proposal'].update(frame_id='mujoco_world'),
    lambda d:d['robots'][0].update(robot_type='MANIPULATOR'),
])
def test_incomplete_never_approved(mutate):
    d=sample();mutate(d);assert not run(d).can_execute


@pytest.mark.parametrize('field',['last_frame_at','last_processed_at'])
def test_stale_or_missing_sensor_denied(field):
    d=sample();d['sensors'][0][field]=None
    r=run(d);assert statuses(r)['R08']=='FAIL' and not r.can_execute


def test_offline_sensor():
    d=sample();d['sensors'][0]['status']='OFFLINE'
    assert statuses(run(d))['R08']=='FAIL'


def test_zone_speed_limit():
    d=sample();d['zones'][0]['max_speed']=0.1
    assert statuses(run(d))['R03']=='FAIL'


def test_actual_trajectory_speed_over_limit():
    d=sample();d['proposal']['trajectory'][1]['time_from_start_s']=1
    assert statuses(run(d))['R03']=='FAIL'


def test_keep_out_crossing_between_waypoints():
    d=sample();z=deepcopy(d['zones'][0]);z.update(zone_id='keepout',zone_type='KEEP_OUT')
    z['geometry']['coordinates']=[[[0.4,-0.1],[0.6,-0.1],[0.6,0.1],[0.4,0.1],[0.4,-0.1]]]
    d['zones'].append(z);d['sensors'][0]['covered_zone_ids'].append('keepout')
    assert statuses(run(d))['R04']=='FAIL'


def worker(d,x=0.5,y=1.0):
    return {'worker_id':'worker-1','pose':{'frame_id':'world','position':{'x':x,'y':y,'z':0}},
       'current_zone_id':'zone-A','safety_status':'WARNING',
       'last_seen_at':d['context']['snapshot_at'],'confidence':0.9}


def test_worker_distance_and_estop():
    d=sample();d['workers']=[worker(d)]
    r=run(d);assert statuses(r)['R05']=='FAIL' and not r.requires_estop
    d['workers'][0]['pose']['position']['y']=0.3
    assert run(d).requires_estop


def test_stale_worker_unknown():
    d=sample();w=worker(d);w['last_seen_at']='2000-01-01T00:00:00Z';d['workers']=[w]
    assert statuses(run(d))['R05']=='UNKNOWN'


def test_adapter_and_task_identity():
    d=sample();d['adapters']=[]
    assert statuses(run(d))['R07']=='FAIL'
    d=sample();d['tasks'][0]['target_robot_id']='other'
    assert statuses(run(d))['R01']=='FAIL'


def test_stale_snapshot_and_future_rejected():
    d=sample();d['robots'][0]['updated_at']='2000-01-01T00:00:00Z'
    assert statuses(run(d))['DATA_FRESHNESS']=='UNKNOWN'
    d=sample();d['context']['snapshot_at']='2099-01-01T00:00:00Z'
    assert not evaluate(EvaluationRequest.model_validate(d)).can_execute


def test_duplicate_and_naive_utc_rejected():
    d=sample();d['robots'].append(deepcopy(d['robots'][0]))
    with pytest.raises(ValidationError): EvaluationRequest.model_validate(d)
    d=sample();d['context']['snapshot_at']='2026-10-17T00:00:00'
    with pytest.raises(ValidationError): EvaluationRequest.model_validate(d)


def test_no_state_leak_between_requests():
    d=sample();d['hazards']=[hazard(d)];assert not run(d).can_execute
    d['hazards']=[];assert run(d).can_execute


def test_approval_revoked_on_preexecution_recheck():
    d=sample();assert run(d).can_execute
    d['hazards']=[hazard(d)];assert not run(d).can_execute


def test_id_with_sparql_text_is_bound_safely():
    d=sample();d['proposal']['proposal_id']='x" . } UNION { ?a ?b ?c } #'
    d['hazards']=[hazard(d)]
    assert run(d).hazard_ids==['hazard-001']


def test_api_and_validation():
    with TestClient(app) as client:
        assert client.get('/health').status_code==200
        r=client.post('/evaluate',json=sample())
        assert r.status_code==200 and r.json()['can_execute']
        assert client.post('/evaluate',json={}).status_code==422
        assert client.get('/openapi.json').status_code==200


def test_moving_robot_sensor_loss_requests_stop():
    d=sample();d['robots'][0]['status']='MOVING';d['sensors'][0]['status']='OFFLINE'
    r=run(d);assert not r.can_execute and r.requires_estop


def test_stop_not_treated_as_motion_and_not_auto_approved():
    d=sample();d['proposal'].update(action_type='STOP',parameters={})
    assert not run(d).can_execute


def test_unsupported_request_policy_field_rejected():
    d=sample();d['rules']=[]
    with pytest.raises(ValidationError): EvaluationRequest.model_validate(d)


def test_api_internal_failure_is_503(monkeypatch):
    import ontology_safety.api as api
    def broken(_): raise RuntimeError('test failure')
    monkeypatch.setattr(api,'evaluate',broken)
    with TestClient(app) as client:
        r=client.post('/evaluate',json=sample())
        assert r.status_code==503 and 'execution must be blocked' in r.json()['detail']


def test_backend_client_timeout_fails_closed(monkeypatch):
    import httpx
    from ontology_safety.backend_client import request_evaluation, SafetyServiceUnavailable
    def timeout(*args,**kwargs): raise httpx.TimeoutException('test timeout')
    monkeypatch.setattr(httpx,'post',timeout)
    with pytest.raises(SafetyServiceUnavailable): request_evaluation(sample())


def test_backend_client_valid_response(monkeypatch):
    import httpx
    from ontology_safety.backend_client import request_evaluation
    def reply(url,*,json,timeout):
        data=evaluate(EvaluationRequest.model_validate(json)).model_dump(mode='json')
        return httpx.Response(200,json=data,request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',reply)
    assert request_evaluation(sample()).can_execute


def test_unknown_hazard_zone_not_silently_ignored():
    d=sample();h=hazard(d);h.update(zone_id='unmapped',related_robot_ids=['other']);d['hazards']=[h]
    assert run(d).decision=='INDETERMINATE'
