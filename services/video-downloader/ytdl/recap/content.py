"""
What the finished piece is meant to BE, and how that changes the writing.

The pipeline is the same for every kind of piece -- read the video, pick the
moments, narrate them, check the narration. What differs is what counts as a
good moment, how the narrator speaks, what they are forbidden from doing, and
what the reviewer looks for. Those differences live here, in one profile per
content type, rather than being scattered through the prompts.

Adding a kind of piece means adding a profile. It should not mean touching the
pipeline, and it must never mean a second pipeline: a trailer and a recap
differ in what they say about a video, not in how the video is read.

The user chooses the type. Nothing here infers it -- a film can be made into a
trailer or a recap, and which one is wanted is not a property of the film.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentProfile:
    """One kind of piece: what to choose, how to tell it, what to check."""

    id: str
    label: str
    description: str          # one line, shown in the picker
    brief: str                # names the piece inside a prompt

    pick: str                 # which moments earn their place
    voice: str                # how the English narration is written
    burmese: str              # what changes in Burmese for this kind
    review: str               # what the reviewer checks, in priority order

    # Roughly how much of a clip's room the narration should use. News packs
    # information in; a trailer leaves air. Applied as a nudge to the length
    # guidance, never as a hard rule -- see burmese.chars_for.
    density: float = 1.0


RECAP = ContentProfile(
    id="recap",
    label="Recap",
    description="Explain the story from beginning to end.",
    brief="a recap that retells the whole story, ending included",
    pick=(
        "Choose the moments the story does not make sense without: the setup, "
        "each turn, the conflict, the payoff, and how it ends. Prefer a moment "
        "that changes something over one that merely looks good. Drop "
        "repetition, small talk and background business."
    ),
    voice=(
        "Tell it as a story that has already happened, to someone who has not "
        "seen it. The ending belongs in a recap -- it is not a spoiler to be "
        "protected. Keep it moving and concrete: who did what, and what it led "
        "to. Never a list of events; each line follows from the one before."
    ),
    burmese=(
        "Conversational Burmese movie-recap narration -- the way a Burmese "
        "creator explains a film to viewers. Engaging and story-driven, "
        "carried along by ordinary connectives (အဲဒီနောက်၊ ဒါပေမယ့်၊ ဒီလိုနဲ့၊ "
        "အဲဒီအချိန်မှာပဲ) used where events genuinely connect."
    ),
    review=(
        "1. story accuracy and chronology  2. character names consistent, "
        "pronouns clear  3. cause and effect kept, not a list  4. important "
        "events present, invented ones absent  5. follows on from the previous "
        "line  6. natural spoken Burmese  7. reads aloud well"
    ),
)

NEWS = ContentProfile(
    id="news",
    label="News",
    description="Report what the video shows, factually and plainly.",
    brief="a news report on what this video shows",
    pick=(
        "Choose the moments that establish the facts: what happened, who is "
        "involved, where, and when. Prefer a moment that carries information "
        "over one that is merely dramatic. Include the current state of "
        "affairs and any stated consequences."
    ),
    voice=(
        "Report it. Lead with what happened and why it matters, then the "
        "details, then the context, then where things stand now -- adapting "
        "that order to the material rather than forcing it.\n"
        "Accuracy outranks style here, and this is the strictest rule in the "
        "system:\n"
        "- Never invent a fact, quote, number, name, date, place, cause or "
        "outcome. Nothing beyond what the source establishes.\n"
        "- Keep uncertainty exactly as uncertain as the source left it. "
        "'Reportedly' does not become 'definitely'. 'Officials are "
        "investigating' does not become 'officials found'. Attribute a claim "
        "to whoever made it.\n"
        "- Keep dates and time references as the source gives them. Do not "
        "write 'today' unless the source establishes that it is today -- this "
        "script may be read long after it is written.\n"
        "No dramatisation, no suspense, no opinion, no emotive language."
    ),
    burmese=(
        "Burmese news narration: clear, professional and plain, but still "
        "understandable rather than bureaucratic. Factual constructions over "
        "dramatic ones. Phrasing of the kind Burmese news uses (အစီရင်ခံချက် "
        "တွေအရ၊ တာဝန်ရှိသူတွေက၊ လက်ရှိမှာ) -- but ONLY where the source "
        "actually supports what they assert. No movie-recap storytelling, no "
        "exaggeration, no emotional colouring."
    ),
    review=(
        "1. factual accuracy above everything  2. dates, names, numbers and "
        "places exactly as the source has them  3. uncertainty preserved -- no "
        "claim firmed up  4. nothing invented or inferred  5. no exaggeration "
        "or dramatisation  6. clear structure  7. natural spoken Burmese"
    ),
    density=1.15,
)

MOVIE_TRAILER = ContentProfile(
    id="movie_trailer",
    label="Movie trailer",
    description="A cinematic, suspenseful trailer script.",
    brief="a trailer that makes someone want to watch the whole thing",
    pick=(
        "Choose for tension, spectacle and intrigue, and order them so they "
        "escalate: quiet setup, the problem, the rising stakes, then a final "
        "moment that leaves a question hanging. Establish the premise and the "
        "people it happens to. NEVER choose the resolution."
    ),
    voice=(
        "Write a trailer voice-over, not an explanation. Short lines. Raise "
        "questions and leave them open. Build; do not summarise.\n"
        "The spoiler rule is absolute: do not reveal the ending, the twist, "
        "who wins, or how the conflict resolves. A trailer that gives those "
        "away has failed at being a trailer.\n"
        "You have more stylistic freedom here than in any other mode, but "
        "style may never assert something untrue. Do not invent characters, "
        "events, powers, relationships or places. A rhetorical line that "
        "claims no fact is fine; a dramatic line that invents one is not."
    ),
    burmese=(
        "Cinematic Burmese: shorter sentences, stronger rhythm, built on "
        "curiosity. Fragments are acceptable where they are natural Burmese "
        "and read well aloud. A pause between beats carries weight -- but "
        "sparingly; a pause after every line is a mannerism, not drama. Never "
        "give away how it ends."
    ),
    review=(
        "1. no spoilers -- ending, twist and outcome all withheld  2. nothing "
        "invented as fact  3. cinematic tone and escalating rhythm  4. opens "
        "on a hook, closes on a question  5. the genre is unmistakable  "
        "6. natural spoken Burmese, not stilted fragments  7. reads aloud well"
    ),
    density=0.8,
)

DOCUMENTARY_RECAP = ContentProfile(
    id="documentary_recap",
    label="Documentary recap",
    description="Informative, story-driven documentary narration.",
    brief="a documentary-style piece retelling what this video covers",
    pick=(
        "Choose the moments that carry the subject forward: the background "
        "that makes it make sense, the people involved, what was done or "
        "found, the evidence for it, and what followed. Prefer a moment that "
        "shows something over one that merely states it."
    ),
    voice=(
        "Explain rather than sell. Measured, curious and unhurried, but still "
        "a story -- each part earns the next. Give the context a viewer needs "
        "before the thing that depends on it.\n"
        "Stay factual: invent no dates, findings, statistics, quotes or "
        "motives. Where the source separates what is established from what is "
        "argued or guessed at, keep that separation -- a theory presented as a "
        "theory stays a theory, and a claim keeps whoever made it. More "
        "narrative than a news report, and far more factual than a trailer."
    ),
    burmese=(
        "Informative Burmese narration that still carries a listener along: "
        "more measured than a trailer, more flowing than news. Explanatory "
        "connectives where one idea genuinely leads to the next. Keep the "
        "distinction between what is known and what is proposed. No "
        "dramatisation."
    ),
    review=(
        "1. factual accuracy and correct chronology  2. context present before "
        "what depends on it  3. evidence kept with the claim it supports  "
        "4. fact, claim and theory not blurred together  5. nothing invented  "
        "6. clear narrative line  7. natural spoken Burmese"
    ),
    density=1.05,
)


NARRATOR = ContentProfile(
    id="narrator",
    label="Narrator",
    description="Voice-over that plays along with the video, not a summary of it.",
    brief="a narrator's voice-over that runs alongside the video",
    pick=(
        "Choose the moments that carry the video and let them breathe -- this "
        "narration plays over the footage rather than replacing it, so the "
        "viewer is watching what you are talking about. Prefer moments where a "
        "voice adds something: what is happening, who these people are, what "
        "it means. Skip anything that speaks for itself on screen."
    ),
    voice=(
        "You are the narrator of this video, speaking over it while it plays, "
        "to someone who is watching along with you. That is the difference "
        "from a recap: they can see it, so do not tell them what they are "
        "looking at. Add what the picture cannot say -- who someone is, why "
        "this matters, what is about to be at stake -- and then get out of the "
        "way and let a moment play.\n"
        "Speak WITH the footage, in the present as it unfolds. Leave silence "
        "where the video is doing the work. Never narrate over a moment by "
        "describing it back."
    ),
    burmese=(
        "Warm, unhurried Burmese narration of the kind heard over a film or a "
        "programme -- a person talking you through something you are both "
        "watching. Present-tense where the action is happening now. Room to "
        "breathe between thoughts rather than a wall of speech. Not the brisk "
        "delivery of a recap channel, and not the detachment of a news reader."
    ),
    review=(
        "1. adds what the picture cannot say, rather than describing it  "
        "2. written for someone watching, not someone who has not seen it  "
        "3. leaves the footage room to play  4. nothing invented  5. present "
        "and unhurried  6. natural spoken Burmese  7. reads aloud well"
    ),
    density=0.85,
)


PROFILES: dict[str, ContentProfile] = {
    p.id: p for p in (RECAP, NARRATOR, NEWS, MOVIE_TRAILER, DOCUMENTARY_RECAP)
}

DEFAULT = RECAP.id

# Older projects, and the first cut of this feature, used these names.
_ALIASES = {
    "trailer": MOVIE_TRAILER.id,
    "documentary": DOCUMENTARY_RECAP.id,
    "news_report": NEWS.id,
}


def normalise(name: str) -> str:
    """The canonical id for whatever was asked for, or the default."""
    key = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    key = _ALIASES.get(key, key)
    return key if key in PROFILES else DEFAULT


def is_valid(name: str) -> bool:
    """
    Whether this is a content type at all.

    The API refuses anything else rather than falling back, so that a typo is
    reported instead of quietly producing the wrong kind of piece -- and so
    that nothing arbitrary can be routed into a prompt.
    """
    key = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _ALIASES.get(key, key) in PROFILES


def profile_for(name: str) -> ContentProfile:
    """The profile to write with."""
    return PROFILES[normalise(name)]


def listing() -> list[dict]:
    """The picker's options."""
    return [
        {"id": p.id, "label": p.label, "description": p.description}
        for p in PROFILES.values()
    ]
