"""
A chunk that stops before the words run out.

VoxCPM sometimes ends generation early and the clip simply stops mid-thought:
measured on a real line, 25.1 seconds of audio for words needing 27, the
missing part being the closing clause. The model reports nothing, so it has to
be caught by measuring what came back.
"""

import types

import pytest

from ytdl.recap import localtts


class _Model:
    """Returns however many seconds each call is told to."""

    def __init__(self, seconds):
        self.seconds = list(seconds)
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        secs = self.seconds.pop(0) if self.seconds else 0.0
        return types.SimpleNamespace(seconds=secs)


@pytest.fixture
def engine(monkeypatch):
    def pcm(audio):
        # 24kHz, 16-bit mono, so two bytes a sample
        return b"\1\2" * int(audio.seconds * 24000)

    monkeypatch.setattr(localtts, "_pcm", pcm)
    monkeypatch.setattr(localtts, "trim_silence", lambda p, rate, **k: p)
    monkeypatch.setattr(localtts, "sample_rate", lambda m: 24000)
    monkeypatch.setattr(localtts, "wav_header", lambda pcm, rate: pcm)
    monkeypatch.setattr(localtts, "_join_pcm", lambda parts, rate, **k: b"".join(parts))


def _line(chars: int) -> str:
    return "မ" * chars


def test_a_chunk_that_stopped_early_is_spoken_again(engine, monkeypatch):
    model = _Model([4.0, 8.0])          # 4s for words needing ~8.3
    monkeypatch.setattr(localtts, "load", lambda mid=None: model)

    out = localtts.speak(_line(120))
    assert model.calls == 2, "the short one is not accepted"
    assert len(out) / 2 / 24000 == pytest.approx(8.0), "the fuller take is kept"


def test_the_longer_of_the_two_wins(engine, monkeypatch):
    """A second attempt can come back shorter still."""
    model = _Model([5.0, 2.0])
    monkeypatch.setattr(localtts, "load", lambda mid=None: model)

    out = localtts.speak(_line(120))
    assert len(out) / 2 / 24000 == pytest.approx(5.0)


def test_a_brisk_reading_is_not_mistaken_for_a_stop(engine, monkeypatch):
    """The fastest measured reading is 17.9 chars a second; that is speed."""
    model = _Model([120 / 17.9])
    monkeypatch.setattr(localtts, "load", lambda mid=None: model)

    localtts.speak(_line(120))
    assert model.calls == 1, "no retry, and no GPU minute spent on one"


def test_a_very_short_line_is_left_alone(engine, monkeypatch):
    """Under two seconds the measure is noise, not evidence."""
    model = _Model([0.4])
    monkeypatch.setattr(localtts, "load", lambda mid=None: model)

    localtts.speak(_line(20))
    assert model.calls == 1
