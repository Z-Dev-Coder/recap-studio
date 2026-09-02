"""
Reading a video that has nothing to say.

Silent cartoons, music videos, gameplay, wordless documentary footage: the
story is shown rather than spoken, so there is no speech to transcribe and
Whisper correctly returns nothing. Everything downstream -- chapter planning,
story analysis, script writing -- consumes Cue(start, end, text), so the fix
is not another pipeline but another source for the same rows: sample frames,
have a model that can see describe them, and hand back Cues.

The description is written in English regardless of the finished language.
Not because English is special, but because it is an intermediate: a compact,
literal account of what happens, which the Burmese stage then writes FROM.
Asking one model to look at a picture and produce publishable Burmese in one
step gets worse at both jobs than doing them in order.

Frames are batched, several per request. One request per frame would be a
hundred requests for an eight-minute cartoon, which no free tier survives and
which also loses the thing that makes a description useful -- a frame means
little alone, and a lot beside the ones either side of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .media import MediaError, frame_at, probe
from .transcript import Cue

# How often to look. A cartoon gag runs three to six seconds, so four is close
# enough to catch each beat without paying for the same joke twice.
EVERY = 4.0

# Frames per request. Enough context for the model to see what changed between
# them; few enough that one failure does not cost the whole video.
BATCH = 10

# Above this the sampling stretches instead, because a long video at a fixed
# interval is a bill nobody agreed to.
MAX_FRAMES = 180


@dataclass
class Shot:
    """One sampled moment: when it was, and the frame itself."""

    at: float
    blob: bytes


_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "at": {"type": "number"},
                    "text": {"type": "string"},
                },
                "required": ["at", "text"],
            },
        }
    },
    "required": ["moments"],
}

_PROMPT = """You are watching {n} frames sampled from a video, in order.
Their timestamps in the video are: {stamps}.

Describe what is HAPPENING at each frame, as one plain English sentence.

Write what a viewer sees, not what a catalogue would say:
- name the characters or objects doing something, and what they are doing
- prefer the action over the setting: "Mickey drops the bolt down the shaft"
  beats "an interior of a clock tower"
- if a frame continues the previous one, say what CHANGED rather than
  repeating the scene
- no interpretation, no mood, no guessing at dialogue that is not shown
- if a frame is a title card, a logo or a black frame, say exactly that

Return one entry per frame, with its timestamp in `at`."""


def sample(src: Path, work: Path, duration: float, every: float = EVERY,
           cancel=None) -> list[Shot]:
    """
    Take frames across the whole video at a steady interval.

    Steady rather than scene-detected on purpose: scene detection on animation
    fires on every whip-pan and misses slow action entirely, and the intervals
    it returns are unpredictable to price. An even sweep is worse at finding
    cuts and much better at covering the video, which is what a recap needs.
    """
    if duration <= 0:
        raise MediaError("that video has no length to sample")

    step = max(every, duration / MAX_FRAMES)
    work.mkdir(parents=True, exist_ok=True)

    shots: list[Shot] = []
    at = step / 2                      # mid-interval, not on the cut
    i = 0
    while at < duration:
        if cancel is not None and cancel.is_set():
            break
        dest = work / f"look_{i:04d}.jpg"
        try:
            frame_at(src, at, dest, width=640, cancel=cancel)
        except MediaError:
            at += step
            i += 1
            continue                   # one unreadable frame is not a failure
        if dest.exists():
            shots.append(Shot(at=round(at, 2), blob=dest.read_bytes()))
        at += step
        i += 1
    return shots


def describe(client, shots: list[Shot], on_progress=None, cancel=None) -> list[Cue]:
    """
    Turn sampled frames into timed English descriptions.

    The rows come back as Cues spanning frame to frame, so a description
    covers the stretch it was sampled from rather than an instant.
    """
    if not shots:
        return []

    said: dict[float, str] = {}
    batches = [shots[i:i + BATCH] for i in range(0, len(shots), BATCH)]

    for n, batch in enumerate(batches, 1):
        if cancel is not None and cancel.is_set():
            break
        stamps = ", ".join(f"{s.at:.1f}s" for s in batch)
        prompt = _PROMPT.format(n=len(batch), stamps=stamps)
        try:
            out = client.generate_json(
                prompt, _SCHEMA, temperature=0.2,
                images=[("image/jpeg", s.blob) for s in batch],
                cancel=cancel,
                max_tokens=len(batch) * 120,
            )
        except Exception:      # noqa: BLE001
            # A batch that fails leaves a gap in the description rather than
            # ending the read. Fifteen minutes of video is worth more than the
            # forty seconds one refused request covers.
            if on_progress:
                on_progress(n, len(batches))
            continue

        for row in out.get("moments") or []:
            try:
                at = float(row.get("at"))
            except (TypeError, ValueError):
                continue
            text = " ".join(str(row.get("text") or "").split())
            if text:
                said[_nearest(at, batch)] = text
        if on_progress:
            on_progress(n, len(batches))

    return _to_cues(shots, said)


def _nearest(at: float, batch: list[Shot]) -> float:
    """Models round timestamps; snap an answer back to the frame it describes."""
    return min((s.at for s in batch), key=lambda t: abs(t - at))


def _to_cues(shots: list[Shot], said: dict[float, str]) -> list[Cue]:
    """Each description covers the stretch from its frame to the next one."""
    cues: list[Cue] = []
    for shot, nxt in zip(shots, shots[1:] + [None]):
        text = said.get(shot.at)
        if not text:
            continue                   # a frame nobody described is a gap, not a blank
        end = nxt.at if nxt else shot.at + EVERY
        cues.append(Cue(start=shot.at, end=max(end, shot.at + 0.5), text=text))
    return cues


def read(src: Path, work: Path, client, duration: float = 0.0,
         every: float = EVERY, on_progress=None, cancel=None) -> list[Cue]:
    """Sample the video and describe it: the whole job, for one caller."""
    duration = duration or (probe(src).duration or 0.0)
    shots = sample(src, work, duration, every=every, cancel=cancel)
    return describe(client, shots, on_progress=on_progress, cancel=cancel)
