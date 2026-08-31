"""
How much room the local voice is given to say a line.

A narrator script -- twenty beats across eight minutes -- produced narration
with invented Burmese words and stretches of Chinese. A recap script over a
similar video did not. The difference was not the content type: it was that
the generation cap came from the CLIP length divided between chunks, so twenty
long beats gave each chunk far more room than its words needed, and the model
filled it.
"""

from __future__ import annotations

import sys
import types

import pytest

from ytdl.recap import localtts


class FakeModel:
    """Records the max_len it is asked for, and returns silence."""

    def __init__(self):
        self.asked = []

    def generate(self, **kw):
        self.asked.append(kw.get("max_len"))
        import numpy as np
        return np.zeros(1200, dtype="float32")


@pytest.fixture
def spoken(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(localtts, "load", lambda *a, **k: model)
    monkeypatch.setattr(localtts, "sample_rate", lambda *a, **k: 16000)
    return model


def test_the_room_follows_the_words(spoken):
    """Twice the text should be given roughly twice the time."""
    localtts.speak("က" * 60, max_seconds=0)
    short = spoken.asked[0]
    spoken.asked.clear()
    localtts.speak("က" * 120, max_seconds=0)
    long = spoken.asked[0]
    assert long > short * 1.6


def test_a_long_clip_no_longer_hands_out_slack(spoken):
    """
    The bug: a 25-second clip divided between chunks gave a short line ten
    seconds to fill. The words decide now, and the clip only caps.
    """
    localtts.speak("က" * 60, max_seconds=25.0)
    frames = spoken.asked[0]
    natural = 60 / localtts.MY_CHARS_PER_SECOND
    assert frames <= natural * 1.5 * localtts.FRAMES_PER_SECOND


def test_the_clip_still_caps_a_long_line(spoken):
    """A line may not outrun the footage it plays over."""
    localtts.speak("က" * 140, max_seconds=3.0)
    frames = spoken.asked[0]
    assert frames <= 3.0 * 1.25 * localtts.FRAMES_PER_SECOND + 1


def test_every_chunk_is_given_a_limit(spoken):
    """Long text is split; each piece must be bounded, not just the first."""
    localtts.speak("က" * 400, max_seconds=0)
    assert len(spoken.asked) >= 2
    assert all(n and n > 0 for n in spoken.asked)


def test_a_tiny_line_still_gets_a_floor(spoken):
    localtts.speak("ကက", max_seconds=0)
    assert spoken.asked[0] >= 24
