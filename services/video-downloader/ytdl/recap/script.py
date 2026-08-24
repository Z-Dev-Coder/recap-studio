"""
Turning a transcript into a recap script.

The alignment requirement -- the recap must cover the whole of the original,
not just whatever the model found interesting in the first few minutes -- is
enforced structurally rather than by asking nicely. The video is divided into
equal chapters and exactly one beat is taken from each, so coverage is a
property of the plan instead of a hope about the output.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from .gemini import Gemini, GeminiError
from .transcript import Cue

LANGUAGES = {"en": "English", "my": "Burmese (Myanmar)"}


@dataclass
class Beat:
    """One line of recap, and the slice of original video it plays over."""

    index: int
    start: float
    end: float
    en: str = ""
    my: str = ""
    score: float = 5.0        # how essential this moment is, 1-10
    why: str = ""             # one line on why it earned its place

    def as_dict(self) -> dict:
        return asdict(self)

    def text(self, lang: str) -> str:
        return (self.my if lang == "my" else self.en) or self.en or self.my


# ------------------------------------------------------------------ planning

# How far a chapter boundary may slide to avoid landing mid-event, as a share
# of one chapter. Small on purpose: the equal spacing is what guarantees the
# recap covers the whole video, and that guarantee is worth more than a
# perfectly placed cut.
SNAP_WINDOW = 0.35


def plan_windows(
    duration: float,
    count: int,
    cues: list[Cue] | None = None,
    events: list[dict] | None = None,
) -> list[tuple[float, float]]:
    """
    Chapters across the whole video: the coverage guarantee.

    Equal chapters are what stop a recap from covering the first three minutes
    and calling it done, so the spacing stays essentially equal. But a boundary
    that falls in the middle of a sentence -- or in the middle of an event --
    splits one moment across two beats, and both halves then get narrated as if
    they were separate things.

    So each interior boundary is allowed to slide, by at most SNAP_WINDOW of a
    chapter, to the nearest gap between speech, preferring a gap that is also a
    seam between story events. Boundaries stay in order and no chapter is
    allowed to collapse.
    """
    count = max(1, count)
    if duration <= 0:
        return [(0.0, 0.0)]
    step = duration / count
    edges = [i * step for i in range(count + 1)]

    seams = _boundary_candidates(cues, events)
    if seams:
        reach = step * SNAP_WINDOW
        for i in range(1, count):
            near = [t for t in seams if abs(t - edges[i]) <= reach]
            if not near:
                continue
            # keep the order strict, so no chapter can vanish
            low = edges[i - 1] + step * 0.25
            high = edges[i + 1] - step * 0.25
            near = [t for t in near if low < t < high]
            if near:
                edges[i] = min(near, key=lambda t: abs(t - edges[i]))

    return [(edges[i], edges[i + 1]) for i in range(count)]


def _boundary_candidates(
    cues: list[Cue] | None,
    events: list[dict] | None,
) -> list[float]:
    """
    Moments where cutting is least likely to break something.

    A pause in the speech is the cheap signal, available on every video. When
    the story analysis ran, the edges of its events are the better signal, and
    they are offered twice so a pause that is also an event seam wins.
    """
    out: list[float] = []
    for a, b in zip(cues or [], (cues or [])[1:]):
        if b.start - a.end >= 0.35:          # a real pause, not a breath
            out.append((a.end + b.start) / 2)
    for e in events or []:
        try:
            out.append(float(e.get("end", 0)))
            out.append(float(e.get("start", 0)))
        except (TypeError, ValueError):
            continue
    return sorted(t for t in out if t > 0)


def beat_plan(duration: float, mode: str, target_seconds: float = 0.0) -> tuple[int, float]:
    """
    How many beats, and how long each clip runs.

    Reels are a fixed budget cut into equal pieces. Long form scales with the
    source so a 40-minute talk does not collapse into the same 8 clips as a
    3-minute one.
    """
    if mode == "reels":
        total = target_seconds or 60.0
        count = 8 if total <= 45 else 10 if total <= 75 else 12
        return count, total / count
    total = target_seconds or max(45.0, min(duration * 0.22, 300.0))
    count = max(6, min(24, int(duration // 60) + 6))
    return count, max(4.0, total / count)


def cues_in(cues: list[Cue], start: float, end: float) -> list[Cue]:
    return [c for c in cues if c.end > start and c.start < end]


# ------------------------------------------------------------------ prompting

_SCHEMA = {
    "type": "object",
    "properties": {
        "title_en": {"type": "string"},
        "title_my": {"type": "string"},
        "description_en": {"type": "string"},
        "description_my": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "video_type": {"type": "string"},
        "pacing": {"type": "string"},
        "hook_en": {"type": "string"},
        "hook_my": {"type": "string"},
        "thumbnail_text_en": {"type": "string"},
        "thumbnail_text_my": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "en": {"type": "string"},
                    "score": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["index", "start", "end", "en", "score", "why"],
            },
        },
    },
    "required": [
        "title_en", "title_my", "description_en", "description_my",
        "hashtags", "thumbnail_text_en", "thumbnail_text_my", "beats",
        "video_type", "pacing", "hook_en", "hook_my",
    ],
}


def _windows_block(
    cues: list[Cue],
    windows: list[tuple[float, float]],
    story=None,
) -> str:
    """
    Each chapter as the beat-picker sees it.

    When the story analysis ran, what HAPPENS in a chapter is listed above what
    is SAID in it. A transcript alone leaves the model to infer events from
    dialogue, which is how visual moments -- somebody arriving, escaping, being
    caught -- get missed on videos that show more than they say.
    """
    from .story import event_block

    lines = []
    for i, (ws, we) in enumerate(windows):
        parts = ["### Chapter {} | window {:.1f}s - {:.1f}s".format(i, ws, we)]

        detail = event_block(story.events_in(ws, we), limit=5) if story else ""
        if detail:
            parts.append("What happens:\n" + detail)

        inside = cues_in(cues, ws, we)
        body = " ".join(c.text for c in inside).strip()
        if len(body) > 1400:
            body = body[:1400] + " ..."
        if body:
            parts.append("What is said:\n" + body)
        elif not detail:
            parts.append("(no speech in this stretch)")

        lines.append("\n".join(parts))
    return "\n\n".join(lines)


# Measured against real narration rather than assumed. English prose reads at
# roughly 2.3 words a second, but the Burmese line carrying the same content
# takes about 1.45x as long to speak -- and the Burmese is what the final video
# uses. Sizing to the slower of the two is what makes the audio fit the clip.
WORDS_PER_SECOND = 1.6


def _length_rule(clip_len: float, mode: str) -> str:
    """
    How much narration a clip of this length can actually carry.

    Written after a reel came back with 594 seconds of speech for a 45 second
    video: the script had been told to write three to five sentences per beat
    regardless of how long the beat lasted. A four-second clip holds about ten
    words, and no amount of detail changes that.
    """
    words = max(6, int(clip_len * WORDS_PER_SECOND))
    if mode == "reels" or clip_len < 7:
        kind = "reel" if mode == "reels" else "clip"
        return (
            "ONE punchy sentence of about {} words -- no more. This {} runs "
            "{:.0f} seconds and that is all it holds. Cut every word that is "
            "not carrying meaning.".format(words, kind, clip_len)
        )
    sentences = "two to three" if clip_len < 14 else "three to five"
    return (
        "{} sentences of recap narration, about {} words in total, in "
        "English.".format(sentences.capitalize(), words)
    )


def build_prompt(
    *,
    title: str,
    uploader: str,
    duration: float,
    cues: list[Cue],
    windows: list[tuple[float, float]],
    clip_len: float,
    mode: str,
    context: str = "",
    frame_note: str = "",
    story=None,
) -> str:
    kind = (
        "a punchy vertical short/reel" if mode == "reels"
        else "a long-form recap that still reads as a complete story"
    )
    return """You are writing a social-media recap package for a video.

