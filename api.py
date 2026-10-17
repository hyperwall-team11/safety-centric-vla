import logging
from fastapi import FastAPI, HTTPException
from .contracts import EvaluationRequest, EvaluationResponse
from .evaluator import evaluate, POLICY_VERSION

app = FastAPI(title='Ontology Safety Evaluator', version='1.1.0')

@app.get('/health')
def health():
    return {'status':'ok','policy_version':POLICY_VERSION}

@app.post('/evaluate', response_model=EvaluationResponse)
def evaluate_endpoint(request: EvaluationRequest):
    try:
        return evaluate(request)
    except Exception:
        logging.exception('Safety evaluation failed')
        # Backend MUST treat non-2xx/network/parse errors as execution denial.
        raise HTTPException(503, 'Safety evaluation unavailable; execution must be blocked')
