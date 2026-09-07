"""
How much of a line the engine is allowed to speak.

VoxCPM has no reliable stop of its own on Burmese -- its runaway guard counts
tokens, and Burmese tokenises into far more of them than Latin text -- so the
app caps each chunk. The cap has to come from the words, not from a span
guessed before anyone knew how long the words take.
"""

import pytest

from ytdl.recap import localtts, tts


def test_the_clip_length_is_dropped_when_the_cut_will_be_refitted(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(localtts, "speak",
                        lambda text, **k: seen.update(k) or b"RIFF" + b"\0" * 200)
    monkeypatch.setattr(localtts, "available", lambda: True)
    monkeypatch.setattr(tts, "clip_seconds_of", lambda p: 1.0)

    row = {"index": 0, "recap_start": 0.0, "recap_end": 12.0,
           "my": "မြန်မာ" * 60}
    tts.narrate(api_key="", timeline=[row], out_dir=tmp_path, lang="my",
                engine="voxcpm", cap_to_clip=False)

    assert seen["max_seconds"] == 0.0, \
        "a 12s guess must not cut a line that takes 27s to say"


def test_the_clip_length_still_binds_when_the_cut_is_fixed(monkeypatch, tmp_path):
    """Without refitting, a line outrunning its clip is narration nobody hears."""
    seen = {}
    monkeypatch.setattr(localtts, "speak",
                        lambda text, **k: seen.update(k) or b"RIFF" + b"\0" * 200)
    monkeypatch.setattr(localtts, "available", lambda: True)
    monkeypatch.setattr(tts, "clip_seconds_of", lambda p: 1.0)

    row = {"index": 0, "recap_start": 0.0, "recap_end": 12.0, "my": "မြန်မာ" * 60}
    tts.narrate(api_key="", timeline=[row], out_dir=tmp_path, lang="my",
                engine="voxcpm", cap_to_clip=True)

    assert seen["max_seconds"] == 12.0
