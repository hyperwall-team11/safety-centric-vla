#!/usr/bin/env python3
"""Run observation -> OpenVLA -> Safety -> ROS execution until a stop condition."""
from __future__ import annotations

import argparse
import json
import os

from backend_client import BackendApiError, BackendClient
from closed_loop import OpenVlaClosedLoop, StopReason
from observation_sources import FileFrameSource, JsonPhaseCompletionSource
from openvla_engine import OpenVlaInferenceEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenVLA fail-closed execution loop")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backend-token", default=os.getenv("OPENVLA_BACKEND_TOKEN"))
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--image", required=True, help="CCTV image file overwritten for each frame")
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--manipulation", choices=("PICK", "PLACE"), required=True)
    parser.add_argument("--model", default="openvla/openvla-7b")
    parser.add_argument("--unnorm-key", default="bridge_orig")
    parser.add_argument("--speed-limit", type=float, default=0.2)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--frame-timeout", type=float, default=30.0)
    parser.add_argument("--result-timeout", type=float, default=30.0)
    parser.add_argument(
        "--completion-signal",
        help="CV/operator JSON file; phase completion stays false when omitted or absent",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    completion = (
        JsonPhaseCompletionSource(args.completion_signal)
        if args.completion_signal
        else None
    )
    backend = BackendClient(args.backend_url, token=args.backend_token)
    engine = OpenVlaInferenceEngine(args.model, unnorm_key=args.unnorm_key)
    frames = FileFrameSource(args.image, timeout_seconds=args.frame_timeout)
    loop = OpenVlaClosedLoop(
        backend,
        engine,
        frames,
        completion=completion,
        result_timeout_seconds=args.result_timeout,
        event_sink=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True),
    )
    try:
        result = loop.run(
            args.task_id,
            args.instruction,
            manipulation=args.manipulation,
            speed_limit=args.speed_limit,
            max_steps=args.max_steps,
        )
    except (BackendApiError, OSError, ValueError, TimeoutError) as exc:
        print(json.dumps({"status": "ERROR", "detail": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
    except KeyboardInterrupt as exc:
        print(json.dumps({"status": "INTERRUPTED"}))
        raise SystemExit(130) from exc

    print(json.dumps({
        "status": "STOPPED",
        "reason": result.reason,
        "completed_steps": result.completed_steps,
        "task_status": result.task_status,
        "proposal_id": result.proposal_id,
    }, ensure_ascii=False))
    successful = (
        result.reason == StopReason.PHASE_COMPLETE
        or result.reason == StopReason.TASK_TERMINAL
        and result.task_status == "COMPLETED"
    )
    raise SystemExit(0 if successful else 2)


if __name__ == "__main__":
    main()
