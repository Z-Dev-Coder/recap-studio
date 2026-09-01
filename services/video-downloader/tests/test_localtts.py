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


# ------------------------------------------------- does the reference say that?

def make_wav(path, seconds, rate=24000):
    import wave, struct
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return path


def test_a_clip_matching_its_line_is_trusted(tmp_path):
    """30 Burmese characters is about two seconds."""
    clip = make_wav(tmp_path / "ok.wav", 2.1)
    assert localtts._plausible_reference(clip, "က" * 30)


def test_a_clip_that_rambles_past_its_line_is_not(tmp_path):
    """
    The real case: 9.1 seconds of audio labelled as a two-second sentence. The
    model carried on past the line, and the extra was not Burmese.
    """
    clip = make_wav(tmp_path / "long.wav", 9.1)
    assert not localtts._plausible_reference(clip, "က" * 30)


def test_a_clip_far_too_short_is_not_trusted(tmp_path):
    clip = make_wav(tmp_path / "short.wav", 0.4)
    assert not localtts._plausible_reference(clip, "က" * 200)


def test_a_little_either_way_is_fine(tmp_path):
    """Speakers vary; the check is for clips that say something else."""
    for seconds in (1.6, 2.0, 3.5):
        clip = make_wav(tmp_path / f"n{seconds}.wav", seconds)
        assert localtts._plausible_reference(clip, "က" * 30), seconds


def test_an_unreadable_clip_is_given_the_benefit_of_the_doubt(tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav file")
    assert localtts._plausible_reference(junk, "က" * 30)


def test_a_reference_with_no_text_is_not_judged(tmp_path):
    """With no claim about what it says, there is nothing to contradict."""
    clip = make_wav(tmp_path / "any.wav", 30.0)
    assert localtts._plausible_reference(clip, "")


# ------------------------------------------------------- silence in the clips

def tone(seconds, rate=16000, level=6000):
    import numpy as np
    return (np.random.randn(int(rate * seconds)) * level).astype("int16")


def quiet(seconds, rate=16000):
    import numpy as np
    return np.zeros(int(rate * seconds), dtype="int16")


def joined(*parts):
    import numpy as np
    return np.concatenate(parts).tobytes()


def test_padding_at_both_ends_is_removed():
    """
    Measured on real narration: up to 1.4 seconds before the first word. That
    silence was counted as part of the line, so footage was cut for it and the
    app's own lead-in was added on top.
    """
    rate = 16000
    out = localtts.trim_silence(joined(quiet(1.4), tone(1.0), quiet(0.2)), rate)
    seconds = len(out) // 2 / rate
    assert 1.0 <= seconds <= 1.2      # the speech, plus a little kept either side


def test_a_clip_that_is_already_tight_is_left_as_it_is():
    rate = 16000
    pcm = joined(tone(2.0))
    assert len(localtts.trim_silence(pcm, rate)) == len(pcm)


def test_silence_is_kept_at_the_edges_so_speech_is_not_clipped():
    rate = 16000
    out = localtts.trim_silence(joined(quiet(0.5), tone(1.0), quiet(0.5)), rate)
    assert len(out) // 2 / rate > 1.0


def test_a_silent_clip_is_not_emptied():
    """Nothing but silence is a failure to report, not something to delete."""
    rate = 16000
    pcm = joined(quiet(1.0))
    assert len(localtts.trim_silence(pcm, rate)) == len(pcm)


def test_an_empty_clip_survives():
    assert localtts.trim_silence(b"", 16000) == b""
