"""
Leaving the ends of the source alone.

A copyright card at the front and an end title at the back are not the story.
A beat placed on one narrates a logo, and because the narration then starts
against the wrong picture, every line after it is off by the length of the
card. These check that the skipped stretches are never drawn from.
"""

from ytdl.recap.script import plan_windows, parse_manual, respread
from ytdl.recap.video import plan_fitted


def test_chapters_stay_inside_the_wanted_span():
    windows = plan_windows(491, 20, skip_start=25, skip_end=20)
    assert windows[0][0] == 25
    assert windows[-1][1] == 471
    assert all(a >= 25 and b <= 471 for a, b in windows)


def test_no_skip_still_covers_everything():
    windows = plan_windows(491, 20)
    assert (windows[0][0], windows[-1][1]) == (0.0, 491.0)


def test_absurd_skips_are_ignored_rather_than_obeyed():
    # asking to skip more than the video is a slip, not an instruction
    windows = plan_windows(60, 4, skip_start=40, skip_end=40)
    assert (windows[0][0], windows[-1][1]) == (0.0, 60.0)


def test_a_pasted_script_starts_after_the_card():
    beats = parse_manual("one\ntwo\nthree", 300, skip_start=30, skip_end=15)
    assert beats[0].start == 30
    assert beats[-1].start == 200      # the last chapter of 30s..285s


def test_relaying_an_existing_script_moves_it_off_the_card():
    rows = [{"index": i, "start": 0.0, "end": 0.0, "my": "x"} for i in range(6)]
    beats = respread(rows, 240, skip_start=20, skip_end=10)
    assert beats[0].start == 20
    assert beats[-1].end <= 230        # each beat takes part of its chapter
    assert [b.my for b in beats] == ["x"] * 6      # the words are untouched


def test_the_cut_does_not_reach_into_the_ends():
    beats = [{"start": 30.0, "end": 40.0}, {"start": 200.0, "end": 210.0}]
    plan = plan_fitted(beats, [12.0, 12.0], duration=240, first=25, last=215)
    assert all(a >= 25 and b <= 215 for a, b in plan)


def test_a_line_by_the_end_card_backs_off_it_instead_of_crossing():
    beats = [{"start": 210.0, "end": 214.0}]
    (a, b), = plan_fitted(beats, [20.0], duration=240, first=0, last=215)
    assert b <= 215 and round(b - a, 3) == 20.0
