"""Reusable OpenVLA model runtime with lazy heavyweight imports."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OpenVlaDependencies:
    numpy: Any
    torch: Any
    image: Any
    auto_model: Any
    auto_processor: Any


def load_dependencies() -> OpenVlaDependencies:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModelForVision2Seq, AutoProcessor

    return OpenVlaDependencies(np, torch, Image, AutoModelForVision2Seq, AutoProcessor)


class OpenVlaInferenceEngine:
    """Load OpenVLA once and predict multiple image/instruction steps."""

    def __init__(
        self,
        model_id: str = "openvla/openvla-7b",
        *,
        unnorm_key: str = "bridge_orig",
        device: str = "cuda:0",
        dependencies: OpenVlaDependencies | None = None,
    ) -> None:
        self.model_id = model_id
        self.unnorm_key = unnorm_key
        self.device = device
        self.dependencies = dependencies or load_dependencies()
        deps = self.dependencies
        if device.startswith("cuda") and not deps.torch.cuda.is_available():
            raise RuntimeError("CUDA GPU unavailable. Run on an allocated GPU compute node.")

        self.processor = deps.auto_processor.from_pretrained(model_id, trust_remote_code=True)
        self.model = deps.auto_model.from_pretrained(
            model_id,
            torch_dtype=deps.torch.bfloat16,
            low_cpu_mem_usage=True,
            trust_remote_code=True,
        ).to(device)
        self.model.eval()

    def predict(self, image_path: str | Path, instruction: str) -> list[float]:
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {path}")
        normalized_instruction = instruction.strip().lower()
        if not normalized_instruction:
            raise ValueError("instruction must not be blank")

        deps = self.dependencies
        image = deps.image.open(path).convert("RGB")
        prompt = f"In: What action should the robot take to {normalized_instruction}?\nOut:"
        inputs = self.processor(prompt, image).to(self.device, dtype=deps.torch.bfloat16)
        with deps.torch.inference_mode():
            action = self.model.predict_action(
                **inputs,
                unnorm_key=self.unnorm_key,
                do_sample=False,
            )
        vector = deps.numpy.asarray(action, dtype=deps.numpy.float32).reshape(-1)
        if vector.size != 7 or not deps.numpy.isfinite(vector).all():
            raise RuntimeError(f"Expected finite 7-D action, got shape={vector.shape}: {vector!r}")
        return [float(value) for value in vector]
