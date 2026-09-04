"""
Giving the GPU back, and not lying about work that stopped.

The voice model holds 5.9GB of a 6GB card. Keeping it between lines is right;
keeping it after the run is what makes the next press fail on a machine that
looks idle.
"""

import json

from ytdl.recap import localtts
from ytdl.recap.project import Project


def test_releasing_with_nothing_loaded_is_harmless():
    localtts._model = None
    assert localtts.release() is False


def test_releasing_drops_the_model(monkeypatch):
    localtts._model = object()
    localtts._model_id = "openbmb/VoxCPM2"
    assert localtts.release() is True
    assert localtts._model is None
    assert localtts._model_id == ""
    assert localtts.release() is False        # and stays dropped


def test_a_step_saved_as_running_does_not_come_back_running(tmp_path):
    p = Project(id="t", dir=tmp_path)
    p.mark("voice", "running", message="line 4 of 20", progress=0.2)
    p.save()

    back = Project.load(tmp_path)
    assert back.steps["voice"].status == "idle"
    assert back.steps["voice"].progress == 0.0
    assert "interrupted" in back.steps["voice"].message


def test_a_finished_step_comes_back_finished(tmp_path):
    p = Project(id="t", dir=tmp_path)
    p.mark("voice", "done", message="20 lines")
    p.save()
    assert Project.load(tmp_path).steps["voice"].status == "done"
