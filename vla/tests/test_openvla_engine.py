from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from openvla_engine import OpenVlaDependencies, OpenVlaInferenceEngine


class FakeArray:
    def __init__(self, values):
        self.values = list(values)
        self.size = len(self.values)
        self.shape = (len(self.values),)

    def reshape(self, _size):
        return self

    def __iter__(self):
        return iter(self.values)

    def __repr__(self):
        return repr(self.values)


class FakeNumpy:
    float32 = "float32"

    @staticmethod
    def asarray(values, dtype=None):
        return FakeArray(values)

    @staticmethod
    def isfinite(values):
        return SimpleNamespace(all=lambda: all(value == value for value in values.values))


class FakeInputs(dict):
    def to(self, device, dtype=None):
        self["moved_to"] = (device, dtype)
        return self


class FakeProcessor:
    calls = []

    @classmethod
    def from_pretrained(cls, model_id, **kwargs):
        cls.calls.append((model_id, kwargs))
        return cls()

    def __call__(self, prompt, image):
        return FakeInputs(prompt=prompt, image=image)


class FakeModel:
    action = [0.0] * 7
    loads = 0

    @classmethod
    def from_pretrained(cls, model_id, **kwargs):
        cls.loads += 1
        return cls()

    def to(self, device):
        self.device = device
        return self

    def eval(self):
        return None

    def predict_action(self, **kwargs):
        self.last_predict = kwargs
        return self.action


class FakeImage:
    @classmethod
    def open(cls, path):
        return cls()

    def convert(self, mode):
        self.mode = mode
        return self


def dependencies(cuda=True):
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda),
        bfloat16="bfloat16",
        inference_mode=nullcontext,
    )
    return OpenVlaDependencies(FakeNumpy, torch, FakeImage, FakeModel, FakeProcessor)


def test_engine_loads_model_once_and_predicts_repeatedly(tmp_path):
    FakeModel.loads = 0
    image = tmp_path / "frame.png"
    image.write_bytes(b"fake")
    engine = OpenVlaInferenceEngine(dependencies=dependencies())

    first = engine.predict(image, " Move toward the red box ")
    second = engine.predict(image, "close the gripper")

    assert first == [0.0] * 7 and second == [0.0] * 7
    assert FakeModel.loads == 1
    assert engine.model.last_predict["unnorm_key"] == "bridge_orig"


def test_engine_rejects_missing_gpu():
    with pytest.raises(RuntimeError, match="CUDA"):
        OpenVlaInferenceEngine(dependencies=dependencies(cuda=False))


def test_engine_rejects_invalid_action_dimension(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"fake")
    FakeModel.action = [0.0] * 6
    engine = OpenVlaInferenceEngine(dependencies=dependencies())
    try:
        with pytest.raises(RuntimeError, match="7-D"):
            engine.predict(image, "move")
    finally:
        FakeModel.action = [0.0] * 7


def test_engine_rejects_blank_instruction(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"fake")
    engine = OpenVlaInferenceEngine(dependencies=dependencies())

    with pytest.raises(ValueError, match="blank"):
        engine.predict(image, "   ")
