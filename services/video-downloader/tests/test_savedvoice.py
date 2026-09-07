"""
The channel voice, and the projects that already existed when it was chosen.

Choosing a voice only ever affected projects created afterwards, so the same
clip had to be uploaded once per video -- and each upload read the transcript
box at the moment the file was picked, which is how the saved copy kept ending
up with an empty line beside it.
"""

import pytest

from ytdl.web import recap_api
from ytdl.recap.project import Project


@pytest.fixture
def project(tmp_path, monkeypatch):
    p = Project(id="t", dir=tmp_path / "proj")
    p.voice_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(recap_api, "SAVED_VOICE", tmp_path / "voice.wav")
    monkeypatch.setattr(recap_api, "SAVED_VOICE_TEXT", tmp_path / "voice.txt")
    monkeypatch.setattr(recap_api.store, "get", lambda pid: p)
    monkeypatch.setattr(recap_api, "push", lambda *a, **k: None)
    return p


def test_a_project_can_take_the_saved_voice(project, tmp_path):
    recap_api.SAVED_VOICE.write_bytes(b"RIFFclip")
    recap_api.SAVED_VOICE_TEXT.write_text("what it says", encoding="utf-8")

    out = recap_api.use_saved_voice("t")
    assert out["said"] is True
    assert project.voice_reference == "reference.wav"
    assert project.voice_reference_text == "what it says"
    assert (project.voice_dir / "reference.wav").read_bytes() == b"RIFFclip"
    assert project.steps["voice"].status == "idle", "the old narration is stale now"


def test_taking_a_voice_that_was_never_saved_says_so(project):
    with pytest.raises(recap_api.HTTPException) as exc:
        recap_api.use_saved_voice("t")
    assert exc.value.status_code == 400


def test_the_line_typed_afterwards_reaches_the_saved_voice(project):
    """The clip is uploaded first and described second. Both must be kept."""
    recap_api.SAVED_VOICE.write_bytes(b"RIFFclip")
    recap_api.SAVED_VOICE_TEXT.write_text("", encoding="utf-8")
    (project.voice_dir / "reference.wav").write_bytes(b"RIFFclip")

    recap_api.edit(
        "t", recap_api.EditRequest(voice_reference_text="  what it says  "))

    assert recap_api.SAVED_VOICE_TEXT.read_text(encoding="utf-8") == "what it says"


def test_a_different_clip_does_not_overwrite_the_saved_line(project):
    """This project uses its own voice, so its line is not the channel's."""
    recap_api.SAVED_VOICE.write_bytes(b"RIFFclip")
    recap_api.SAVED_VOICE_TEXT.write_text("the channel line", encoding="utf-8")
    (project.voice_dir / "reference.wav").write_bytes(b"RIFFsomethingelse")

    recap_api.edit(
        "t", recap_api.EditRequest(voice_reference_text="only for this video"))

    assert recap_api.SAVED_VOICE_TEXT.read_text(encoding="utf-8") == "the channel line"


def test_choosing_a_voice_reaches_projects_that_already_existed(tmp_path, monkeypatch):
    """The whole point: it is the channel's voice, not this video's."""
    older = Project(id="a", dir=tmp_path / "a")
    newer = Project(id="b", dir=tmp_path / "b")
    for p in (older, newer):
        p.voice_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(recap_api, "SAVED_VOICE", tmp_path / "voice.wav")
    monkeypatch.setattr(recap_api, "SAVED_VOICE_TEXT", tmp_path / "voice.txt")
    monkeypatch.setattr(recap_api.store, "projects", lambda: [older, newer])
    monkeypatch.setattr(recap_api, "push", lambda *a, **k: None)
    recap_api.SAVED_VOICE.write_bytes(b"RIFFclip")
    recap_api.SAVED_VOICE_TEXT.write_text("what it says", encoding="utf-8")

    assert recap_api.spread_saved_voice() == 2
    for p in (older, newer):
        assert p.voice_reference == "reference.wav"
        assert p.voice_reference_text == "what it says"


def test_a_project_that_went_back_to_the_models_voice_is_left_alone(project, tmp_path):
    recap_api.SAVED_VOICE.write_bytes(b"RIFFclip")
    recap_api.SAVED_VOICE_TEXT.write_text("what it says", encoding="utf-8")
    project.voice_cleared = True

    assert recap_api.adopt_saved_voice(project) is False
    assert project.voice_reference == ""


def test_a_project_with_its_own_clip_keeps_it(project, tmp_path):
    recap_api.SAVED_VOICE.write_bytes(b"RIFFclip")
    recap_api.SAVED_VOICE_TEXT.write_text("the channel line", encoding="utf-8")
    (project.voice_dir / "reference.wav").write_bytes(b"RIFFmine")
    project.voice_reference = "reference.wav"
    project.voice_reference_text = "my own line"

    assert recap_api.adopt_saved_voice(project) is False
    assert (project.voice_dir / "reference.wav").read_bytes() == b"RIFFmine"
    assert project.voice_reference_text == "my own line"


def test_a_project_already_on_the_saved_clip_receives_the_line_later(project, tmp_path):
    """The clip is uploaded first; the line is typed afterwards."""
    recap_api.SAVED_VOICE.write_bytes(b"RIFFclip")
    recap_api.SAVED_VOICE_TEXT.write_text("", encoding="utf-8")
    (project.voice_dir / "reference.wav").write_bytes(b"RIFFclip")
    project.voice_reference = "reference.wav"

    recap_api.SAVED_VOICE_TEXT.write_text("what it says", encoding="utf-8")
    assert recap_api.adopt_saved_voice(project) is True
    assert project.voice_reference_text == "what it says"
