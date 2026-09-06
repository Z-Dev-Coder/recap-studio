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


def test_every_placeholder_in_the_template_is_one_the_endpoint_fills():
    """
    The failure that matters is a placeholder pasted into the chat raw. The
    reverse -- the endpoint offering a value the template no longer uses --
    is harmless, and happens whenever a new version of the prompt drops a
    field.
    """
    import re
    text = (recap_api.PROMPTS / "recap_burmese.md").read_text(encoding="utf-8")
    used = set(re.findall(r"\[\[([A-Z_]+)\]\]", text))
    filled = {"DURATION", "TARGET", "CONTENT_TYPE", "PLATFORM", "LANGUAGE",
              "STYLE", "SPECIAL_STYLE", "NAMES", "SOURCE_TITLE", "TIMELINE"}
    assert used <= filled, f"the template uses {used - filled}, which nothing fills"


