#!/usr/bin/env python3
"""One-shot OpenVLA inference using one RGB image + instruction.

Example:
  python openvla_single_image_infer.py --image /shared/cctv.png --instruction 'move the gripper toward the box'
"""
import argparse
import json
from pathlib import Path
from urllib import error, request

import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForVision2Seq, AutoProcessor


def main():
    parser = argparse.ArgumentParser(description='OpenVLA image + instruction one-shot inference (no execution)')
    parser.add_argument('--image', type=Path, required=True, help='Path to the CCTV PNG/JPEG image')
    parser.add_argument('--instruction', required=True, help='English manipulation instruction for OpenVLA')
    parser.add_argument('--model', default='openvla/openvla-7b', help='Hugging Face model ID or local checkpoint')
    parser.add_argument('--unnorm-key', default='bridge_orig', help='Dataset stats key; bridge_orig applies to base checkpoint only')
    parser.add_argument('--output', type=Path, default=Path('openvla_action.json'))
    parser.add_argument('--backend-url', help='Backend base URL; requires --task-id to submit the 7D proposal')
    parser.add_argument('--task-id', help='Existing Backend TaskOrder ID')
    parser.add_argument('--manipulation', choices=('PICK', 'PLACE'), default='PICK')
    parser.add_argument('--speed-limit', type=float, default=0.2)
    parser.add_argument('--phase-complete', action='store_true',
                        help='Mark this observation-confirmed step as the end of PICK/PLACE phase')
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f'Image not found: {args.image}')
    if bool(args.backend_url) != bool(args.task_id):
        parser.error('--backend-url and --task-id must be provided together')
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA GPU unavailable. Run on an allocated GPU compute node, not the login node.')

    device = 'cuda:0'
    image = Image.open(args.image).convert('RGB')
    print(f'[INFO] image={args.image} size={image.size}; loading model={args.model}', flush=True)
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)
    model = AutoModelForVision2Seq.from_pretrained(
        args.model,
        torch_dtype=torch.bfloat16,
        low_cpu_mem_usage=True,
        trust_remote_code=True,
    ).to(device)
    model.eval()

    prompt = f'In: What action should the robot take to {args.instruction.lower()}?\nOut:'
    inputs = processor(prompt, image).to(device, dtype=torch.bfloat16)
    with torch.inference_mode():
        action = model.predict_action(**inputs, unnorm_key=args.unnorm_key, do_sample=False)
    action = np.asarray(action, dtype=np.float32).reshape(-1)
    if action.size != 7 or not np.isfinite(action).all():
        raise RuntimeError(f'Expected finite 7-D action, got shape={action.shape}: {action!r}')

    result = {
        'image': str(args.image),
        'instruction': args.instruction,
        'model': args.model,
        'unnorm_key': args.unnorm_key,
        'action_7d': action.tolist(),
        'executed': False,
    }
    if args.backend_url:
        payload = json.dumps({
            'action_7d': action.tolist(),
            'manipulation': args.manipulation,
            'speed_limit': args.speed_limit,
            'phase_complete': args.phase_complete,
            'model_name': args.model,
            'model_version': args.unnorm_key,
        }).encode('utf-8')
        endpoint = f"{args.backend_url.rstrip('/')}/task-orders/{args.task_id}/openvla-actions"
        http_request = request.Request(
            endpoint,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with request.urlopen(http_request, timeout=10) as response:
                result['submitted_proposal'] = json.loads(response.read().decode('utf-8'))
        except error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'Backend rejected OpenVLA action ({exc.code}): {detail}') from exc
        except error.URLError as exc:
            raise RuntimeError(f'Backend unavailable: {exc.reason}') from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.backend_url:
        print(f"[PASS] action submitted as {result['submitted_proposal']['proposal_id']}; "
              'evaluate and execute it through the Safety API.')
    else:
        print(f'[PASS] action saved to {args.output}; no robot command was issued.')


if __name__ == '__main__':
    main()
