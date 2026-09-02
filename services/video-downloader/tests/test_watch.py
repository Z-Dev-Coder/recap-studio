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


def test_what_has_been_read_is_handed_back_as_it_goes():
    """Thirteen batches is minutes of waiting; an empty box reads as nothing."""
    seen = []
    watch.describe(FakeSeer(), _shots([float(i) * 4 for i in range(25)]),
                   on_progress=lambda done, total, cues=None: seen.append(len(cues or [])))
    assert seen == [10, 20, 25]          # growing, not all at the end


def test_a_failed_batch_still_reports_what_came_before_it():
    seen = []
    watch.describe(FakeSeer(fail_on={2}), _shots([float(i) * 4 for i in range(25)]),
                   on_progress=lambda done, total, cues=None: seen.append(len(cues or [])))
    assert seen == [10, 10, 15]


# ------------------------------------------------ finishing an interrupted read

def test_frames_a_previous_read_described_are_not_paid_for_twice():
    seer = FakeSeer()
    shots = _shots([float(i) * 4 for i in range(25)])
    known = {s.at: "already known" for s in shots[:20]}
    cues = watch.describe(seer, shots, known=known)
    assert len(seer.calls) == 1                 # only the five that were missing
    assert seer.calls[0]["images"] == 5
    assert len(cues) == 25                      # but the answer covers everything


def test_a_read_with_nothing_left_to_do_asks_for_nothing():
    seer = FakeSeer()
    shots = _shots([2.0, 6.0])
    cues = watch.describe(seer, shots, known={2.0: "a", 6.0: "b"})
    assert seer.calls == []
    assert [c.text for c in cues] == ["a", "b"]


def test_failures_are_reported_rather_than_swallowed():
    problems = []
    watch.describe(FakeSeer(fail_on={2}), _shots([float(i) * 4 for i in range(25)]),
                   problems=problems)
    assert len(problems) == 1
    assert problems[0].startswith("40s-76s")    # which stretch went missing


def test_a_daily_cap_stops_the_read_instead_of_firing_doomed_requests():
    class Capped(FakeSeer):
        def generate_json(self, prompt, schema, images=None, **kw):
            self.calls.append({"images": len(images or [])})
            raise RuntimeError("Gemini free-tier daily limit reached for this model")
    seer = Capped()
    problems = []
    watch.describe(seer, _shots([float(i) * 4 for i in range(50)]), problems=problems)
    assert len(seer.calls) == 1        # not five
    assert len(problems) == 1


def test_a_previous_read_is_matched_to_the_frames_being_sampled_now():
    shots = _shots([2.0, 6.0, 10.0])
    rows = [{"start": 2.0, "text": "kept"},
            {"start": 99.0, "text": "from a different sampling"},
            {"start": 6.0, "text": ""}]
    assert watch.already(rows, shots) == {2.0: "kept"}
