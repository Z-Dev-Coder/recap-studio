"""
Writing the Burmese narration, and checking it sounds like Burmese.

The Burmese used to be produced in the same breath as the English, field by
field in one JSON object, with the instruction "the same moment, in Burmese".
Whatever that instruction says, a model writing "my" immediately after "en"
writes a translation of it: the English clause order survives, the register
drifts literary, and the result reads as subtitles rather than narration.

So the Burmese is written here instead, from the STORY -- the events, their
causes, the cast -- with the English line offered only as a note on which
moment is meant. The whole sequence is written in one call so that each line
knows what the line before it said, which is what makes a recap a story rather
than eleven summaries.

Two measured facts shape this module:

* Burmese narration runs at about 14.5 characters a second through VoxCPM
  (measured across real generated lines: 11.8-17.9, median 14.5). That is the
  only length rule used here.
* The same content in Burmese runs 1.05-1.6x the English character count. That
  spread is why comparing Burmese length to English length says nothing about
  Burmese quality -- the old parity check was reading noise.
"""

from __future__ import annotations

from .gemini import Gemini, GeminiError
from .story import Story, event_block

# Measured, not assumed -- see the module docstring. Used to turn "this clip is
# 6 seconds" into "this line has room for about 87 characters", which is a
# statement about speech rather than about English.
MY_CHARS_PER_SECOND = 14.5

# How far a line may fall short of its clip before it is worth a second pass.
# Generous on purpose: a line that ends early leaves a beat of silence, which
# is fine; a line that runs long talks over the next clip, which is not.
SHORT_RATIO = 0.55
LONG_RATIO = 1.25


def chars_for(seconds: float) -> int:
    """How many Burmese characters fit in a clip of this length."""
    return max(20, int(seconds * MY_CHARS_PER_SECOND))


_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
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
    "required": ["lines"],
}

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "ok": {"type": "boolean"},
                    "issues": {"type": "array", "items": {"type": "string"}},
                    "revised": {"type": "string"},
                },
                "required": ["index", "ok"],
            },
        }
    },
    "required": ["lines"],
}


STYLE = """HOW THE BURMESE MUST SOUND

You are a Burmese recap narrator talking to Burmese viewers. You are not
translating anything. You watched this video, you understood it, and now you
are telling people what happened.

Write the way people actually speak:
- Spoken endings -- -တယ်၊ -ပါတယ်၊ -နေတယ်၊ -သွားတယ်၊ -လိုက်တယ်။ Not the literary
  -သည်၊ -၏၊ -ခဲ့လေသည် of a newspaper or a textbook.
- Everyday words. Do not reach for a formal or elegant word when a plain one
  is what a person would say out loud.
- Burmese word order, verb last, with what the listener already knows left
  out. A sentence that maps clause-for-clause onto English is a translation.
- Burmese idiom. Never translate an English figure of speech word by word.

Connect the events. This is one continuous story, not a list:
  အဲဒီနောက်၊ ဒါပေမယ့်၊ အဲဒီအချိန်မှာ၊ အဲဒီမှာ၊ ဒီလိုနဲ့၊ မထင်မှတ်ဘဲ၊
  နောက်ဆုံးတော့၊ ဒါနဲ့၊ ဒါကြောင့်
Use them where the events genuinely connect -- not in every sentence, which
reads as a tic.

Say what caused what. "သူက စက်ကို တပ်တယ်။ သူတို့ အတိတ်ကို ရောက်တယ်။" is two
facts side by side. "စက်ကို တပ်ဆင်ပြီး စမ်းကြည့်လိုက်တဲ့အခါ သူတို့ဟာ မထင်မှတ်ဘဲ
အတိတ်ကမ္ဘာကို ရောက်သွားပါတယ်။" is a story. Write the second kind. (That is an
example of STYLE only -- the content must come from this video.)

Characters: use the names given above, spelled the same way every time. Name
someone when it is not obvious who is meant; once it is obvious, ordinary
pronouns are better than repeating the name in every sentence.

This is read aloud by a speech engine, so write for the ear: short to medium
sentences, ordinary punctuation, clear sentence endings, no brackets, no
symbols, no abbreviations, nothing that has to be seen to be understood.

Never invent. No dialogue, motives, feelings, or backstory that the notes do
not support. If the notes do not say why something happened, do not supply a
reason.
"""


def _line_block(beats, story: Story, seconds_for) -> str:
    """Each beat as the writer needs it: the moment, its events, its room."""
    rows = []
    for b in beats:
        clip = seconds_for(b)
        events = story.events_in(b.start, b.end) if story else []
        detail = event_block(events, limit=4)
        row = [
            "--- LINE {} ({:.0f}s-{:.0f}s of the video, about {} Burmese "
            "characters fit) ---".format(b.index, b.start, b.end, chars_for(clip))
        ]
        if detail:
            row.append("What happens here:\n" + detail)
        elif b.en.strip():
            row.append("What happens here: " + b.en.strip())
        if b.en.strip() and detail:
            # offered as a pointer to the moment, explicitly not as source text
            row.append("(An English writer described this moment as: {}"
                       " -- this is only to show WHICH moment is meant. Do not "
                       "follow its wording or its sentence structure.)".format(b.en.strip()))
        rows.append("\n".join(row))
    return "\n\n".join(rows)


