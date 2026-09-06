"""
A script timed by hand decides where its footage comes from.

An SRT written against the video is the one case where the user knows exactly
which moment each line belongs to. Everything downstream has to treat those
times as given, not as a starting point to improve on.
"""

from ytdl.recap.script import parse_manual, respread
from ytdl.recap.video import plan_fitted

SRT = """1
00:00:29,200 --> 00:00:35,000
မီကီ နာရီစင်ကို တက်လာတယ်

2
00:01:12,500 --> 00:01:18,000
ဒေါ်နယ် စပရိန်နဲ့ ရုန်းကန်နေတယ်

3
00:02:04,000 --> 00:02:09,500
ဂူဖီ ခေါင်းလောင်းကို ရိုက်မိတယ်
"""


def test_srt_times_are_kept_exactly():
    beats = parse_manual(SRT, 300)
    assert [round(b.start, 1) for b in beats] == [29.2, 72.5, 124.0]
    assert all(b.timed for b in beats)


def test_plain_lines_are_not_marked_timed():
    beats = parse_manual("one\ntwo\nthree", 300)
    assert not any(b.timed for b in beats)


def test_a_timed_clip_starts_where_it_was_written_to():
    beats = [b.as_dict() for b in parse_manual(SRT, 300)]
    plan = plan_fitted(beats, [6.0, 6.0, 6.0], duration=300)
    # anchored at the SRT start, not centred on the middle of its span
    assert [round(a, 1) for a, _ in plan] == [29.2, 72.5, 124.0]
    assert all(round(b - a, 1) == 6.0 for a, b in plan)


def test_an_untimed_clip_is_still_centred():
    beats = [{"start": 30.0, "end": 40.0}]
    (a, b), = plan_fitted(beats, [6.0], duration=300)
    assert (round(a, 1), round(b, 1)) == (32.0, 38.0)      # centred on 35s


def test_a_timed_clip_backs_off_the_end_rather_than_crossing_it():
    beats = [{"start": 295.0, "end": 299.0, "timed": True}]
    (a, b), = plan_fitted(beats, [10.0], duration=300, last=300)
    assert b <= 300 and round(b - a, 1) == 10.0


def test_a_timed_script_is_never_respread():
    rows = [b.as_dict() for b in parse_manual(SRT, 300)]
    again = respread(rows, 300)
    assert [round(b.start, 1) for b in again] == [29.2, 72.5, 124.0]


def test_an_untimed_script_still_is():
    rows = [{"index": i, "start": 0.0, "end": 0.0, "my": "x"} for i in range(4)]
    again = respread(rows, 400)
    assert again[0].start == 0.0 and again[-1].start > 200


def test_a_trim_moves_a_timed_line_rather_than_pinning_it():
    """
    Two lines timed inside a trimmed opening once clamped to the same frame,
    so the picture played twice; ignoring the trim instead left clips coming
    from the title sequence the trim existed to remove. A line inside a
    skipped end is laid after the one before it: out of the trim, and still
    its own moment.
    """
    beats = [
        {"start": 10.0, "end": 15.0, "timed": True},
        {"start": 15.5, "end": 20.0, "timed": True},
    ]
    plan = plan_fitted(beats, [4.0, 5.0], duration=490, first=29.2, last=483)
    assert all(a >= 29.2 for a, _ in plan)   # nothing from the trimmed opening
    assert plan[0][0] != plan[1][0]          # never the same footage twice
    assert plan[0][1] <= plan[1][0]          # and never overlapping


def test_a_timed_line_outside_the_trim_keeps_its_own_moment():
    beats = [{"start": 120.0, "end": 128.0, "timed": True}]
    (a, b), = plan_fitted(beats, [6.0], duration=609, first=47.2, last=598)
    assert round(a, 1) == 120.0


def test_a_planned_beat_still_respects_the_trim():
    beats = [{"start": 10.0, "end": 20.0}]        # no timed flag
    (a, _b), = plan_fitted(beats, [4.0], duration=490, first=29.2, last=483)
    assert a >= 29.2
