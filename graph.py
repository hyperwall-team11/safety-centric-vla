from pathlib import Path
from urllib.parse import quote
from rdflib import Graph, Namespace, Literal, RDF, XSD

S = Namespace('https://example.org/robot-safety#')
I = Namespace('https://example.org/robot-safety/instance/')


def node(kind, identifier):
    return I[f'{kind}/{quote(identifier, safe="")}']


def build_graph(req, affected):
    # A fresh graph per request prevents stale resolved hazards leaking into new evaluations.
    g = Graph().parse(Path(__file__).with_name('ontology.ttl'), format='turtle')
    g.bind('s', S)
    for name, key, kind in [('robots','robot_id','Robot'), ('tasks','task_id','TaskOrder'),
                            ('sensors','sensor_id','Sensor'), ('zones','zone_id','Zone'),
                            ('workers','worker_id','Worker'), ('hazards','hazard_id','HazardEvent')]:
        for item in getattr(req, name):
            n = node(kind, getattr(item, key))
            g.add((n, RDF.type, S[kind]))
            g.add((n, S.identifier, Literal(getattr(item, key))))
            for field in ('status', 'severity'):
                if hasattr(item, field): g.add((n, S[field], Literal(getattr(item, field))))
    p = node('ActionProposal', req.proposal.proposal_id)
    g.add((p, RDF.type, S.ActionProposal))
    g.add((p, S.TARGETS, node('Robot', req.proposal.robot_id)))
    for zone_id in affected: g.add((p, S.AFFECTS, node('Zone', zone_id)))
    for t in req.tasks:
        tnode = node('TaskOrder', t.task_id)
        g.add((tnode, S.ASSIGNED_TO, node('Robot', t.target_robot_id)))
        if t.task_id == req.proposal.task_id: g.add((tnode, S.GENERATES, p))
    for sensor in req.sensors:
        for z in sensor.covered_zone_ids:
            g.add((node('Sensor', sensor.sensor_id), S.COVERS, node('Zone', z)))
    for hazard in req.hazards:
        h = node('HazardEvent', hazard.hazard_id)
        g.add((h, S.OCCURRED_IN, node('Zone', hazard.zone_id)))
        g.add((h, S.globalScope, Literal(not hazard.related_robot_ids, datatype=XSD.boolean)))
        for r in hazard.related_robot_ids: g.add((h, S.RELATED_TO, node('Robot', r)))
    return g


def related_critical_hazards(g, proposal_id):
    # Bound RDF nodes: user-provided IDs are never interpolated into SPARQL text.
    query = '''PREFIX s: <https://example.org/robot-safety#>
    SELECT DISTINCT ?id WHERE {
      ?hazard a s:HazardEvent ; s:identifier ?id ;
              s:severity "CRITICAL" ; s:status ?status .
      FILTER (?status IN ("OPEN", "ACKNOWLEDGED"))
      { ?proposal s:TARGETS ?robot . ?hazard s:RELATED_TO ?robot . }
      UNION
      { ?proposal s:AFFECTS ?zone . ?hazard s:OCCURRED_IN ?zone . }
      UNION
      { ?hazard s:globalScope true . }
    }'''
    return sorted(str(row.id) for row in g.query(query, initBindings={
        'proposal': node('ActionProposal', proposal_id)}))


def covering_sensors(g, zone_id):
    query = '''PREFIX s: <https://example.org/robot-safety#>
    SELECT DISTINCT ?id WHERE {
       ?sensor a s:Sensor ; s:COVERS ?zone ; s:identifier ?id ; s:status "ONLINE" .
    }'''
    return sorted(str(row.id) for row in g.query(query, initBindings={'zone': node('Zone', zone_id)}))
