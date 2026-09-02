"""
Reading a video that says nothing.

A silent cartoon has a story, it is just shown rather than spoken. These check
the frames-to-Cues path: that a description covers the stretch it was sampled
from, that a failed batch costs its own frames and not the video, and that a
long video is sampled less often rather than more expensively.
"""

from pathlib import Path

import pytest

from ytdl.recap import watch


class FakeSeer:
    """A model that can look, and reports what it was asked to look at."""

    def __init__(self, answers=None, fail_on=None):
        self.answers = answers
        self.fail_on = fail_on or set()
        self.calls = []

    def generate_json(self, prompt, schema, images=None, **kw):
        self.calls.append({"images": len(images or []), "prompt": prompt})
        if len(self.calls) in self.fail_on:
            raise RuntimeError("the model refused")
        stamps = [float(s.rstrip("s")) for s in
                  prompt.split("are: ")[1].split(".\n")[0].split(", ")]
        return {"moments": [{"at": t, "text": f"something at {t:.1f}"} for t in stamps]}


def _shots(times):
    return [watch.Shot(at=t, blob=b"jpeg") for t in times]


def test_a_description_covers_the_stretch_it_was_sampled_from():
    cues = watch.describe(FakeSeer(), _shots([2.0, 6.0, 10.0]))
    assert [(c.start, c.end) for c in cues] == [(2.0, 6.0), (6.0, 10.0), (10.0, 14.0)]
    assert cues[0].text == "something at 2.0"


def test_frames_are_batched_rather_than_sent_one_at_a_time():
    seer = FakeSeer()
    watch.describe(seer, _shots([float(i) * 4 for i in range(25)]))
    # 25 frames at 10 a batch is three requests, not twenty-five
    assert len(seer.calls) == 3
    assert [c["images"] for c in seer.calls] == [10, 10, 5]


def test_a_failed_batch_costs_its_own_frames_and_not_the_video():
    seer = FakeSeer(fail_on={2})
    cues = watch.describe(seer, _shots([float(i) * 4 for i in range(25)]))
    assert len(seer.calls) == 3
    # the ten frames of the refused batch are missing; the other fifteen are not
    assert len(cues) == 15


def test_a_frame_nobody_described_leaves_a_gap_not_a_blank():
    class Silent(FakeSeer):
        def generate_json(self, prompt, schema, images=None, **kw):
            return {"moments": []}
    assert watch.describe(Silent(), _shots([2.0, 6.0])) == []


def test_a_long_video_is_sampled_less_often_rather_than_more_expensively(tmp_path, monkeypatch):
    taken = []
    monkeypatch.setattr(watch, "frame_at",
                        lambda src, at, dest, **k: (taken.append(at),
                                                    dest.write_bytes(b"x"), dest)[-1])
    watch.sample(Path("src.mp4"), tmp_path, duration=3600, every=4.0)
    assert len(taken) <= watch.MAX_FRAMES
    assert taken[1] - taken[0] > 4.0        # the interval stretched


def test_a_short_video_keeps_the_asked_for_interval(tmp_path, monkeypatch):
    taken = []
    monkeypatch.setattr(watch, "frame_at",
                        lambda src, at, dest, **k: (taken.append(at),
                                                    dest.write_bytes(b"x"), dest)[-1])
    watch.sample(Path("src.mp4"), tmp_path, duration=60, every=4.0)
    assert round(taken[1] - taken[0], 2) == 4.0


def test_a_model_that_rounds_a_timestamp_still_lands_on_its_frame():
    class Rounder(FakeSeer):
        def generate_json(self, prompt, schema, images=None, **kw):
            return {"moments": [{"at": 2, "text": "rounded"},
                                {"at": 6, "text": "also rounded"}]}
    cues = watch.describe(Rounder(), _shots([2.4, 6.4]))
    assert [c.text for c in cues] == ["rounded", "also rounded"]
