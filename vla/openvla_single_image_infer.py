#!/usr/bin/env python3
"""One-shot OpenVLA inference using one RGB image + instruction.

No backend calls and no robot execution are performed.
Example:
  python openvla_single_image_infer.py --image /shared/cctv.png --instruction 'move the gripper toward the box'
"""
import argparse
import json
from pathlib import Path

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
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f'Image not found: {args.image}')
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'[PASS] action saved to {args.output}; no robot command was issued.')


if __name__ == '__main__':
    main()
