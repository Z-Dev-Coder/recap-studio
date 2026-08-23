"""
Local narration with VoxCPM2, as an alternative to Gemini TTS.

Gemini's free tier allows roughly three spoken lines a minute, which is the
one place a recap reliably hits a wall: a dozen lines means minutes of waiting
and the occasional refusal. VoxCPM2 runs on this machine with no quota at all,
supports Burmese, and is Apache-2.0.

It is optional and imported lazily: the package pulls in PyTorch and a ~4GB
model, which is not something to inflict on someone who is happy with the
cloud voice.
"""

from __future__ import annotations

import io
import struct
import sys
import threading
import types
import wave
from pathlib import Path

# One model instance, loaded once and shared. Loading takes tens of seconds and
# several GB, so doing it per line would be far slower than the synthesis.
_model = None
_model_lock = threading.Lock()
_model_id = ""

DEFAULT_MODEL = "openbmb/VoxCPM2"

# Only a fallback: the real rate is read off the loaded model, because the
# model decides it and a wrong header plays the narration at the wrong speed.
SAMPLE_RATE = 24000


class LocalTTSError(RuntimeError):
    """VoxCPM is missing or refused to speak, phrased for the person reading."""


def available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("voxcpm") is not None
    except (ImportError, ValueError):
        return False


def install_hint() -> str:
    return (
        "Local narration needs VoxCPM. Install it once with:\n"
        r"  venv\Scripts\python.exe -m pip install voxcpm"
        "\n"
        "The first run downloads about 4GB of model weights.\n"
        "A CUDA build of PyTorch makes it several times faster:\n"
        "  venv\\Scripts\\python.exe -m pip install torch --index-url "
        "https://download.pytorch.org/whl/cu121"
    )


_device_note = ""


def device_note() -> str:
    """
    What it will actually run on, so nobody is surprised by the speed.

    Memoised: importing torch takes seconds, and this is asked for on every
    page load. Without the cache the whole UI waits on it.
    """
    global _device_note
    if _device_note:
        return _device_note
    try:
        import torch
    except ImportError:
        _device_note = "PyTorch is not installed"
        return _device_note
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        note = f"{name} ({gb:.0f}GB)"
        if gb < 7.5:
            note += " -- below the 8GB VoxCPM asks for, so it may fall back to CPU"
    else:
        note = "CPU only -- expect roughly 15-30 seconds per line"
    _device_note = note
    return note


def _stand_in_for_wetext() -> None:
    """
    Let VoxCPM import without wetext.

    VoxCPM's text normaliser imports wetext, which drags in kaldifst -- a
    package that publishes no wheel for this Python and cannot be built here
    without a C++ toolchain. wetext is used in exactly one place, to expand
    Chinese and English numbers into words. Burmese narration never needs it,
    so rather than lose the whole engine over a number formatter, a
    pass-through stands in.

    The cost is that English narration will read "1990" as digits rather than
    "nineteen ninety". Nothing else changes.
    """
    if "wetext" in sys.modules:
        return
    try:
        import wetext  # noqa: F401
        return
    except ImportError:
        pass

    shim = types.ModuleType("wetext")

    class Normalizer:
        def __init__(self, *args, **kwargs):
            pass

        def normalize(self, text):
            return text

        def __call__(self, text):
            return text

    shim.Normalizer = Normalizer
    shim.__doc__ = "pass-through stand-in installed by Recap Studio"
    sys.modules["wetext"] = shim


def load(model_id: str = DEFAULT_MODEL):
    global _model, _model_id
    if not available():
        raise LocalTTSError(install_hint())
    with _model_lock:
        if _model is None or _model_id != model_id:
            _stand_in_for_wetext()
            from voxcpm import VoxCPM
            try:
                _model = VoxCPM.from_pretrained(model_id)
            except Exception as exc:      # noqa: BLE001 - surfaced verbatim
                raise LocalTTSError(f"could not load {model_id}: {exc}") from exc
            _model_id = model_id
    return _model


def _to_wav(audio, rate: int) -> bytes:
    """
    Normalise whatever the model returns into 16-bit PCM WAV.

    VoxCPM hands back float samples in -1..1; writing those into a 16-bit WAV
    without scaling produces silence, which is a confusing way to fail.
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise LocalTTSError("numpy is required to save the audio") from exc

    data = np.asarray(audio).squeeze()
    if data.dtype.kind == "f":
        peak = float(np.max(np.abs(data))) or 1.0
        if peak > 1.0:
            data = data / peak
        data = (data * 32767.0).astype("<i2")
    elif data.dtype != np.dtype("<i2"):
        data = data.astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(data.tobytes())
    return buf.getvalue()


def speak(
    text: str,
    reference_audio: Path | None = None,
    reference_text: str = "",
    model_id: str = DEFAULT_MODEL,
    cancel=None,
    **_ignored,
) -> bytes:
    """
    Speak `text` locally and return WAV bytes.

    `reference_audio` turns this into voice cloning: a few seconds of someone
    talking is enough for VoxCPM to narrate in that voice, which is the reason
    to run it locally rather than for the quota alone.
    """
    text = (text or "").strip()
    if not text:
        raise LocalTTSError("there is nothing to say")

    if cancel is not None and cancel.is_set():
        from .media import Cancelled
        raise Cancelled()

    model = load(model_id)
    kwargs = {"text": text}
    if reference_audio and Path(reference_audio).exists():
        kwargs["prompt_wav_path"] = str(reference_audio)
        if reference_text.strip():
            kwargs["prompt_text"] = reference_text.strip()

    try:
        audio = model.generate(**kwargs)
    except TypeError:
        # older signatures take the reference differently; retry plain rather
        # than failing the whole narration over a keyword name
        audio = model.generate(text=text)
    except Exception as exc:      # noqa: BLE001
        raise LocalTTSError(f"VoxCPM could not speak that line: {exc}") from exc

    return _to_wav(audio, sample_rate(model))


def sample_rate(model) -> int:
    """
    The rate the model actually produced, read from the model itself.

    Guessing here is not harmless: writing a 24kHz header onto 16kHz samples
    plays the narration fast and high, which sounds like a broken voice rather
    than a wrong number.
    """
    for owner in (getattr(model, "tts_model", None), model):
        rate = getattr(owner, "sample_rate", None)
        if isinstance(rate, (int, float)) and rate > 1000:
            return int(rate)
    return SAMPLE_RATE
