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

def plan_windows(duration: float, count: int) -> list[tuple[float, float]]:
    """Equal chapters across the whole video: the coverage guarantee."""
    count = max(1, count)
    step = duration / count
    return [(i * step, (i + 1) * step) for i in range(count)]


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
                    "my": {"type": "string"},
                    "score": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["index", "start", "end", "en", "my", "score", "why"],
            },
        },
    },
    "required": [
        "title_en", "title_my", "description_en", "description_my",
        "hashtags", "thumbnail_text_en", "thumbnail_text_my", "beats",
        "video_type", "pacing", "hook_en", "hook_my",
    ],
}


def _windows_block(cues: list[Cue], windows: list[tuple[float, float]]) -> str:
    lines = []
    for i, (ws, we) in enumerate(windows):
        inside = cues_in(cues, ws, we)
        body = " ".join(c.text for c in inside).strip() or "(no speech in this stretch)"
        if len(body) > 1600:
            body = body[:1600] + " ..."
        lines.append("### Chapter {} | window {:.1f}s - {:.1f}s\n{}".format(i, ws, we, body))
    return "\n\n".join(lines)


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

{context}{frame_note}The video below is split into {n} equal chapters. The transcript for
each chapter follows.

{chapters}

YOUR TASK
Return JSON with these fields.

1. "beats": EXACTLY {n} objects, one per chapter, in order, with "index" equal
   to the chapter number.
   - "start" and "end" must fall INSIDE that chapter's window, and should pick
     the single most interesting or quotable moment in it.
   - Aim for about {clip:.1f} seconds per beat ({minclip:.1f}s minimum).
   - Prefer starting on a sentence boundary so the clip does not open mid-word.
   - "en": THREE to FIVE full sentences of recap narration for this moment, in
     English -- roughly {words} words, which is what fits a {clip:.0f} second
     clip read aloud at a natural pace. Say what actually happens: who does
     what, to whom, and what it leads to. Name the people and the things on
     screen. A one-line caption is not enough; this is spoken narration that
     has to carry the clip on its own. Do not repeat the transcript verbatim --
     retell it in your own words, sharper than the original.
   - "my": the SAME line in natural Burmese (Myanmar), carrying every piece of
     information the English carries -- the names, the actions, the outcome.
     Write how a Burmese creator would actually say it, not a word-for-word
     transliteration, and never a shortened gloss: if the English runs to four
     sentences, so does the Burmese. A Burmese line visibly briefer than its
     English twin is wrong and must be rewritten longer and fuller.
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
        chapters=_windows_block(cues, windows),
        clip=clip_len,
        minclip=clip_len * 0.6,
        # ~2.3 words a second is a comfortable narration pace
        words=max(35, int(clip_len * 2.3)),
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


# ------------------------------------------------------------------ parity

# Measured against real output: a Burmese line carrying the same content as its
# English twin runs roughly 0.9-1.4x its character count. Below this it is a
# compressed gloss, which is what "the Burmese is too short" looks like in the
# data.
PARITY_FLOOR = 0.85

_EXPAND_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "my": {"type": "string"},
                },
                "required": ["index", "my"],
            },
        }
    },
    "required": ["items"],
}


def thin_burmese(beats: list[Beat]) -> list[int]:
    """Indexes whose Burmese says visibly less than the English."""
    thin = []
    for i, b in enumerate(beats):
        if not b.en.strip():
            continue
        if not b.my.strip() or len(b.my) < PARITY_FLOOR * len(b.en):
            thin.append(i)
    return thin


def expand_burmese(client: Gemini, beats: list[Beat], title: str = "") -> int:
    """
    Rewrite the Burmese lines that came back short, in one extra call.

    Asking again for the whole package would re-roll the English and the
    timings too; this touches only the lines that need more words, so a good
    first draft is not thrown away to fix a few of them.
    """
    thin = thin_burmese(beats)
    if not thin:
        return 0

    lines = []
    for i in thin:
        lines.append(
            "index {}:\n  ENGLISH: {}\n  CURRENT BURMESE (too short): {}".format(
                i, beats[i].en, beats[i].my or "(missing)"
            )
        )

    header = "These Burmese narration lines for a video recap"
    if title:
        header += ' of "{}"'.format(title)
    header += " are shorter than their English counterparts and have lost detail."

    prompt = "\n\n".join([
        header,
        "\n\n".join(lines),
        "Rewrite each one in natural Burmese (Myanmar script) so it carries "
        "everything the English says -- the names, the actions, the outcome -- "
        "at the same length, sentence for sentence. These are spoken narration "
        "lines that must fill several seconds of video each, so a short "
        "caption-style line is wrong: write it out in full. Keep the meaning "
        "and tone. Return the same index for each. Burmese only, no "
        "transliteration, no English words except proper names that Burmese "
        "speakers would leave in Latin script.",
    ])

    try:
        data = client.generate_json(prompt, _EXPAND_SCHEMA, temperature=0.6)
    except GeminiError:
        return 0        # the first draft stands rather than failing the step

    fixed = 0
    for item in data.get("items") or []:
        try:
            i = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        text = (item.get("my") or "").strip()
        # only accept a rewrite that is actually longer than what it replaces
        if 0 <= i < len(beats) and len(text) > len(beats[i].my):
            beats[i].my = text
            fixed += 1
    return fixed


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
    count, clip_len = beat_plan(duration, mode, target_seconds)
    windows = plan_windows(duration, count)
    frame_note = ""
    if frames:
        frame_note = (
            "ON-SCREEN CONTENT\n"
            "{} screenshots are attached, one from each chapter in order. Use "
            "them to describe what is actually shown -- slides, code, faces, "
            "products, on-screen text -- not just what is said.".format(len(frames))
        )
    prompt = build_prompt(
        title=title, uploader=uploader, duration=duration, cues=cues,
        windows=windows, clip_len=clip_len, mode=mode,
        context=context, frame_note=frame_note,
    )
    client = Gemini(api_key, model)
    data = client.generate_json(prompt, _SCHEMA, temperature, images=frames)
    beats = repair_beats(data.get("beats") or [], windows, clip_len, duration)
    expanded = expand_burmese(client, beats, title)
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
    }