SOURCE
Title: {title}
Channel: {uploader}
Length: {dur:.0f} seconds ({mins:.1f} minutes)
Target: {kind}

{context}{frame_note}The video below is split into {n} chapters, covering it end to end. What
happens in each -- and what is said in it -- follows.

The Burmese narration is written separately, from the same notes. Write only
the English here.

{chapters}

YOUR TASK
Return JSON with these fields.

1. "beats": EXACTLY {n} objects, one per chapter, in order, with "index" equal
   to the chapter number.
   - "start" and "end" must fall INSIDE that chapter's window, and should pick
     the single most interesting or quotable moment in it.
   - Aim for about {clip:.1f} seconds per beat ({minclip:.1f}s minimum).
   - Prefer starting on a sentence boundary so the clip does not open mid-word.
   - "en": {length_rule}
     This is spoken narration read aloud over the clip, so it MUST fit in
     {clip:.0f} seconds at a natural speaking pace. Going over means the voice
     is still talking when the clip has moved on. Say what actually happens:
     who does what, to whom, and what it leads to. Name the people and things
     on screen. Do not repeat the transcript verbatim -- retell it in your own
     words, sharper than the original.
     The lines are read out back to back, so each one must follow on from the
     one before it. A line that would read the same if the beats were shuffled
     is wrong.
   - "score": 1-10, how strongly this moment would hold a scrolling viewer.
     Score against these, and say which one applies in "why":
       hook (makes you stop scrolling), emotional peak, strong opinion,
       revelation or twist, conflict, a quotable line, a story payoff, or
       concrete practical value.
     A moment hitting one of those hard is a 9 or 10. Setup that merely
     explains, or throat-clearing, is a 2 or 3. Use the whole range and be
     honest -- when the recap is shortened, high scores keep their screen time
     and low ones lose it, so this rating decides what survives the trim.
   - "why": which criterion above it hits, and why, in a few words.
   - Together the beats must tell the story of the WHOLE video from start to
     finish, so early chapters set up and the final chapter lands the payoff.
   - Write them as ONE narration read straight through, not as captions that
     happen to sit next to each other. Each line must follow on from the one
     before it: carry the thread forward with the ordinary words a narrator
     uses -- then, so, but, after that, meanwhile, in the end -- and refer back
     to what was already said instead of reintroducing it. Once a person has
     been named, later lines say "he" or "she". A line that would read the same
     if the beats were shuffled is wrong.

