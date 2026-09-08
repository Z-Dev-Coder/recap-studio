"""
Pasting a script whose cues span more than one paragraph.

A writer laying a cue out as two paragraphs is ordinary. The parser split the
text on blank lines and skipped whatever piece had no timestamp, so every
paragraph after the first was thrown away -- silently, which is how half a
script went missing on paste with nothing to show for it.
"""

from ytdl.recap.script import _manual_srt, parse_manual


TWO_PARAGRAPHS = """1
00:00:06,000 --> 00:00:22,000
the first thing that happens.

and the part that was being dropped.

2
00:00:22,000 --> 00:00:38,000
the next cue entirely.
"""


def test_a_paragraph_after_a_blank_line_stays_in_its_cue():
    rows = _manual_srt(TWO_PARAGRAPHS)
    assert len(rows) == 2, "a blank line inside a cue does not start a new one"
    assert rows[0][2] == "the first thing that happens. and the part that was being dropped."
    assert rows[1][2] == "the next cue entirely."


def test_the_next_cues_number_is_not_swallowed_into_this_one():
    rows = _manual_srt(TWO_PARAGRAPHS)
    assert not rows[0][2].endswith("2"), "the trailing 2 belongs to the cue after it"


def test_the_timings_are_the_ones_that_were_written():
    rows = _manual_srt(TWO_PARAGRAPHS)
    assert rows[0][0] == 6.0 and rows[0][1] == 22.0
    assert rows[1][0] == 22.0 and rows[1][1] == 38.0


def test_a_digit_only_line_inside_a_cue_is_kept():
    """Only the line before the NEXT timestamp is an index."""
    rows = _manual_srt("""1
00:00:01,000 --> 00:00:05,000
before

7

after
""")
    assert rows[0][2] == "before 7 after"


def test_the_whole_thing_still_becomes_beats():
    beats = parse_manual(TWO_PARAGRAPHS, duration=60.0, lang="en")
    assert len(beats) == 2
    assert "dropped" in beats[0].en
    assert beats[0].timed is True, "written timings are taken as given"


def test_plain_lines_without_timestamps_are_unaffected():
    beats = parse_manual("one line\n\nanother line\n", duration=60.0, lang="en")
    assert [b.en for b in beats] == ["one line", "another line"]
