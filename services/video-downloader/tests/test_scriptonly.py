"""
A project with no video: a script to speak, and nothing else.

The narration only ever needed the script. Requiring a download to reach it
was a rule about the usual case standing in front of a legitimate one --
someone who wants a voice track and no video at all.
"""

from ytdl.recap import script as script_mod


def test_a_script_with_no_video_still_becomes_beats():
    beats = script_mod.parse_manual("တစ်ကြောင်း။\n\nနှစ်ကြောင်း။", 0.0, "my")
    assert len(beats) == 2
    assert [b.my for b in beats] == ["တစ်ကြောင်း။", "နှစ်ကြောင်း။"]


def test_the_lines_are_in_order_and_have_a_length():
    beats = script_mod.parse_manual("တစ်။\n\nနှစ်။\n\nသုံး။", 0.0, "my")
    assert [b.index for b in beats] == [0, 1, 2]
    assert all(b.end > b.start for b in beats)


def test_coverage_of_nothing_is_nothing_rather_than_an_error():
    beats = script_mod.parse_manual("တစ်။", 0.0, "my")
    assert script_mod.coverage(beats, 0.0) == 0.0


def test_a_timed_script_still_keeps_its_times_without_a_video():
    srt = "1\n00:00:10,000 --> 00:00:14,000\nတစ်ကြောင်း။\n"
    beats = script_mod.parse_manual(srt, 0.0, "my")
    assert beats[0].start == 10.0
    assert beats[0].timed
