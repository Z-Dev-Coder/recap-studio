"""
A step is only running if something is actually running it.

Found in the wild: a narration marked "running" for 23 minutes with the GPU
idle at 0 MiB. The service had restarted under it. Left alone it shows a
spinner and a progress bar for ever, and blocks the retry that would fix it.
"""

from ytdl.web import recap_api
from ytdl.recap.project import Project


def _project(tmp_path):
    p = Project(id="p1", dir=tmp_path)
    recap_api._running.pop("p1", None)
    return p


def test_a_running_step_with_no_worker_is_cleared(tmp_path):
    p = _project(tmp_path)
    p.mark("voice", "running", message="working...", progress=0.4)
    assert recap_api.reconcile(p) is True
    assert p.steps["voice"].status == "idle"
    assert "interrupted" in p.steps["voice"].message


def test_a_step_that_really_is_running_is_left_alone(tmp_path):
    p = _project(tmp_path)
    p.mark("voice", "running", message="line 3 of 15")
    recap_api._running["p1"] = "voice"
    try:
        assert recap_api.reconcile(p) is False
        assert p.steps["voice"].status == "running"
        assert p.steps["voice"].message == "line 3 of 15"
    finally:
        recap_api._running.pop("p1", None)


def test_a_different_step_running_does_not_protect_this_one(tmp_path):
    p = _project(tmp_path)
    p.mark("voice", "running", message="dead")
    p.mark("video", "running", message="clip 2 of 9")
    recap_api._running["p1"] = "video"
    try:
        assert recap_api.reconcile(p) is True
        assert p.steps["voice"].status == "idle"
        assert p.steps["video"].status == "running"
    finally:
        recap_api._running.pop("p1", None)


def test_nothing_running_needs_no_change(tmp_path):
    p = _project(tmp_path)
    p.mark("voice", "done", message="15 lines")
    assert recap_api.reconcile(p) is False
    assert p.steps["voice"].status == "done"
