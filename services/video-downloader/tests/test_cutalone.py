"""
Cutting the recap without the narration.

The cut is normally fitted to the spoken lines, which means waiting for the
voice. Editing by hand elsewhere reverses that: the cut is wanted first, at a
length chosen by the user, and the narration is laid over it in the editor.
"""

import pytest

from ytdl.recap import pipeline
from ytdl.recap.project import Project


@pytest.fixture
def project(tmp_path):
    p = Project(id="t", dir=tmp_path)
    p.dir.mkdir(parents=True, exist_ok=True)
    p.source_path.write_bytes(b"video")
    p.duration = 600.0
    p.beats = [{"index": i, "start": i * 10.0, "end": i * 10.0 + 6.0, "my": "x"}
               for i in range(6)]
    return p


def _stub(monkeypatch, seen):
    monkeypatch.setattr(pipeline, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(pipeline.video_mod, "build",
                        lambda *a, **k: seen.update(k) or
                        {"timeline": [], "width": 1080, "height": 1920, "duration": 90.0})
    monkeypatch.setattr(pipeline, "write_subtitles", lambda p: None)
    monkeypatch.setattr(pipeline, "_reburn_captions", lambda p, cancel=None: None)


def test_the_cut_runs_with_no_narration_at_all(project, monkeypatch):
    seen = {}
    _stub(monkeypatch, seen)
    project.cut_seconds = 90.0

    pipeline.run_video(project)
    assert seen["fit_seconds"] is None, "there are no spoken lengths to fit to"
    assert seen["target_seconds"] == 90.0, "so the length asked for decides it"


def test_fitting_is_ignored_until_there_is_something_to_fit_to(project, monkeypatch):
    """The tick stays on for later; it must not block the cut now."""
    seen = {}
    _stub(monkeypatch, seen)
    project.fit_to_voice = True
    project.narration = []
    project.cut_seconds = 120.0

    pipeline.run_video(project)
    assert seen["fit_seconds"] is None
    assert seen["target_seconds"] == 120.0


def test_with_narration_the_lines_decide_the_length(project, monkeypatch):
    seen = {}
    _stub(monkeypatch, seen)
    project.fit_to_voice = True
    project.narration = [{"index": i, "seconds": 4.0, "file": f"line_{i:03d}_my.wav"}
                         for i in range(6)]

    pipeline.run_video(project)
    assert seen["fit_seconds"] is not None
    assert all(w > 4.0 for w in seen["fit_seconds"]), "each line plus its padding"
