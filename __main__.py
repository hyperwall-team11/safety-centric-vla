import argparse
from pathlib import Path
from .contracts import EvaluationRequest
from .evaluator import evaluate

parser = argparse.ArgumentParser(description='Evaluate a complete trusted-backend JSON snapshot')
parser.add_argument('input', type=Path)
parser.add_argument('--graph', type=Path, help='Export evaluated ontology graph as Turtle')
args = parser.parse_args()
req = EvaluationRequest.model_validate_json(args.input.read_text(encoding='utf-8'))
result,graph = evaluate(req, return_graph=True)
if args.graph: graph.serialize(destination=str(args.graph),format='turtle')
print(result.model_dump_json(indent=2))
