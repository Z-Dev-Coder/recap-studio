"""
The lines list, before anything has been run.

It is where a single line is played, edited, or spoken on its own -- the way
to try one before committing to a run of twenty. It read project.timeline,
which only exists once something has been run, so a script that had just been
pasted showed nothing at all.
"""

import pytest

from ytdl.web import recap_api
from ytdl.recap.project import Project


@pytest.fixture
def project(tmp_path, monkeypatch):
    p = Project(id="t", dir=tmp_path)
    p.voice_dir.mkdir(parents=True, exist_ok=True)
    p.beats = [
        {"index": 0, "start": 2.0, "end": 8.0, "my": "ပထမကြောင်း", "en": ""},
        {"index": 1, "start": 9.0, "end": 15.0, "my": "ဒုတိယကြောင်း", "en": ""},
    ]
    monkeypatch.setattr(recap_api.store, "get", lambda pid: p)
    return p


def test_a_pasted_script_lists_its_lines_immediately(project):
    out = recap_api.voice_lines("t")
    assert [r["text"] for r in out["lines"]] == ["ပထမကြောင်း", "ဒုတိယကြောင်း"]
    assert all(r["file"] == "" for r in out["lines"]), "none spoken yet, and it says so"


def test_the_language_comes_from_the_script_when_none_is_set(project):
    """A project that has never narrated has no voice_lang to read."""
    project.voice_lang = ""
    assert recap_api.voice_lines("t")["lang"] == "my"


def test_a_clip_already_made_is_found(project):
    (project.voice_dir / "line_001_my.wav").write_bytes(b"RIFF")
    rows = recap_api.voice_lines("t")["lines"]
    assert rows[0]["file"] == ""
    assert rows[1]["file"] == "line_001_my.wav", "positions must match the run's own naming"


def test_the_timeline_wins_once_there_is_one(project):
    """After a cut, positions are real rather than laid out end to end."""
    project.timeline = [
        {"index": 0, "recap_start": 40.0, "my": "ပထမကြောင်း"},
    ]
    rows = recap_api.voice_lines("t")["lines"]
    assert len(rows) == 1
    assert rows[0]["at"] == 40.0


def test_a_project_with_no_script_lists_nothing(project):
    project.beats = []
    assert recap_api.voice_lines("t")["lines"] == []
