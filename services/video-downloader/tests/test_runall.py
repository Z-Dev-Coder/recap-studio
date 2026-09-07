"""
What "Run all" is allowed to overwrite.

Regenerating the script is the point of that button when the script was
generated. When the script was written or pasted by hand it is the opposite:
the run destroys the thing the user arrived with, and the narration and cut
made from it.
"""

from ytdl.web.recap_api import chain_steps
from ytdl.recap.project import STEPS, Project


def _project(tmp_path, by_hand: bool):
    p = Project(id="t", dir=tmp_path)
    p.beats = [{"index": 0, "start": 1.0, "end": 5.0, "my": "a line"}]
    p.script_by_hand = by_hand
    return p


def test_a_generated_script_is_regenerated(tmp_path):
    steps = chain_steps(_project(tmp_path, by_hand=False), list(STEPS))
    assert steps == list(STEPS)


def test_a_hand_written_script_survives_run_all(tmp_path):
    steps = chain_steps(_project(tmp_path, by_hand=True), list(STEPS))
    assert "script" not in steps
    assert "transcript" not in steps, "nothing downstream reads it once a script exists"
    assert steps == ["source", "voice", "video", "thumbnail", "final"]


def test_the_flag_alone_is_not_enough(tmp_path):
    """Marked by hand but with no lines, there is nothing to protect."""
    p = _project(tmp_path, by_hand=True)
    p.beats = []
    assert chain_steps(p, list(STEPS)) == list(STEPS)


def test_a_script_pasted_before_the_flag_existed_is_still_protected(tmp_path):
    """
    Projects on disk predate script_by_hand. What they do carry is the mark
    from_text() puts on every line it parses, which the generator never writes.
    """
    p = Project(id="t", dir=tmp_path)
    p.beats = [{"index": 0, "start": 1.0, "end": 5.0, "my": "a line",
                "why": "timed by hand"}]
    p.script_by_hand = False

    assert "script" not in chain_steps(p, list(STEPS))


def test_one_hand_written_line_among_generated_ones_is_enough(tmp_path):
    """Add mode mixes them, and regenerating would destroy exactly those."""
    p = Project(id="t", dir=tmp_path)
    p.beats = [{"why": "the recap turns here"},
               {"why": "written by hand"}]
    assert "script" not in chain_steps(p, list(STEPS))


def test_a_generated_script_is_not_mistaken_for_one(tmp_path):
    p = Project(id="t", dir=tmp_path)
    p.beats = [{"why": "this is where the story turns"}, {"why": ""}, {}]
    assert chain_steps(p, list(STEPS)) == list(STEPS)