2. "title_en" / "title_my": a scroll-stopping title, under 80 characters.
3. "description_en" / "description_my": a post description of 2-4 short
   paragraphs. Open with a hook, say what the viewer gets, close with a call
   to action. No hashtags in here. The Burmese description must be as full as
   the English one -- same paragraphs, same detail, not a summary of it.
4. "hashtags": 12-18 relevant tags WITHOUT the "#" character, most specific
   first, mixing broad reach tags with niche ones. Latin script only.
5. "thumbnail_text_en" / "thumbnail_text_my": 2-5 words for a thumbnail
   overlay, short enough to read at a glance.
6. "video_type": what this actually is -- podcast, tutorial, vlog, cartoon,
   lecture, news, gameplay, music, review, or whatever fits. "pacing": fast,
   medium or slow. Decide these FIRST and let them shape everything else: a
   tutorial recap promises what the viewer will learn, a cartoon recap sells
   the joke, a podcast recap leads with the strongest opinion in the room.
7. "hook_en" / "hook_my": one spoken sentence to open the recap before the
   first beat -- the line that stops the scroll. It must be about THIS video's
   actual content, never a generic "you won't believe this".

Write for a real audience: concrete, specific, no filler like "in this video".
""".format(
        title=title or "(unknown)",
        uploader=uploader or "(unknown)",
        dur=duration,
        mins=duration / 60,
        kind=kind,
        n=len(windows),
        context=(context + "\n\n") if context else "",
        frame_note=(frame_note + "\n\n") if frame_note else "",
        chapters=_windows_block(cues, windows, story),
        clip=clip_len,
        minclip=clip_len * 0.6,
        length_rule=_length_rule(clip_len, mode),
    )


# ------------------------------------------------------------------ repair

def repair_beats(
    raw: list[dict],
    windows: list[tuple[float, float]],
    clip_len: float,
    duration: float,
) -> list[Beat]:
    """
    Force the model's timings back inside their chapter.

    A beat that lands slightly outside its window, or two that overlap, would
    produce clips that jump backwards once joined -- so the plan wins over the
    model on timing, while the words stay entirely the model's own.
    """
    by_index: dict[int, dict] = {}
    for item in raw or []:
        # the model has returned bare strings and numbers in this array before
        if not isinstance(item, dict):
            continue
        try:
            i = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(windows):
            by_index.setdefault(i, item)

    beats: list[Beat] = []
    for i, (ws, we) in enumerate(windows):
        item = by_index.get(i, {})
        try:
            start = float(item.get("start", ws))
            end = float(item.get("end", start + clip_len))
        except (TypeError, ValueError):
            start, end = ws, ws + clip_len

        span = max(0.0, we - ws)
        want = min(clip_len, span) if span else clip_len

        start = min(max(start, ws), max(ws, we - want))
        end = max(start + max(1.5, want * 0.6), min(end, we))
        end = min(end, we, duration)
        if end - start < 1.0:                      # a degenerate window
            end = min(start + 1.5, duration)

        try:
            score = float(item.get("score", 5))
        except (TypeError, ValueError):
            score = 5.0

        beats.append(Beat(
            index=i,
            start=round(start, 3),
            end=round(end, 3),
            en=(item.get("en") or "").strip(),
            my=(item.get("my") or "").strip(),
            score=min(10.0, max(1.0, score)),
            why=(item.get("why") or "").strip(),
        ))

    beats.sort(key=lambda b: b.start)
    return beats


def coverage(beats: list[Beat], duration: float) -> float:
    """How much of the original the beats reach across, 0-1."""
    if not beats or duration <= 0:
        return 0.0
    return min(1.0, (beats[-1].end - beats[0].start) / duration)


# --------------------------------------------------------- Burmese quality

def thin_burmese(beats: list[Beat], seconds_for=None) -> list[int]:
    """
    Which Burmese lines are worth a second pass.

    This used to compare the Burmese character count against the English one
    and flag anything under 85% of it. Measured against real output that test
    is meaningless: the same content in Burmese runs anywhere from 1.05x to
    1.6x the English length, so the ratio says more about the two writing
    systems than about whether a line is any good. Worse, it pushed the repair
    pass to pad Burmese with filler until the arithmetic came out right.

    What actually matters is whether the line fits the clip when spoken, so
    that is what is measured now -- see burmese.needs_work.

    Returns positions in `beats` (not beat indexes), as it always did.
    """
    from . import burmese

    flagged = set(burmese.needs_work(beats, seconds_for))
    return [i for i, b in enumerate(beats) if b.index in flagged]


def expand_burmese(
    client: Gemini,
    beats: list[Beat],
    title: str = "",
    story=None,
    seconds_for=None,
) -> int:
    """
    Repair the Burmese lines that need it. Kept under its old name because the
    pipeline and the API both call it, but its job has changed: it no longer
    makes short lines longer, it fixes lines that are wrong -- missing,
    inaccurate, unnatural, or the wrong length for the clip they play over.

    Returns how many lines were changed.
    """
    from . import burmese
    from .story import Story

    flagged = burmese.needs_work(beats, seconds_for)
    if not flagged:
        return 0
    report = burmese.review(
        client, beats, story or Story(),
        title=title, seconds_for=seconds_for, only=flagged,
    )
    return report.get("revised", 0)


# --------------------------------------------------------------- subtitles

_TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["index", "text"],
            },
        }
    },
    "required": ["lines"],
}


def translate_cues(
    client: Gemini,
    cues: list[Cue],
    target: str = "my",
    batch: int = 40,
    overlap: int = 6,
    title: str = "",
    glossary: str = "",
) -> list[str]:
    """
    Translate a transcript into `target`, keeping the line count intact.

    Auto-captions break mid-sentence -- "papey oh papey did you get the fi" is
    a whole line -- so translating each one on its own produces disjointed,
    unnatural results. The model is given the passage as continuous speech and
    asked to translate it as such before splitting it back across the same
    lines, with `overlap` lines of the previous batch carried forward so the
    seam between batches does not break a sentence either.

    Line counts must survive because each line is shown against a timestamp;
    anything the model drops keeps its original text rather than leaving a gap.
    """
    language = LANGUAGES.get(target, target)
    out = [c.text for c in cues]

    start = 0
    while start < len(cues):
        chunk = cues[start:start + batch]
        # lines before this batch, for context only -- not to be returned
        lead = cues[max(0, start - overlap):start]

        listing = "\n".join(
            "{}: {}".format(start + i, c.text) for i, c in enumerate(chunk)
        )
        before = ""
        if lead:
            before = (
                "The lines immediately before this batch, for continuity "
                "(do NOT translate these, they are already done):\n"
                + "\n".join(c.text for c in lead)
                + "\n\n"
            )

        prompt = "\n\n".join(filter(None, [
            "This is the transcript of a video" + (f' titled "{title}"' if title else "")
            + ", cut into timed subtitle lines. The lines are consecutive "
            "fragments of ONE continuous piece of speech -- many of them start "
            "or end mid-sentence.",
            before + "Lines to translate:\n" + listing,
            glossary,
            "Read the whole passage first and translate it into {} as "
            "continuous, natural speech -- the way a person actually talks, "
            "not a word-for-word rendering of each fragment. Then split that "
            "translation back across the SAME line numbers.\n\n"
            "Requirements:\n"
            "- Return every index given above, exactly once.\n"
            "- Consecutive lines must read as one flowing passage: a sentence "
            "may begin on one line and finish on the next, exactly as it does "
            "in the original.\n"
            "- Do NOT compress. Write the full natural phrasing even where "
            "that runs longer than the English fragment; a clipped, "
            "telegraphic line is wrong.\n"
            "- Keep names and recurring terms spelled consistently throughout."
            .format(language),
        ]))

        try:
            data = client.generate_json(prompt, _TRANSLATE_SCHEMA, temperature=0.3)
        except GeminiError:
            start += batch
            continue        # this batch keeps the original text
        for item in data.get("lines") or []:
            try:
                i = int(item.get("index", -1))
            except (TypeError, ValueError):
                continue
            text = (item.get("text") or "").strip()
            if text and 0 <= i < len(out):
                out[i] = text
        start += batch
    return out


# ------------------------------------------------------------------ entry

def generate(
    *,
    api_key: str,
    model: str,
    title: str,
    uploader: str,
    duration: float,
    cues: list[Cue],
    mode: str = "reels",
    target_seconds: float = 0.0,
    temperature: float = 0.7,
    context: str = "",
    frames: list[tuple[str, bytes]] | None = None,
) -> dict:
    """
    Video in, recap package out, in four stages rather than one prompt.

    It used to be a single call that had to work out the story, pick the
    moments, write English, write Burmese and lay out the whole social post at
    once. Everything competed for the same attention, and the Burmese lost --
    written in the same breath as the English, it came out as a translation of
    it whatever the instructions said.

    Now:
      1. READ    -- what actually happens (story.analyse), on its own.
      2. PICK    -- which moments to use and the English narration, given the
                    events rather than the raw transcript.
      3. WRITE   -- the Burmese, from the same events and the whole beat
                    sequence at once, so lines connect and none of it is
                    translated from the English.
      4. CHECK   -- read the Burmese back and repair only what is wrong.

    Stages 1 and 4 degrade to nothing if they fail: no analysis means stage 2
    falls back to the transcript it always used, and no review leaves the
    written Burmese standing. Cost is one extra call over the old path in the
    common case, two when lines need repair.
    """
    from . import burmese as burmese_mod
    from . import story as story_mod

    client = Gemini(api_key, model)
    count, clip_len = beat_plan(duration, mode, target_seconds)

    # ---------------------------------------------------------- 1. read it
    story = story_mod.analyse(
        client, title=title, duration=duration, cues=cues,
        context=context, frames=frames,
    )

    # chapters can now avoid cutting an event in half
    windows = plan_windows(duration, count, cues, story.events if story else None)

    # The frames were read in stage 1 and what they showed is in the events, so
    # sending them again here would pay for the same pictures twice to tell the
    # model something it has already been told in words. They are only attached
    # when there is no analysis to carry them.
    resend = None if story else frames
    frame_note = ""
    if resend:
        frame_note = (
            "ON-SCREEN CONTENT\n"
            "{} screenshots are attached, one from each chapter in order. Use "
            "them to describe what is actually shown -- slides, code, faces, "
            "products, on-screen text -- not just what is said.".format(len(resend))
        )

    # ------------------------------------------------- 2. pick the moments
    prompt = build_prompt(
        title=title, uploader=uploader, duration=duration, cues=cues,
        windows=windows, clip_len=clip_len, mode=mode,
        context=context, frame_note=frame_note, story=story or None,
    )
    data = client.generate_json(prompt, _SCHEMA, temperature, images=resend)
    beats = repair_beats(data.get("beats") or [], windows, clip_len, duration)

    # the clip a line is spoken over is the only length that matters
    def seconds_for(beat: Beat) -> float:
        return max(1.5, min(beat.end - beat.start, clip_len * 1.5))

    # ------------------------------------------------- 3. write the Burmese
    written = burmese_mod.write(
        client, beats, story, title=title, seconds_for=seconds_for,
        temperature=min(0.9, temperature + 0.1),
    )

    # ------------------------------------------------------- 4. read it back
    review = {"checked": 0, "revised": 0, "issues": []}
    if written:
        review = burmese_mod.review(
            client, beats, story, title=title, seconds_for=seconds_for,
        )
    expanded = review.get("revised", 0)

    return {
        "beats": [b.as_dict() for b in beats],
        "video_type": (data.get("video_type") or "").strip(),
        "pacing": (data.get("pacing") or "").strip(),
        "hook": {"en": data.get("hook_en", ""), "my": data.get("hook_my", "")},
        "title": {"en": data.get("title_en", ""), "my": data.get("title_my", "")},
        "description": {
            "en": data.get("description_en", ""),
            "my": data.get("description_my", ""),
        },
        "hashtags": [h.lstrip("#").strip() for h in (data.get("hashtags") or []) if h.strip()],
        "thumbnail_text": {
            "en": data.get("thumbnail_text_en", ""),
            "my": data.get("thumbnail_text_my", ""),
        },
        "coverage": round(coverage(beats, duration), 3),
        "mode": mode,
        "burmese_expanded": expanded,
        # internal, for diagnosis -- never shown to the user
        "story": story_mod.as_dict(story) if story else {},
        "burmese_written": written,
        "burmese_review": review,
    }