def write(
    client: Gemini,
    beats,
    story: Story,
    *,
    title: str = "",
    seconds_for=None,
    temperature: float = 0.8,
) -> int:
    """
    Write every Burmese line in one pass, in order, as continuous narration.

    Mutates `beats` in place, setting `.my`. Returns how many were written.

    One call rather than one per line: a line written alone cannot refer to
    what came before it, and continuity was the thing most obviously missing.
    A failure leaves the beats as they were, so a broken call costs the Burmese
    rather than the whole script.
    """
    beats = [b for b in beats]
    if not beats:
        return 0
    seconds_for = seconds_for or (lambda b: max(1.5, b.end - b.start))

    blocks = [
        "You are writing the Burmese narration for a recap of a video"
        + (' titled "{}"'.format(title) if title else "") + ".",
        story.overview_block() if story else "",
        story.cast_block() if story else "",
        STYLE,
        "THE RECAP, LINE BY LINE\n"
        "Below is every line in order. Write the Burmese for each one. They are "
        "read out back to back over the video, so line 2 must follow on from "
        "line 1 -- if a line would read the same with the others shuffled, it "
        "is wrong.\n\n" + _line_block(beats, story, seconds_for),
        "Return one entry per line, with its index and the Burmese.\n\n"
        "About length: the character count given for each line is the room the "
        "clip has, not a target to hit. Fill it with real detail from the notes "
        "when there is more worth saying, and stop when there is not. Never pad "
        "a line with empty words to make it longer, and never let it run past "
        "its room -- the voice would still be talking when the picture moves on.",
    ]
    prompt = "\n\n".join(b for b in blocks if b and b.strip())

    try:
        data = client.generate_json(prompt, _WRITE_SCHEMA, temperature)
    except GeminiError:
        return 0

    by_index = {b.index: b for b in beats}
    written = 0
    for item in data.get("lines") or []:
        try:
            i = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        text = (item.get("my") or "").strip()
        beat = by_index.get(i)
        if beat is not None and text:
            beat.my = text
            written += 1
    return written


def needs_work(beats, seconds_for=None) -> list[int]:
    """
    Which lines are worth a second look, judged on speech rather than on English.

    A line is flagged when it is missing, when it is far too short to carry its
    clip, or when it would overrun the clip and talk over the next one. Length
    relative to the English line is deliberately NOT a criterion: the same
    content runs anywhere from 1.05x to 1.6x the English character count, so
    that comparison flags good Burmese as often as bad.
    """
    seconds_for = seconds_for or (lambda b: max(1.5, b.end - b.start))
    flagged = []
    for b in beats:
        if not b.my.strip():
            if b.en.strip():
                flagged.append(b.index)
            continue
        room = chars_for(seconds_for(b))
        if len(b.my) < SHORT_RATIO * room or len(b.my) > LONG_RATIO * room:
            flagged.append(b.index)
    return flagged


def review(
    client: Gemini,
    beats,
    story: Story,
    *,
    title: str = "",
    seconds_for=None,
    only: list[int] | None = None,
    temperature: float = 0.5,
) -> dict:
    """
    Read the Burmese back and repair what is wrong with it.

    The reviewer sees the story notes and the narration, and is asked to leave
    alone anything that is already good -- a reviewer that rewrites everything
    is just a second writer with less context, and it churns quota for nothing.

    Returns a small report; mutates `beats` in place where a line was revised.
    """
    beats = [b for b in beats if b.my.strip() or b.en.strip()]
    if only is not None:
        wanted = set(only)
        beats = [b for b in beats if b.index in wanted]
    if not beats:
        return {"checked": 0, "revised": 0, "issues": []}

    seconds_for = seconds_for or (lambda b: max(1.5, b.end - b.start))

    rows = []
    for b in beats:
        events = story.events_in(b.start, b.end) if story else []
        detail = event_block(events, limit=4) or b.en.strip()
        rows.append(
            "--- LINE {} (room: about {} characters) ---\n"
            "What actually happens here:\n{}\n"
            "The Burmese written for it:\n{}".format(
                b.index, chars_for(seconds_for(b)), detail, b.my or "(nothing)")
        )

    prompt = "\n\n".join(x for x in [
        "You are checking the Burmese narration of a video recap"
        + (' for "{}"'.format(title) if title else "") + " before it is spoken aloud.",
        story.cast_block() if story else "",
        "\n\n".join(rows),
        """CHECK EACH LINE FOR
1. Does it say what actually happens? Nothing important dropped.
2. Nothing added that the notes do not support -- no invented motives,
   feelings, dialogue or backstory.
3. Natural spoken Burmese, not a translation. Watch for English clause order,
   literary -သည်/-၏ endings, formal vocabulary where a plain word belongs, and
   English idioms carried over word for word.
4. Characters named consistently, pronouns clear about who is meant.
5. Cause and effect kept -- events connected, not listed.
6. It follows on from the line before it.
7. Good to read aloud: sentence length, clear boundaries, no symbols or
   abbreviations, natural punctuation for pauses.
8. No repetition, and no words padding it out to length.
9. It fits its room without overrunning.

Set "ok" true for a line that is already good and leave "revised" empty. Do
NOT rewrite a good line to make it different -- only fix what is actually
wrong, and when you do, change as little as possible. List what was wrong in
"issues".""",
    ] if x and x.strip())

    try:
        data = client.generate_json(prompt, _REVIEW_SCHEMA, temperature)
    except GeminiError:
        return {"checked": 0, "revised": 0, "issues": []}

    by_index = {b.index: b for b in beats}
    revised = 0
    issues: list[str] = []
    for item in data.get("lines") or []:
        try:
            i = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        beat = by_index.get(i)
        if beat is None:
            continue
        for note in item.get("issues") or []:
            note = str(note).strip()
            if note:
                issues.append(f"line {i}: {note}")
        if item.get("ok"):
            continue
        text = (item.get("revised") or "").strip()
        if text and text != beat.my:
            beat.my = text
            revised += 1

    return {"checked": len(beats), "revised": revised, "issues": issues[:40]}
