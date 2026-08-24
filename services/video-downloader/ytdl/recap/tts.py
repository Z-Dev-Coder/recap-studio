"""
Narration: turning the recap script into spoken audio.

Gemini's TTS models take a style instruction in plain English prepended to the
text ("Say brightly and fast: ..."), which is how tone, pace and mood are
controlled -- there are no rate or pitch knobs. That makes the style box in the
UI the real control surface, so it is passed through verbatim.

The audio comes back as headerless 16-bit PCM, so every clip is wrapped into a
WAV before anything else touches it.
"""

from __future__ import annotations

import base64
import struct
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .gemini import API_ROOT, GeminiError

SAMPLE_RATE = 24000
DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
TTS_MODELS = (
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
)


@dataclass(frozen=True)
class Voice:
    name: str
    character: str
    note: str = ""


# Google's prebuilt set. The character words are Google's own descriptors --
# they are what the UI shows, because "Sulafat" tells nobody anything.
VOICES: tuple[Voice, ...] = (
    Voice("Zephyr", "Bright", "clean and light; good default for explainers"),
    Voice("Puck", "Upbeat", "energetic; suits comedy and fast cuts"),
    Voice("Charon", "Informative", "steady documentary read"),
    Voice("Kore", "Firm", "confident, neutral; reliable all-rounder"),
    Voice("Fenrir", "Excitable", "high energy; for action and reveals"),
    Voice("Leda", "Youthful", "younger, casual"),
    Voice("Orus", "Firm", "grounded and assured"),
    Voice("Aoede", "Breezy", "relaxed and warm"),
    Voice("Callirrhoe", "Easy-going", "unhurried, conversational"),
    Voice("Autonoe", "Bright", "clear and positive"),
    Voice("Enceladus", "Breathy", "intimate, close-mic feel"),
    Voice("Iapetus", "Clear", "very legible; good over busy footage"),
    Voice("Umbriel", "Easy-going", "soft and calm"),
    Voice("Algieba", "Smooth", "polished narration"),
    Voice("Despina", "Smooth", "even and pleasant"),
    Voice("Erinome", "Clear", "crisp and precise"),
    Voice("Algenib", "Gravelly", "textured, older-sounding"),
    Voice("Rasalgethi", "Informative", "teacherly"),
    Voice("Laomedeia", "Upbeat", "lively and quick"),
    Voice("Achernar", "Soft", "gentle, low intensity"),
    Voice("Alnilam", "Firm", "authoritative"),
    Voice("Schedar", "Even", "measured, unemotional"),
    Voice("Gacrux", "Mature", "older, settled"),
    Voice("Pulcherrima", "Forward", "leaning-in, persuasive"),
    Voice("Achird", "Friendly", "approachable and warm"),
    Voice("Zubenelgenubi", "Casual", "chatty, informal"),
    Voice("Vindemiatrix", "Gentle", "quiet and kind"),
    Voice("Sadachbia", "Lively", "animated"),
    Voice("Sadaltager", "Knowledgeable", "expert tone"),
    Voice("Sulafat", "Warm", "rich and welcoming"),
)

VOICE_NAMES = tuple(v.name for v in VOICES)

# What the preview says, so a voice can be judged on the language it will speak
SAMPLES = {
    "en": "Here is what happens next, and it is the part everyone remembers.",
    "my": "နောက်တစ်ခွင်မှာ "
          "ဘာတွေ ဖြစ်လာမယ်ဆိုတာ "
          "အားလုံး မှတ်မိနေတဲ့ "
          "အပိုင်းပါ။",
}


def wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw mono 16-bit PCM in a WAV header."""
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def speak(
    api_key: str,
    text: str,
    voice: str = "Kore",
    style: str = "",
    model: str = DEFAULT_MODEL,
    timeout: int = 180,
    retries: int = 4,
    cancel=None,
) -> bytes:
    """
    Speak `text` and return WAV bytes.

    `style` is a plain instruction like "warm and unhurried" -- it is folded
    into the prompt because that is the only tone control these models have.

    The free tier allows only a handful of TTS calls a minute, and a recap is
    a dozen lines, so hitting the limit is normal rather than exceptional: a
    429 waits and tries again instead of failing the whole narration.
    """
    if not api_key:
        raise GeminiError("No Gemini API key set. Add one in Settings.")
    text = (text or "").strip()
    if not text:
        raise GeminiError("there is nothing to say")

    prompt = f"Say this {style.strip()}: {text}" if style.strip() else text
    last_error = ""
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or "Kore"}}
            },
        },
    }
    url = f"{API_ROOT}/models/{model}:generateContent"

    for attempt in range(max(1, retries)):
        if cancel is not None and cancel.is_set():
            from .media import Cancelled
            raise Cancelled()
        try:
            resp = requests.post(url, params={"key": api_key}, json=body, timeout=timeout)
        except requests.RequestException as exc:
            raise GeminiError(f"Could not reach Gemini TTS: {exc}") from exc

        if resp.status_code == 429:
            # Google says how long to wait; only guess when it does not
            from .gemini import retry_after
            wait = retry_after(resp) or 8 * (2 ** attempt)
            last_error = f"rate limited, resets in ~{int(wait)}s"
            if attempt == retries - 1:
                break
            waited = 0.0
            while waited < wait:
                if cancel is not None and cancel.is_set():
                    from .media import Cancelled
                    raise Cancelled()
                time.sleep(0.5)
                waited += 0.5
            continue

        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except ValueError:
                detail = resp.text[:200]
            raise GeminiError(f"TTS failed ({resp.status_code}): {detail}")

        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, ValueError) as exc:
            raise GeminiError("TTS returned no audio") from exc

        spoken = None
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                spoken = inline["data"]
                break
        if spoken:
            return wav(base64.b64decode(spoken))

        # a 200 that carries no audio is transient: the model occasionally
        # answers with an empty or text part. Retry rather than lose the line.
        last_error = "no audio in the reply"
        said = " ".join(p.get("text", "") for p in parts).strip()
        if said:
            last_error = f"model replied with text instead of audio: {said[:120]}"
        if attempt < retries - 1:
            time.sleep(4)
            continue
        raise GeminiError("TTS returned no audio -- " + last_error)

    raise GeminiError(
        "Gemini's free tier limits how many lines can be spoken per minute, and "
        "it is still refusing after several waits. Leave it a minute and press "
        "Regenerate -- finished lines are kept."
        + (f" ({last_error})" if last_error else "")
    )


def preview(api_key: str, voice: str, style: str = "", lang: str = "en",
            model: str = DEFAULT_MODEL, text: str = "") -> bytes:
    """A short sample, in the language the narration will actually be in."""
    return speak(api_key, text or SAMPLES.get(lang, SAMPLES["en"]), voice, style, model)


# ------------------------------------------------------------------ advice

_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "voice_en": {"type": "string"},
        "voice_my": {"type": "string"},
        "style": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["voice_en", "voice_my", "style", "reason"],
}


def suggest(client, title: str, description: str, beats: list[dict]) -> dict:
    """
    Ask for a voice and style that suit this particular video.

    The model sees the same script the voice will read, so the advice is about
    this video rather than a generic "use a friendly voice".
    """
    catalogue = "\n".join(f"- {v.name}: {v.character}, {v.note}" for v in VOICES)
    sample = "\n".join(f"- {b.get('en', '')}" for b in (beats or [])[:6])

    prompt = "\n\n".join([
        "Pick the narration voice for this video recap.",
        f"Title: {title or '(unknown)'}",
        f"Description: {(description or '')[:600]}",
        "Lines the voice will read:\n" + (sample or "(none yet)"),
        "Available voices:\n" + catalogue,
        "Return: \"voice_en\" and \"voice_my\" (voice names from the list -- they "
        "may be the same, but pick per language if one suits Burmese better), "
        "\"style\" as a short instruction the TTS will follow such as "
        "'brightly and quickly, like a comedy trailer' or 'calmly and clearly, "
        "like a documentary', and \"reason\" as one sentence on why this fits "
        "this video. Match the energy of the content: a cartoon is not a "
        "lecture, and a tutorial is not a hype reel.",
    ])

    data = client.generate_json(prompt, _SUGGEST_SCHEMA, temperature=0.6)
    valid = set(VOICE_NAMES)
    return {
        "voice_en": data.get("voice_en") if data.get("voice_en") in valid else "Kore",
        "voice_my": data.get("voice_my") if data.get("voice_my") in valid else "Kore",
        "style": (data.get("style") or "").strip(),
        "reason": (data.get("reason") or "").strip(),
    }


# ------------------------------------------------------------------ track

def custom_path(out_dir: Path, index: int, lang: str) -> Path:
    """Where a user-supplied take for one line lives."""
    return out_dir / f"line_{index:03d}_{lang}_custom.wav"


def narrate(
    api_key: str,
    timeline: list[dict],
    out_dir: Path,
    lang: str = "en",
    voice: str = "Kore",
    style: str = "",
    model: str = DEFAULT_MODEL,
    on_progress=None,
    cancel=None,
    min_interval: float = 20.0,
    label: str = "",
    engine: str = "gemini",
    reference_audio=None,
    reference_text: str = "",
    local_model: str = "",
) -> list[dict]:
    """
    Speak every line of the recap, one clip per beat.

    Each clip is saved beside the project and tagged with the moment in the
    recap it belongs to, so the mux step can lay them onto the timeline
    without asking the model anything again.

    Lines are spaced out by `min_interval` seconds because the free tier allows
    only about three TTS calls a minute -- firing a dozen at once simply earns
    a wall of 429s. A line the user has recorded or uploaded themselves is used
    as-is and costs no quota at all.
    """
    from .media import Cancelled

    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[dict] = []
    rows = [r for r in timeline if (r.get(lang) or "").strip()]

    last_call = 0.0
    for i, row in enumerate(rows):
        if cancel is not None and cancel.is_set():
            raise Cancelled()

        mine = custom_path(out_dir, i, lang)
        if mine.exists() and mine.stat().st_size > 1000:
            made.append({"file": mine.name, "at": float(row.get("recap_start") or 0),
                         "text": row[lang], "custom": True})
            if on_progress:
                on_progress(i + 1, len(rows))
            continue

        dest = out_dir / f"line_{i:03d}_{lang}.wav"
        if dest.exists() and dest.stat().st_size > 1000:
            # a retry after a rate limit resumes instead of paying for it twice
            made.append({"file": dest.name, "at": float(row.get("recap_start") or 0),
                         "text": row[lang]})
            if on_progress:
                on_progress(i + 1, len(rows))
            continue
        if engine == "voxcpm":
            # nothing to pace: it runs here, and there is no quota to respect
            from . import localtts
            # the clip this line plays over is the budget: narration that
            # outruns its clip is narration nobody hears in place
            clip_seconds = max(
                0.0,
                float(row.get("recap_end") or 0) - float(row.get("recap_start") or 0),
            )
            audio = localtts.speak(
                row[lang],
                reference_audio=reference_audio,
                reference_text=reference_text,
                model_id=local_model or localtts.DEFAULT_MODEL,
                cancel=cancel,
                max_seconds=clip_seconds,
            )
        else:
            # keep under the per-minute ceiling instead of colliding with it
            gap = min_interval - (time.monotonic() - last_call)
            while gap > 0 and last_call:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                time.sleep(min(0.5, gap))
                gap = min_interval - (time.monotonic() - last_call)

            audio = speak(api_key, row[lang], voice, style, model, cancel=cancel)
        last_call = time.monotonic()
        dest.write_bytes(audio)
        made.append({
            "file": dest.name,
            "at": float(row.get("recap_start") or 0),
            "text": row[lang],
        })
        if on_progress:
            on_progress(i + 1, len(rows))
    return made
