"""Run Florence-2 object detection on PNGs. Run inside a GPU allocation."""
import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import torch
from PIL import Image, ImageDraw
from transformers import AutoModelForCausalLM, AutoProcessor


def detect_once(model, processor, image, task):
    """Run one inference with the same settings in detection and benchmark modes."""
    inputs = processor(text=task, images=image, return_tensors="pt").to("cuda", torch.float16)
    torch.cuda.synchronize()
    generation_start = time.perf_counter()
    with torch.inference_mode():
        generated = model.generate(
            input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
            max_new_tokens=512, do_sample=False, num_beams=3,
        )
    torch.cuda.synchronize()
    generation_ms = (time.perf_counter() - generation_start) * 1000
    raw = processor.batch_decode(generated, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(raw, task=task, image_size=image.size)
    return raw, parsed, generation_ms


def percentile(samples, fraction):
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def benchmark(model, processor, paths, model_id, seconds, warmup_iterations, task):
    """Replay decoded images for a sustained single-stream measurement."""
    images = []
    for path in paths:
        with Image.open(path) as source:
            images.append(source.convert("RGB"))

    for index in range(warmup_iterations):
        detect_once(model, processor, images[index % len(images)], task)

    torch.cuda.reset_peak_memory_stats()
    latencies_ms = []
    measurement_start = time.perf_counter()
    while time.perf_counter() - measurement_start < seconds or not latencies_ms:
        image = images[len(latencies_ms) % len(images)]
        iteration_start = time.perf_counter()
        detect_once(model, processor, image, task)
        latencies_ms.append((time.perf_counter() - iteration_start) * 1000)
    elapsed = time.perf_counter() - measurement_start
    throughput_hz = len(latencies_ms) / elapsed
    return {
        "model": model_id,
        "gpu": torch.cuda.get_device_name(0),
        "task": task,
        "images": [{"path": str(path), "width": image.width, "height": image.height}
                   for path, image in zip(paths, images)],
        "warmup_iterations": warmup_iterations,
        "requested_seconds": seconds,
        "measured_seconds": elapsed,
        "completed_iterations": len(latencies_ms),
        "throughput_hz": throughput_hz,
        "mean_latency_ms": sum(latencies_ms) / len(latencies_ms),
        "p50_latency_ms": percentile(latencies_ms, 0.50),
        "p95_latency_ms": percentile(latencies_ms, 0.95),
        "max_latency_ms": max(latencies_ms),
        "target_hz": 10,
        "meets_10hz_single_stream_target": throughput_hz >= 10,
        "below_7hz_fallback_threshold": throughput_hz < 7,
        "peak_torch_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2,
        "latencies_ms": latencies_ms,
        "measurement_note": (
            "Decoded static images replayed in one sequential stream. Latency includes "
            "preprocessing, transfer, generation and postprocessing; excludes image capture, "
            "network, model loading and JSON/PNG writes. This is not multi-camera or end-to-end latency."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--model", default="microsoft/Florence-2-base")
    parser.add_argument("--output", type=Path, default=Path("vlm_results"))
    parser.add_argument("--benchmark-seconds", type=float,
                        help="Replay the input images for this many measured seconds")
    parser.add_argument("--warmup-iterations", type=int, default=3,
                        help="Unmeasured calls before benchmarking (default: 3)")
    args = parser.parse_args()
    if args.benchmark_seconds is not None and (
        not math.isfinite(args.benchmark_seconds) or args.benchmark_seconds <= 0
    ):
        parser.error("--benchmark-seconds must be a positive finite number")
    if args.warmup_iterations < 0:
        parser.error("--warmup-iterations must be nonnegative")
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
    if args.benchmark_seconds is not None:
        summary = benchmark(model, processor, args.images, model_id,
                            args.benchmark_seconds, args.warmup_iterations, task)
        summary_path = args.output / "benchmark.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in summary.items()
                          if key != "latencies_ms"}, ensure_ascii=False, indent=2), flush=True)
        print(f"Benchmark saved to: {summary_path.resolve()}")
        return
    for path in args.images:
        with Image.open(path) as source:
            image = source.convert("RGB")
        torch.cuda.reset_peak_memory_stats()
        raw, parsed, generation_ms = detect_once(model, processor, image, task)
        result = {
            "image": str(path),
            "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "model": model_id, "task": task,
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
