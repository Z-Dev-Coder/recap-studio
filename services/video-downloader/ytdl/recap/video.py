"""
Building the recap cut, and the subtitle files that go with it.

The cut is the original footage spliced together -- the beats name their
moments in the source, each is re-encoded on exact boundaries, and the parts
are joined. The recap SRT is retimed onto the joined timeline, because a
subtitle file for the recap must be relative to the recap, not the original.
"""

from __future__ import annotations

from pathlib import Path

from .media import Cancelled, concat, cut, probe
from .transcript import Cue, to_srt

REELS_MAX = 90.0     # a safety ceiling; the plan already targets ~60s
MIN_CLIP = 1.5       # below this a clip reads as a glitch rather than a moment


def _bounds(beats: list[dict], duration: float) -> list[tuple[float, float]]:
    """
    How far each beat may grow before it would collide with its neighbour.

    The gaps between beats are the material the recap was trimmed out of, so
    they are exactly what a longer cut should reclaim -- and reclaiming all of
    them lands on the untouched original.

    Each gap is split down the middle so neighbours cannot both claim it.
    Handing the whole gap to both sides lets their spans overlap once the
    budget grows, which plays the same seconds twice and makes the total come
    out longer than the video actually is.
    """
    out = []
    for i, b in enumerate(beats):
        start, end = float(b["start"]), float(b["end"])
        prev_end = float(beats[i - 1]["end"]) if i > 0 else None
        next_start = float(beats[i + 1]["start"]) if i < len(beats) - 1 else None

        left = 0.0 if prev_end is None else (prev_end + start) / 2
        if next_start is None:
            right = duration if duration > end else end
        else:
            right = (end + next_start) / 2

        out.append((min(left, start), max(right, end)))
    return out


def plan_fitted(beats: list[dict], wants: list[float],
                duration: float = 0.0) -> list[tuple[float, float]]:
    """
    Give each beat exactly as much footage as its narration needs.

    A recap whose length was chosen independently of the script ends up with
    five seconds of voice sitting in thirty seconds of footage, eleven times
    over -- the narration reads as if it stopped after the first line, and the
    footage in between is the very material the recap was supposed to cut. So
    size each span to the line spoken over it instead: the picture then changes
    when the narrator moves on, which is what makes a recap feel edited.

    The span stays centred on the moment the beat named, and cannot grow past
    its neighbours -- footage belonging to the next beat is not this beat's to
    borrow.
    """
    limits = _bounds(beats, duration)
    out = []
    for beat, want, (lo, hi) in zip(beats, wants, limits):
        start, end = float(beat["start"]), float(beat["end"])
        want = max(MIN_CLIP, float(want or 0))
        middle = (start + end) / 2

        if want >= hi - lo:
            # The line is longer than the gap between this beat's neighbours.
            # Truncating here is what let the voice overrun its clip and drift
            # into the next one -- a second and a half on one line, eleven
            # seconds across a whole recap. The clips are concatenated, so
            # taking footage a neighbour also takes costs a moment of repeated
            # picture; the voice staying on its own clip is worth more than
            # that. Only the video's own ends are hard limits.
            lo = max(0.0, middle - want / 2)
            hi = lo + want
            if duration > 0 and hi > duration:
                hi = duration
                lo = max(0.0, duration - want)
            out.append((lo, hi))
            continue

        a = middle - want / 2
        b = a + want
        if a < lo:
            a, b = lo, lo + want
        elif b > hi:
            a, b = hi - want, hi
        out.append((a, b))
    return out


