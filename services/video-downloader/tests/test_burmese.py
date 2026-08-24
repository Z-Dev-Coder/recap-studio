"""
The Burmese writing and review stages.

The central claim these tests defend is that the Burmese is written from the
story, not translated from the English -- so several of them assert on what the
writer was TOLD, because that is what decides whether the output is narration
or a translation.

Length is checked against speech duration only. There is deliberately no test
comparing Burmese length to English length: that comparison is what the old
code did, and it was wrong.
"""

from __future__ import annotations

from ytdl.recap import burmese
from ytdl.recap.script import Beat
from ytdl.recap.story import from_dict


def flat(text: str) -> str:
    """Prompt text with its line wrapping removed, for phrase matching."""
    return " ".join(text.split()).lower()


def beats(n=3, my="", en="an english line describing the moment"):
    return [Beat(index=i, start=i * 30.0, end=i * 30.0 + 6.0, en=en, my=my)
            for i in range(n)]


def test_chars_for_uses_measured_speech_rate():
    # 14.5 chars/sec, measured from real VoxCPM output
    assert burmese.chars_for(6.0) == 87
    assert burmese.chars_for(0.0) == 20        # a floor, never zero


def test_write_fills_every_line(fake_gemini, story_reply):
    rows = beats(3)
    client = fake_gemini([{"lines": [
        {"index": i, "my": "မြန်မာစာကြောင်း " + str(i)} for i in range(3)
    ]}])
    written = burmese.write(client, rows, from_dict(story_reply, 150.0))
    assert written == 3
    assert all(b.my for b in rows)


def test_write_is_one_call_for_continuity(fake_gemini, story_reply):
    """Lines written separately cannot follow on from each other."""
    rows = beats(8)
    client = fake_gemini([{"lines": [{"index": i, "my": "x"} for i in range(8)]}])
    burmese.write(client, rows, from_dict(story_reply, 150.0))
    assert client.calls == 1


def test_write_is_given_the_story_not_the_transcript(fake_gemini, story_reply):
    rows = beats(2)
    client = fake_gemini([{"lines": []}])
    burmese.write(client, rows, from_dict(story_reply, 150.0))
    prompt = client.prompts[0]
    assert "the machine runs out of control" in prompt      # the event
    assert "because" in prompt                              # and its cause
    assert "Renn" in prompt                                 # and the cast


def test_write_marks_english_as_a_pointer_not_a_source(fake_gemini, story_reply):
    """The English may be shown, but never as text to translate."""
    rows = beats(2)
    client = fake_gemini([{"lines": []}])
    burmese.write(client, rows, from_dict(story_reply, 150.0))
    prompt = client.prompts[0]
    assert "do not follow its wording" in flat(prompt)
    assert "you are not translating" in flat(prompt)


def test_write_survives_failure_without_losing_beats(fake_gemini, gemini_error, story_reply):
    rows = beats(3, my="already here")
    client = fake_gemini([gemini_error("down")])
    assert burmese.write(client, rows, from_dict(story_reply, 150.0)) == 0
    assert all(b.my == "already here" for b in rows)


def test_write_ignores_junk_entries(fake_gemini, story_reply):
    rows = beats(2)
    client = fake_gemini([{"lines": [
        {"index": "x", "my": "bad index"},
        {"index": 99, "my": "no such beat"},
        {"index": 0, "my": ""},
        {"index": 1, "my": "good"},
    ]}])
    assert burmese.write(client, rows, from_dict(story_reply, 150.0)) == 1
    assert rows[0].my == "" and rows[1].my == "good"


# ------------------------------------------------------------------ length

def test_needs_work_flags_empty_and_mis_sized_lines():
    rows = beats(4)
    rows[0].my = ""                       # missing
    rows[1].my = "တို"                    # far too short for 6 seconds
    rows[2].my = "က" * 87                 # about right
    rows[3].my = "က" * 400                # would overrun the clip
    flagged = burmese.needs_work(rows)
    assert flagged == [0, 1, 3]


def test_length_is_judged_against_the_clip_not_the_english():
    """
    The same Burmese line is right or wrong depending only on its clip.

    This is the behaviour the old parity check got backwards: it compared
    against the English, which says nothing.
    """
    short_clip = [Beat(index=0, start=0, end=2.0, en="x" * 500, my="က" * 29)]
    long_clip = [Beat(index=0, start=0, end=20.0, en="x" * 5, my="က" * 29)]
    assert burmese.needs_work(short_clip) == []      # fits 2 seconds
    assert burmese.needs_work(long_clip) == [0]      # far too thin for 20


def test_a_line_much_shorter_than_its_english_is_not_flagged():
    """Burmese may legitimately be shorter than the English it accompanies."""
    rows = [Beat(index=0, start=0, end=6.0, en="e" * 300, my="က" * 80)]
    assert burmese.needs_work(rows) == []


# ------------------------------------------------------------------ review

def test_review_leaves_good_lines_alone(fake_gemini, story_reply):
    rows = beats(2, my="original burmese")
    client = fake_gemini([{"lines": [
        {"index": 0, "ok": True, "revised": "should be ignored"},
        {"index": 1, "ok": True},
    ]}])
    report = burmese.review(client, rows, from_dict(story_reply, 150.0))
    assert report["revised"] == 0
    assert all(b.my == "original burmese" for b in rows)


def test_review_revises_only_what_it_flags(fake_gemini, story_reply):
    rows = beats(2, my="original burmese")
    client = fake_gemini([{"lines": [
        {"index": 0, "ok": False, "revised": "fixed", "issues": ["too formal"]},
        {"index": 1, "ok": True},
    ]}])
    report = burmese.review(client, rows, from_dict(story_reply, 150.0))
    assert report["revised"] == 1
    assert rows[0].my == "fixed"
    assert rows[1].my == "original burmese"
    assert "too formal" in report["issues"][0]


def test_review_of_a_subset_touches_nothing_else(fake_gemini, story_reply):
    rows = beats(4, my="original")
    client = fake_gemini([{"lines": [{"index": 2, "ok": False, "revised": "new"}]}])
    burmese.review(client, rows, from_dict(story_reply, 150.0), only=[2])
    assert rows[2].my == "new"
    assert [b.my for b in rows if b.index != 2] == ["original"] * 3
    assert "LINE 2" in client.prompts[0] and "LINE 0" not in client.prompts[0]


def test_review_survives_failure(fake_gemini, gemini_error, story_reply):
    rows = beats(2, my="original")
    client = fake_gemini([gemini_error("timeout")])
    report = burmese.review(client, rows, from_dict(story_reply, 150.0))
    assert report["revised"] == 0
    assert all(b.my == "original" for b in rows)


def test_review_does_nothing_with_no_lines(fake_gemini, story_reply):
    client = fake_gemini([])
    report = burmese.review(client, [], from_dict(story_reply, 150.0))
    assert report == {"checked": 0, "revised": 0, "issues": []}
    assert client.calls == 0


def test_style_forbids_the_literary_register():
    assert "-သည်" in burmese.STYLE
    assert "-ပါတယ်" in burmese.STYLE
    assert "not translating" in flat(burmese.STYLE)
