"""
Fixing one line's words in the voice step.

Hearing a line is when its wording turns out to be wrong -- a name the engine
cannot pronounce, a sentence that runs too long. Sending the whole script back
to change one word clears the cut and the narration with it, which means
respeaking every line.
"""

import pytest

from ytdl.web import recap_api
from ytdl.recap.project import Project


@pytest.fixture
def project(tmp_path, monkeypatch):
    p = Project(id="t", dir=tmp_path)
    p.dir.mkdir(parents=True, exist_ok=True)
    p.voice_lang = "my"
    p.beats = [
        {"index": 0, "start": 0.0, "end": 6.0, "my": "ပထမကြောင်း", "en": "first"},
        {"index": 1, "start": 6.0, "end": 12.0, "my": "ဒုတိယကြောင်း", "en": "second"},
    ]
    p.timeline = [dict(b, recap_start=b["start"], recap_end=b["end"]) for b in p.beats]
    p.narration = [{"file": "line_000_my.wav", "index": 0, "at": 0.0, "seconds": 3.0}]
    monkeypatch.setattr(recap_api.store, "get", lambda pid: p)
    monkeypatch.setattr(recap_api, "push", lambda *a, **k: None)
    monkeypatch.setattr(recap_api.pipeline, "write_subtitles", lambda p: None)
    monkeypatch.setattr(recap_api.pipeline, "write_text_assets", lambda p: None)
    return p


def test_one_line_changes_and_nothing_else_does(project):
    out = recap_api.edit_line_text(
        "t", 1, recap_api.LineTextRequest(text="  အသစ်  ", lang="my"))

    assert out["changed"] is True
    assert project.beats[1]["my"] == "အသစ်", "and the spacing is tidied"
    assert project.beats[0]["my"] == "ပထမကြောင်း", "the other line is untouched"
    assert project.narration, "the clips already made are not thrown away"


def test_the_new_words_reach_the_timeline_as_well(project):
    """
    The timeline is what gets spoken and what the captions are built from. Left
    behind, the line would be respoken from the old text and nothing would
    appear to have changed.
    """
    recap_api.edit_line_text("t", 1, recap_api.LineTextRequest(text="အသစ်", lang="my"))
    assert project.timeline[1]["my"] == "အသစ်"


def test_an_empty_line_is_refused(project):
    with pytest.raises(recap_api.HTTPException) as exc:
        recap_api.edit_line_text("t", 0, recap_api.LineTextRequest(text="   "))
    assert exc.value.status_code == 400


def test_editing_marks_the_render_stale_but_not_the_cut(project):
    """The words changed, not how long the line takes -- that is the respeak."""
    recap_api.edit_line_text("t", 0, recap_api.LineTextRequest(text="အသစ်", lang="my"))
    assert project.steps["final"].status == "idle"


def test_the_edit_makes_the_script_the_users(project):
    """Run all must not then regenerate over the top of the correction."""
    recap_api.edit_line_text("t", 0, recap_api.LineTextRequest(text="အသစ်", lang="my"))
    assert project.has_own_script() is True


def test_a_line_can_be_respoken_before_the_cut_exists(project, monkeypatch):
    """Speaking comes before cutting, so refusing without a timeline sent the
    user back to respeak every line -- the cost this endpoint avoids."""
    project.timeline = []
    monkeypatch.setattr(recap_api, "load_settings", lambda: {})
    monkeypatch.setattr(recap_api.tts_mod, "narrate",
                        lambda **k: [{"file": "line_001_my.wav", "index": 1,
                                      "at": 6.0, "seconds": 2.0, "text": "x"}])

    out = recap_api.regenerate_line("t", 1, lang="my")
    assert out["ok"] is True
    assert project.timeline, "a stand-in is laid out instead of refusing"


def test_respeaking_one_line_reports_itself(project, monkeypatch):
    """
    A line takes about a minute, four if the model loads first. Saying nothing
    for that long is indistinguishable from a hang.
    """
    seen = []
    monkeypatch.setattr(recap_api, "load_settings", lambda: {})
    monkeypatch.setattr(recap_api, "_claim", lambda pid, what: True)
    monkeypatch.setattr(recap_api, "_release", lambda pid: None)
    monkeypatch.setattr(recap_api, "push",
                        lambda p: seen.append(p.steps["voice"].message))
    monkeypatch.setattr(recap_api.tts_mod, "narrate",
                        lambda **k: [{"file": "line_001_my.wav", "index": 1,
                                      "at": 6.0, "seconds": 2.5, "text": "x"}])

    recap_api.regenerate_line("t", 1, lang="my")

    assert any("speaking line 2" in m for m in seen), "it says what it is doing"
    assert project.steps["voice"].status == "done", "and stops saying it afterwards"
    assert "2.5s" in project.steps["voice"].message


def test_a_failed_line_does_not_leave_the_step_spinning(project, monkeypatch):
    monkeypatch.setattr(recap_api, "load_settings", lambda: {})
    monkeypatch.setattr(recap_api, "_claim", lambda pid, what: True)
    monkeypatch.setattr(recap_api, "_release", lambda pid: None)
    monkeypatch.setattr(recap_api, "push", lambda p: None)

    def boom(**k):
        raise RuntimeError("the engine fell over")
    monkeypatch.setattr(recap_api.tts_mod, "narrate", boom)

    with pytest.raises(recap_api.HTTPException):
        recap_api.regenerate_line("t", 1, lang="my")
    assert project.steps["voice"].status == "error"
    assert "line 2" in project.steps["voice"].error
