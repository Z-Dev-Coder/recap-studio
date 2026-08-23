"""
Thumbnail candidates.

This module only finds and ranks frames. The text is laid over them in the
browser, on a canvas, because Chromium shapes Burmese correctly and the
Pillow build here has no Raqm -- drawing Myanmar script with Pillow would
place the glyphs in visually wrong order. Doing it in the page also means
the overlay stays editable instead of being baked in by the server.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from .media import Cancelled, MediaError, frame_at, probe


@dataclass
class Candidate:
    path: Path
    time: float
    quality: float            # how good the picture is: sharp, lit, not empty
    beat_score: float = 0.0   # how good the moment is, per the recap script
    why: str = ""             # the script's reason this moment mattered

    @property
    def score(self) -> float:
        """
        Ranked on both picture and moment.

        A crisp frame of a throwaway second makes a worse thumbnail than a
        decent frame of the moment the recap is actually about, so the script's
        own rating carries nearly half the weight.
        """
        if self.beat_score <= 0:
            return round(self.quality * 0.7, 4)      # not from a beat at all
        return round(self.quality * 0.55 + (self.beat_score / 10) * 0.45, 4)

    def as_dict(self, rel_to: Path | None = None) -> dict:
        # posix separators: this name is handed straight to the browser as a URL
        name = self.path.name if rel_to is None else self.path.relative_to(rel_to).as_posix()
        return {
            "file": name,
            "time": round(self.time, 2),
            "score": self.score,
            "quality": round(self.quality, 4),
            "beat_score": round(self.beat_score, 1),
            "why": self.why,
        }


def _span(beat: dict) -> tuple[float, float] | None:
    try:
        start, end = float(beat["start"]), float(beat["end"])
    except (KeyError, TypeError, ValueError):
        return None
    return (start, end) if end > start else None


def _score(path: Path) -> float:
    """
    Prefer sharp, well-lit, non-empty frames.

    Sharpness is the standard deviation of an edge-detect pass: a motion
    blurred or near-black frame has almost no edge energy, which is exactly
    the frame nobody wants as a thumbnail. Brightness is scored as distance
    from mid-grey so blown-out and pitch-black frames both fall away.
    """
    with Image.open(path) as im:
        grey = im.convert("L")
        small = grey.resize((320, 180))
        edges = small.filter(ImageFilter.FIND_EDGES)
        sharpness = ImageStat.Stat(edges).stddev[0] / 64.0
        brightness = ImageStat.Stat(small).mean[0] / 255.0
        balance = 1.0 - abs(brightness - 0.5) * 2      # 1 at mid-grey, 0 at either extreme
        colour = ImageStat.Stat(im.convert("RGB")).stddev
        variety = min(1.0, sum(colour) / 3 / 70.0)
    return round(min(1.0, sharpness) * 0.55 + balance * 0.25 + variety * 0.20, 4)


def candidates(
    video: Path,
    out_dir: Path,
    count: int = 16,
    beats: list[dict] | None = None,
    cancel=None,
) -> list[Candidate]:
    """
    Pull frames spread across the video and rank them.

    The recap's own moments come first -- a thumbnail showing something the
    recap actually covers beats a random frame from a stretch that was cut --
    and the rest of the quota is filled with an even sweep of the whole video,
    so a short script never limits how many frames there are to choose from.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe(video).duration or 0.0
    if duration <= 0:
        raise ValueError("could not read the video duration")

    count = max(1, min(count, 40))
    times: list[tuple[float, float, str]] = []      # (when, beat score, why)

    def add(when: float, beat_score: float = 0.0, why: str = "") -> None:
        when = min(max(when, 0.0), max(0.0, duration - 0.05))
        # a second apart is close enough to be the same picture
        if all(abs(when - t[0]) > 1.0 for t in times):
            times.append((when, beat_score, why))

    # Strongest moments first, and three frames from each of the very best --
    # the thumbnail should come from what the recap is actually about, and one
    # frame of a good moment can still catch a blink or a cut.
    ranked = sorted(
        (b for b in (beats or []) if _span(b)),
        key=lambda b: float(b.get("score", 5) or 5),
        reverse=True,
    )
    for b in ranked:
        if len(times) >= count:
            break
        start, end = _span(b)
        score = float(b.get("score", 5) or 5)
        why = (b.get("why") or b.get("en") or "")[:80]
        picks = 3 if score >= 8 else 2 if score >= 6 else 1
        for k in range(picks):
            if len(times) >= count:
                break
            add(start + (end - start) * (k + 1) / (picks + 1), score, why)

    # fill any remaining quota with an even sweep, skipping intro and end card
    sweep = max(0, count - len(times))
    if sweep:
        span = duration * 0.92
        for i in range(sweep * 2):          # oversample: duplicates get dropped
            if len(times) >= count:
                break
            add(duration * 0.04 + span * i / max(1, sweep * 2 - 1))

    times.sort(key=lambda t: t[0])

    found: list[Candidate] = []
    for i, (when, beat_score, why) in enumerate(times):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        dest = out_dir / f"frame_{i:02d}.jpg"
        try:
            frame_at(video, when, dest, cancel=cancel)
            found.append(Candidate(
                path=dest, time=when, quality=_score(dest),
                beat_score=beat_score, why=why,
            ))
        except (MediaError, OSError):
            # a frame ffmpeg cannot read must not stop the rest -- but only
            # that. Catching everything here once turned a TypeError in this
            # very loop into a bare "no usable frames" with nothing to debug.
            continue

    found.sort(key=lambda c: c.score, reverse=True)
    return found
