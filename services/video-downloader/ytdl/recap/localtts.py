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

# Only VoxCPM2 is offered. The 0.5B and 1.5 models are a third the size and
# three times faster, but they are trained on Chinese and English alone, and
# given Burmese they do not fail -- they speak fluent Chinese-sounding nonsense.
# A faster model that silently produces the wrong language is not a choice
# worth offering, so the list has one entry and cannot be got wrong.
MODELS = {
    "openbmb/VoxCPM2": "2B - 4.6GB - 30 languages including Burmese",
}

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
        # VoxCPM2 is documented as wanting 8GB, but it was measured fitting a
        # 6GB card here using 6.4GB of it -- tight rather than impossible. The
        # warning stays, because anything else claiming VRAM mid-run will
        # break it.
        if gb < 7.5:
            note += " -- VoxCPM2 fits, but only just; close other GPU work"
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

    # A project saved before the smaller models were dropped may still name
    # one. Honouring that would download 1.5GB to speak the wrong language.
    if model_id not in MODELS:
        model_id = DEFAULT_MODEL
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

# Measured across real generated narration: 11.8-17.9, median 14.5.
MY_CHARS_PER_SECOND = 14.5


def _plausible_reference(clip: Path, text: str, slack: float = 2.2) -> bool:
    """
    Whether a reference clip really says what it claims to.

    Burmese runs about 14.5 characters a second. A clip far longer than its
    text says more than the text does; one far shorter says less. Either way
    pairing them tells the model something untrue.
    """
    if not (text or "").strip():
        return True        # no claim about what it says, so nothing to contradict
    try:
        with wave.open(str(clip)) as w:
            seconds = w.getnframes() / float(w.getframerate() or 1)
    except Exception:      # noqa: BLE001 - unreadable is not a reason to refuse
        return True
    expected = max(0.5, len(text) / MY_CHARS_PER_SECOND)
    return expected / slack <= seconds <= expected * slack


def trim_silence(pcm: bytes, rate: int, floor: float = 0.006,
                 keep: float = 0.06) -> bytes:
    """
    Cut the silence the model leaves at either end of a clip.

    VoxCPM pads its output -- measured on real narration, up to 1.4 seconds
    before the first word and a fifth of a second after the last. That silence
    was being measured as part of the line, so the cut allocated footage for
    it, and the app's own lead-in was then added on top. The picture changed,
    then nothing happened, twice over.

    A little is kept at each end so the speech does not begin on the very first
    sample, which sounds clipped.
    """
    import numpy as np

    audio = np.frombuffer(pcm, dtype=np.int16)
    if audio.size == 0:
        return pcm
    loud = np.abs(audio.astype(np.float32) / 32768.0) > floor
    if not loud.any():
        return pcm          # nothing but silence: leave it alone rather than empty

    margin = int(keep * rate)
    first = max(0, int(np.argmax(loud)) - margin)
    last = min(audio.size, audio.size - int(np.argmax(loud[::-1])) + margin)
    return audio[first:last].tobytes()


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
    voice_anchor: Path | None = None,
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

    # VoxCPM has no seed: given no reference clip it invents a fresh voice on
    # every call. Each chunk is its own call, so a line split into three came
    # back in three different voices, changing speaker mid-sentence. The first
    # chunk is therefore written out and handed to the rest as their reference,
    # which is also what keeps one line sounding like the line before it.
    anchor = None
    anchor_text = ""
    if reference_audio and Path(reference_audio).exists():
        anchor = Path(reference_audio)
        anchor_text = (reference_text or "").strip()
        # A reference is handed to the model as "this audio says exactly this".
        # When it does not -- a clip that rambled past its line and was adopted
        # anyway -- the model is being told something false about its own
        # conditioning, and what comes back is neither the voice nor the
        # language asked for. Nine seconds of audio for a two-second sentence
        # is not that sentence, so the claim is dropped and the clip is used as
        # a voice to copy rather than as a transcript to trust.
        if anchor_text and not _plausible_reference(anchor, anchor_text):
            anchor_text = ""
        # an anchor written by an earlier line keeps its text in a sidecar, so
        # the closer prompt mode survives across separate calls
        if not anchor_text:
            sidecar = anchor.with_suffix(".txt")
            if sidecar.exists():
                try:
                    anchor_text = sidecar.read_text(encoding="utf-8").strip()
                except OSError:
                    anchor_text = ""

    # Share the budget across the chunks, with headroom so a natural reading
    # is never cut off mid-word -- the cap exists to stop a runaway, not to
    # trim good speech.
    # How long each chunk is allowed to run.
    #
    # This used to divide the CLIP length between the chunks, which meant the
    # allowance had nothing to do with the words. A narrator script -- twenty
    # beats across eight minutes -- gave each chunk about ten seconds for text
    # that takes seven to say, and the model filled the difference: invented
    # Burmese, and drift into Chinese, which VoxCPM has far more of. A recap
    # script over the same video had forty-two shorter beats, almost no slack,
    # and did not do it.
    #
    # The words decide it now. Burmese runs about 14.5 characters a second, so
    # a chunk gets what its own text needs plus a margin for the model's
    # phrasing -- never room to invent a sentence. The clip length still caps
    # it, so a line cannot outrun the footage it plays over.
    def room_for(text: str) -> int:
        natural = len(text) / MY_CHARS_PER_SECOND
        return max(24, int(natural * 1.35 * FRAMES_PER_SECOND))

    ceiling = 0
    if max_seconds > 0 and chunks:
        ceiling = max(24, int((max_seconds / len(chunks)) * 1.25 * FRAMES_PER_SECOND))

    for chunk in chunks:
        if cancel is not None and cancel.is_set():
            from .media import Cancelled
            raise Cancelled()
        kwargs["text"] = chunk
        per_chunk = room_for(chunk)
        if ceiling:
            per_chunk = min(per_chunk, ceiling)
        kwargs["max_len"] = per_chunk
        kwargs.pop("prompt_wav_path", None)
        kwargs.pop("prompt_text", None)
        kwargs.pop("reference_wav_path", None)
        if anchor is not None and anchor.exists():
            if anchor_text:
                # prompt mode: the model is told what the reference says, which
                # copies the voice most closely. Both or neither -- VoxCPM
                # rejects a prompt_wav_path with no prompt_text.
                kwargs["prompt_wav_path"] = str(anchor)
                kwargs["prompt_text"] = anchor_text
            else:
                # plain voice cloning, no transcript needed
                kwargs["reference_wav_path"] = str(anchor)
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
        piece = trim_silence(_pcm(audio), rate)
        spoken.append(piece)

        # the first thing spoken becomes the voice everything after it copies
        if anchor is None and voice_anchor is not None:
            try:
                voice_anchor.parent.mkdir(parents=True, exist_ok=True)
                voice_anchor.write_bytes(wav_header(piece, rate))
                voice_anchor.with_suffix(".txt").write_text(chunk, encoding="utf-8")
                anchor = voice_anchor
                anchor_text = chunk
            except OSError:
                pass       # no anchor is worse, but not worth failing over

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
