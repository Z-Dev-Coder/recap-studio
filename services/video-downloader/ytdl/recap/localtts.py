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

# VoxCPM2 is the only one of these that speaks anything beyond Chinese and
# English. The smaller models are three times faster and fit a 6GB card
# comfortably, but handed Burmese they produce confident Chinese-sounding
# nonsense rather than failing, so size must never be chosen over language.
DEFAULT_MODEL = "openbmb/VoxCPM2"

MODELS = {
    "openbmb/VoxCPM2": "2B - 4.6GB - 30 languages incl. Burmese. The only one for Burmese.",
    "openbmb/VoxCPM-0.5B": "0.5B - 1.5GB - ENGLISH AND CHINESE ONLY, fastest",
    "openbmb/VoxCPM1.5": "1.5 - 1.8GB - ENGLISH AND CHINESE ONLY",
}

# Anything not in here will be spoken by a model that was never trained on it.
MULTILINGUAL_MODELS = {"openbmb/VoxCPM2"}

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
        "The first run downloads the weights: 1.5GB for the 0.5B default,\n"
        "4.6GB for VoxCPM2.\n"
        "On CPU this runs ~100x slower than realtime, which is not usable; a\n"
        "CUDA build of PyTorch brings it to about a third of realtime. Match\n"
        "the channel to the torch version -- cu126 carries 2.13:\n"
        "  venv\\Scripts\\python.exe -m pip install --force-reinstall torch "
        "torchaudio --index-url https://download.pytorch.org/whl/cu126"
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
        note = f"{name}, {gb:.0f}GB"
        # measured here on a 6GB GTX 1660 Ti: the 0.5B model narrates at about
        # a third of realtime, which is usable. VoxCPM2 wants a bigger card.
        if gb < 7.5:
            note += " -- good for the 0.5B model; VoxCPM2 wants 8GB"
    else:
        note = ("CPU only -- around 100x slower than realtime, which is not "
                "practical. A CUDA build of PyTorch fixes it.")
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

            # The denoiser is a separate model pulled through modelscope, and
            # it only exists to clean up reference audio before cloning. Plain
            # narration never uses it, so it is left off rather than dragging
            # in another dependency and another download.
            try:
                _model = VoxCPM.from_pretrained(model_id, load_denoiser=False)
            except TypeError:
                _model = VoxCPM.from_pretrained(model_id)
            except Exception as exc:      # noqa: BLE001 - surfaced verbatim
                raise LocalTTSError(f"could not load {model_id}: {exc}") from exc
            _model_id = model_id
    return _model


def _to_wav(audio, rate: int) -> bytes:
    """Model output straight to a WAV, for a single ungrouped clip."""
    return wav_header(_pcm(audio), rate)


def _pcm(audio) -> bytes:
    """Model output as raw 16-bit PCM, ready to be joined with other chunks."""
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
    return data.tobytes()


def wav_header(pcm: bytes, rate: int) -> bytes:
    """Wrap raw mono 16-bit PCM in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return buf.getvalue()



# Burmese ends sentences with U+104B and separates clauses with U+104A; the
# ASCII marks appear in mixed text and in the English narration.
_BREAKS = "\u104b\u104a.!?;\n"

# VoxCPM keeps text and generated audio in one KV cache, so what overflows is
# the pair, not the sentence. A 324-character line came through while a
# 258-character one did not -- the difference was how much audio each produced.
# Chunking well under the limit is the only reliable way to stay inside it.
CHUNK_CHARS = 140

# Measured on this model: 60 frames produced 4.80s of audio and 120 produced
# 9.60s, exactly. VoxCPM's own runaway guard is scaled to the TOKEN count, and
# Burmese tokenizes into far more tokens than Latin text, so that guard barely
# binds -- an eleven word line came back as 30 seconds of continuous speech.
# Capping in frames, derived from the seconds the clip actually lasts, is the
# only limit that means the same thing in every language.
FRAMES_PER_SECOND = 12.5


def split_for_speech(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """
    Break a line into pieces small enough to speak in one pass.

    Splits on sentence and clause punctuation, keeping the mark with the
    phrase it ends, then packs those phrases into chunks under `limit`. A
    single phrase longer than the limit is cut on whitespace rather than
    mid-word, and only cut blindly if there is no whitespace at all.
    """
    text = (text or "").strip()
    if not text:
        return []

    phrases, current = [], ""
    for ch in text:
        current += ch
        if ch in _BREAKS:
            if current.strip():
                phrases.append(current.strip())
            current = ""
    if current.strip():
        phrases.append(current.strip())

    pieces = []
    for phrase in phrases:
        while len(phrase) > limit:
            cut = phrase.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit          # no space to break on: cut where we must
            pieces.append(phrase[:cut].strip())
            phrase = phrase[cut:].strip()
        if phrase:
            pieces.append(phrase)

    chunks, current = [], ""
    for piece in pieces:
        candidate = (current + " " + piece).strip() if current else piece
        if len(candidate) > limit and current:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _join_pcm(parts: list[bytes], rate: int, gap: float = 0.18) -> bytes:
    """Concatenate spoken chunks with a short breath between them."""
    silence = b"\x00\x00" * int(rate * gap)
    joined = b""
    for i, part in enumerate(parts):
        if i:
            joined += silence
        joined += part
    return joined


def speak(
    text: str,
    reference_audio: Path | None = None,
    reference_text: str = "",
    model_id: str = DEFAULT_MODEL,
    cancel=None,
    max_seconds: float = 0.0,
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

    chunks = split_for_speech(text)
    rate = sample_rate(model)
    spoken: list[bytes] = []

    # Share the budget across the chunks, with headroom so a natural reading
    # is never cut off mid-word -- the cap exists to stop a runaway, not to
    # trim good speech.
    per_chunk = 0
    if max_seconds > 0 and chunks:
        per_chunk = max(24, int((max_seconds / len(chunks)) * 1.25 * FRAMES_PER_SECOND))

    for chunk in chunks:
        if cancel is not None and cancel.is_set():
            from .media import Cancelled
            raise Cancelled()
        kwargs["text"] = chunk
        if per_chunk:
            kwargs["max_len"] = per_chunk
        try:
            audio = model.generate(**kwargs)
        except TypeError:
            # older signatures take the reference differently; retry plain
            # rather than failing the whole narration over a keyword name
            audio = model.generate(text=chunk)
        except Exception as exc:      # noqa: BLE001
            raise LocalTTSError(
                f"VoxCPM could not speak this part -- {str(exc)[:120]} "
                f"(text was {len(chunk)} characters)"
            ) from exc
        spoken.append(_pcm(audio))

    if not spoken:
        raise LocalTTSError("there was nothing to say")
    return wav_header(_join_pcm(spoken, rate), rate)


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
