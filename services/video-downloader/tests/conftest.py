"""
Shared fakes for the recap tests.

Nothing here touches the network, ffmpeg or the filesystem beyond tmp_path.
The Gemini client is replaced by a scripted stand-in, so a test can say what
the model returns -- including the ways it returns garbage, which is most of
what these tests are for.

None of the fixtures name a real film, character or genre: the pipeline has to
work on videos nobody has seen yet, so the tests use invented material and
check structure and behaviour rather than particular words.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ytdl.recap.gemini import GeminiError          # noqa: E402
from ytdl.recap.transcript import Cue              # noqa: E402


class FakeGemini:
    """
    A Gemini that returns what the test tells it to, in order.

    `replies` is a list of dicts (returned as-is) or exceptions (raised). Every
    prompt is recorded so a test can assert on what the model was actually
    asked -- which is the only way to check that, say, the Burmese writer was
    never shown the English as source text.
    """

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.prompts: list[str] = []
        self.images: list[int] = []
        self.schemas: list[dict] = []

    def generate_json(self, prompt, schema, temperature=0.7, images=None):
        self.prompts.append(prompt)
        self.images.append(len(images or []))
        self.schemas.append(schema)
        if not self.replies:
            return {}
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def calls(self) -> int:
        return len(self.prompts)


@pytest.fixture
def fake_gemini():
    return FakeGemini


@pytest.fixture
def gemini_error():
    return GeminiError


def cues(*rows) -> list[Cue]:
    """cues((0, 2, "text"), ...) -> [Cue, ...]"""
    return [Cue(start=s, end=e, text=t) for s, e, t in rows]


@pytest.fixture
def make_cues():
    return cues


@pytest.fixture
def dialogue_cues():
    """A talky video: continuous speech, few gaps."""
    return cues(*[
        (i * 3.0, i * 3.0 + 2.8, f"line of dialogue number {i}")
        for i in range(40)
    ])


@pytest.fixture
def sparse_cues():
    """A video that shows more than it says: long silences between lines."""
    return cues(
        (5.0, 7.0, "someone speaks once"),
        (60.0, 62.5, "and again much later"),
        (150.0, 152.0, "and once more near the end"),
    )


@pytest.fixture
def story_reply():
    """A well-formed story analysis, deliberately about nothing recognisable."""
    return {
        "video_type": "animated short",
        "setting": "a workshop and the field outside it",
        "premise": "two builders test a machine and lose control of it.",
        "dialogue_weight": "sparse",
        "ending": "the machine is shut down and the pair walk away.",
        "characters": [
            {"name": "Renn", "description": "the builder", "role": "protagonist"},
            {"name": "Odie", "description": "the assistant", "role": "ally"},
        ],
        "events": [
            {
                "start": 0.0, "end": 30.0,
                "characters": ["Renn"], "location": "workshop",
                "event": "Renn assembles the machine",
                "cause": "", "consequence": "the machine can now be switched on",
                "conflict": "", "visual_only": True,
                "importance": "high", "evidence": "shown assembling it on screen",
            },
            {
                "start": 30.0, "end": 90.0,
                "characters": ["Renn", "Odie"], "location": "field",
                "event": "the machine runs out of control",
                "cause": "Renn switched it on without testing it",
                "consequence": "the field is torn up",
                "conflict": "the pair against their own machine",
                "visual_only": False,
                "importance": "high", "evidence": "'shut it off'",
            },
            {
                "start": 90.0, "end": 150.0,
                "characters": ["Odie"], "location": "field",
                "event": "Odie shuts the machine down",
                "cause": "the field is being destroyed",
                "consequence": "the danger ends",
                "conflict": "", "visual_only": True,
                "importance": "high", "evidence": "shown pulling the lever",
            },
        ],
    }
