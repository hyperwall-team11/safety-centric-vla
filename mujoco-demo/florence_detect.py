"""Run Florence-2 object detection on PNGs. Run inside a GPU allocation."""
import argparse
import json
import time
from pathlib import Path

import torch
from PIL import Image, ImageDraw
from transformers import AutoModelForCausalLM, AutoProcessor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--model", default="microsoft/Florence-2-base")
    parser.add_argument("--output", type=Path, default=Path("vlm_results"))
    args = parser.parse_args()
    for path in args.images:
        if not path.is_file():
            parser.error(f"Image not found: {path}")
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is unavailable. Run in a Slurm GPU allocation with CUDA PyTorch.")
    args.output.mkdir(parents=True, exist_ok=True)
    model_id = args.model
    print(f"GPU: {torch.cuda.get_device_name(0)}", flush=True)
    print(f"Loading {model_id}...", flush=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, trust_remote_code=True, torch_dtype=torch.float16,
        attn_implementation="eager",
    ).to("cuda").eval()
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    task = "<OD>"
    for path in args.images:
        with Image.open(path) as source:
            image = source.convert("RGB")
        inputs = processor(text=task, images=image, return_tensors="pt").to("cuda", torch.float16)
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        start = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(
                input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
                max_new_tokens=512, do_sample=False, num_beams=3,
            )
        torch.cuda.synchronize()
        generation_ms = (time.perf_counter() - start) * 1000
        raw = processor.batch_decode(generated, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(raw, task=task, image_size=image.size)
        result = {
            "image": str(path), "model": model_id, "task": task,
            "detections": parsed, "raw_output": raw,
            "generation_ms": generation_ms,
            "peak_torch_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2,
            "measurement_note": "Single-image generation only; not end-to-end latency or total GPU memory.",
        }
        (args.output / f"{path.stem}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        draw = ImageDraw.Draw(image)
        detections = parsed.get(task, {})
        for box, label in zip(detections.get("bboxes", []), detections.get("labels", [])):
            draw.rectangle(tuple(box), outline="lime", width=3)
            draw.text((box[0], box[1]), str(label), fill="lime")
        image.save(args.output / f"{path.stem}_detected.png")
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    print(f"Results saved to: {args.output.resolve()}")


if __name__ == "__main__":
    main()