def plan_clips(beats: list[dict], budget: float, duration: float = 0.0) -> list[tuple[float, float]]:
    """
    Choose the span of original video each beat contributes.

    The budget runs the whole way from the full length of the original down to
    a handful of seconds:

    * at (or above) the full length every gap is reclaimed, so the recap is
      the original, uncut;
    * below it, beats grow outwards from their moment into the surrounding
      footage, the highest scores growing first;
    * below the sum of the beats, the weakest lose their seconds first, so the
      payoff survives a short cut while filler does not.

    Returns (start, end) pairs; a pair shorter than MIN_CLIP has been squeezed
    out entirely and the caller skips it.
    """
    if not beats:
        return []

    spans = [(float(b["start"]), float(b["end"])) for b in beats]
    lengths = [max(0.0, e - s) for s, e in spans]
    total = sum(lengths)
    scores = [max(1.0, float(b.get("score", 5) or 5)) for b in beats]

    if budget <= 0 or abs(budget - total) < 0.05:
        return spans

    # ---------------------------------------------------------- grow
    if budget > total:
        limits = _bounds(beats, duration)
        caps = [max(0.0, hi - lo) for lo, hi in limits]
        room_total = sum(caps) - total
        want = min(budget, total + room_total) - total
        if want <= 0.05:
            return spans

        grown = list(lengths)
        # share the extra seconds by score, then hand any that could not fit
        # (a beat hemmed in by its neighbours) to whoever still has room
        for _ in range(6):
            open_idx = [i for i in range(len(beats)) if caps[i] - grown[i] > 0.05]
            if not open_idx or want <= 0.05:
                break
            weight = sum(scores[i] for i in open_idx)
            handed = 0.0
            for i in open_idx:
                take = min(caps[i] - grown[i], want * (scores[i] / weight))
                grown[i] += take
                handed += take
            want -= handed
            if handed <= 0.05:
                break

        out = []
        for i, (lo, hi) in enumerate(limits):
            extra = grown[i] - lengths[i]
            start, end = spans[i]
            # grow both ways, and spend whatever one side cannot take on the other
            back = min(extra / 2, start - lo)
            fwd = min(extra - back, hi - end)
            back = min(extra - fwd, start - lo)
            out.append((round(start - back, 3), round(end + fwd, 3)))
        return out

    # ---------------------------------------------------------- shrink
    floors = [min(MIN_CLIP, l) for l in lengths]
    spare = budget - sum(floors)
    if spare <= 0:
        # not even the floors fit: keep the best moments, drop the rest
        order = sorted(range(len(beats)), key=lambda i: scores[i], reverse=True)
        kept = [0.0] * len(beats)
        left = budget
        for i in order:
            if left < MIN_CLIP:
                break
            take = min(lengths[i], max(MIN_CLIP, budget / max(1, len(beats) // 2)), left)
            kept[i] = take
            left -= take
        return [(spans[i][0], round(spans[i][0] + kept[i], 3)) for i in range(len(beats))]

    weight_total = sum(scores)
    kept = [
        min(lengths[i], floors[i] + spare * (scores[i] / weight_total))
        for i in range(len(beats))
    ]

    # a beat capped at its natural length frees seconds; give them to the best
    leftover = budget - sum(kept)
    if leftover > 0.05:
        for i in sorted(range(len(beats)), key=lambda i: scores[i], reverse=True):
            room = lengths[i] - kept[i]
            if room <= 0:
                continue
            take = min(room, leftover)
            kept[i] += take
            leftover -= take
            if leftover <= 0.05:
                break

    return [(spans[i][0], round(spans[i][0] + kept[i], 3)) for i in range(len(beats))]


def build(
    source: Path,
    beats: list[dict],
    dest: Path,
    mode: str = "reels",
    work_dir: Path | None = None,
    on_progress=None,
    target_seconds: float = 0.0,
    duration: float = 0.0,
    cancel=None,
    framing: str = "blur",
    shape: str = "",
    fit_seconds: list[float] | None = None,
) -> dict:
    """
    Splice the beats out of `source` into `dest`.

    `target_seconds` is the length the user asked for, and runs from the full
    length of the original downwards: at the top the clips grow until they meet
    and the recap is the whole video, and as it falls the weakest moments give
    up their seconds first.

    Returns the timeline actually produced: each beat with the position it
    occupies in the recap, which is what the SRT and the UI both need.
    """
    if not beats:
        raise ValueError("there are no beats to build from")

    work = work_dir or dest.parent / "_parts"
    work.mkdir(parents=True, exist_ok=True)
    # a named shape (square, portrait) overrides the plain reel/long choice
    vertical = shape if shape in ("reels", "square", "portrait") else (mode == "reels")

    parts: list[Path] = []
    timeline: list[dict] = []
    playhead = 0.0
    # the length the user set on the slider; with none set the beats play at
    # the length the script gave them
    budget = float(target_seconds or 0)

    ordered = sorted(beats, key=lambda b: float(b["start"]))

    # At the full length there is nothing to splice. The clips have grown until
    # they meet, so cutting would take the video apart into one part per beat
    # and glue it straight back together -- eleven re-encodes, eleven seams,
    # and a file that is the original anyway. Encode it once instead, and map
    # the beats onto themselves: in an uncut recap, recap time IS source time.
    if fit_seconds is None and duration > 0 and budget >= duration - 0.5:
        cut(source, dest, 0.0, duration, vertical=vertical,
            cancel=cancel, framing=framing)
        actual = probe(dest).duration or duration
        timeline = [{
            "index": b.get("index", i),
            "source_start": round(float(b["start"]), 3),
            "source_end": round(float(b["end"]), 3),
            "recap_start": round(float(b["start"]), 3),
            "recap_end": round(float(b["end"]), 3),
            "score": float(b.get("score", 5) or 5),
            "why": b.get("why", ""),
            "en": b.get("en", ""),
            "my": b.get("my", ""),
        } for i, b in enumerate(ordered)]
        if on_progress:
            on_progress(1, 1)
        try:
            work.rmdir()
        except OSError:
            pass
        return {
            "path": str(dest),
            "duration": round(actual, 2),
            "clips": 1,
            "timeline": timeline,
            "mode": mode,
            "uncut": True,
        }

    if fit_seconds:
        plan = plan_fitted(ordered, fit_seconds, duration)
    else:
        plan = plan_clips(ordered, budget, duration)

    for i, (beat, span) in enumerate(zip(ordered, plan)):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        start, end = span
        if end - start < MIN_CLIP:
            continue                      # squeezed out by stronger moments

        part = work / f"part_{i:03d}.mp4"
        cut(source, part, start, end, vertical=vertical, cancel=cancel, framing=framing)
        actual = probe(part).duration or (end - start)   # encoders round; trust the file
        parts.append(part)

        timeline.append({
            "index": beat.get("index", i),
            "source_start": round(start, 3),
            "source_end": round(end, 3),
            "recap_start": round(playhead, 3),
            "recap_end": round(playhead + actual, 3),
            "score": float(beat.get("score", 5) or 5),
            "why": beat.get("why", ""),
            "en": beat.get("en", ""),
            "my": beat.get("my", ""),
        })
        playhead += actual
        if on_progress:
            on_progress(i + 1, len(ordered))

    if not parts:
        raise ValueError("every beat was empty or out of range")

    concat(parts, dest, cancel=cancel)
    for p in parts:
        p.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass

    return {
        "path": str(dest),
        "duration": round(playhead, 2),
        "clips": len(parts),
        "timeline": timeline,
        "mode": mode,
    }


def recap_srt(timeline: list[dict], lang: str = "en") -> str:
    """Recap narration as SRT, timed against the recap video."""
    cues = []
    for row in timeline:
        text = (row.get("my") if lang == "my" else row.get("en")) or ""
        text = text.strip()
        if not text:
            continue
        cues.append(Cue(
            start=float(row["recap_start"]),
            end=max(float(row["recap_end"]), float(row["recap_start"]) + 0.8),
            text=text,
        ))
    return to_srt(cues)


def source_srt(timeline: list[dict], lang: str = "en") -> str:
    """
    The same narration timed against the ORIGINAL video.

    Useful when the recap script is being used as a voice-over guide while
    watching the full video, rather than as subtitles for the cut.
    """
    cues = []
    for row in timeline:
        text = (row.get("my") if lang == "my" else row.get("en")) or ""
        text = text.strip()
        if not text:
            continue
        cues.append(Cue(
            start=float(row["source_start"]),
            end=max(float(row["source_end"]), float(row["source_start"]) + 0.8),
            text=text,
        ))
    return to_srt(cues)
