"""
The writing prompt, assembled from what the project already knows.

Copying a template, then the transcript, then typing the same four answers
again for every video is work the machine can do -- and work that goes wrong
quietly, because a stale duration in the prompt produces a script of the wrong
length and nothing says so.
"""

from ytdl.web import recap_api


def test_a_duration_reads_the_way_a_person_writes_one():
    assert recap_api._clock(0) == "00:00"
    assert recap_api._clock(75) == "01:15"
    assert recap_api._clock(609) == "10:09"
    assert recap_api._clock(3725) == "1:02:05"


def test_a_missing_duration_is_not_a_crash():
    assert recap_api._clock(None) == "00:00"


def test_the_template_is_where_the_code_looks_for_it():
    assert (recap_api.PROMPTS / "recap_burmese.md").exists()


def test_the_template_asks_for_every_field_the_endpoint_fills():
    text = (recap_api.PROMPTS / "recap_burmese.md").read_text(encoding="utf-8")
    for field in ("DURATION", "TARGET", "CONTENT_TYPE", "PLATFORM",
                  "LANGUAGE", "STYLE", "SPECIAL_STYLE", "NAMES", "TIMELINE"):
        assert "[[" + field + "]]" in text, f"the template never uses {field}"


def test_nothing_is_left_unfilled():
    """A placeholder the endpoint forgets would be pasted into the chat raw."""
    import re
    text = (recap_api.PROMPTS / "recap_burmese.md").read_text(encoding="utf-8")
    known = {"DURATION", "TARGET", "CONTENT_TYPE", "PLATFORM", "LANGUAGE",
             "STYLE", "SPECIAL_STYLE", "NAMES", "TIMELINE"}
    assert set(re.findall(r"\[\[([A-Z_]+)\]\]", text)) == known
