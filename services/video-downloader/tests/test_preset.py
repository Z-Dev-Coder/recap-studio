"""
Deciding a whole run before it starts.

Two things used to make one click impossible. Some settings were only read
from whichever button was pressed, so a chain ran them at the request's
defaults no matter what the project said; and the saved preset carried only
the layout, so every new video had its length, shape and mix set by hand.
"""

import json

import pytest

from ytdl.web import recap_api
from ytdl.recap.project import Project


@pytest.fixture
def project(tmp_path, monkeypatch):
    p = Project(id="t", dir=tmp_path / "proj")
    p.dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(recap_api, "SAVED_LOOK", tmp_path / "look.json")
    monkeypatch.setattr(recap_api, "SAVED_LOGO", tmp_path / "logo.png")
    monkeypatch.setattr(recap_api.store, "get", lambda pid: p)
    monkeypatch.setattr(recap_api, "push", lambda *a, **k: None)
    return p


def test_the_chain_runs_with_the_projects_answers_not_the_buttons(project, monkeypatch):
    project.source_quality = "2160"
    project.frame_count = 40
    seen = {}
    monkeypatch.setattr(recap_api, "run_chain",
                        lambda pid, steps, options: seen.update(options))

    recap_api.run_all("t", recap_api.StepRequest())
    assert seen["quality"] == "2160", "1080 is the request's default, not the project's"
    assert seen["frame_count"] == 40


def test_the_preset_carries_the_settings_not_only_the_layout(project):
    project.shape = "reels"
    project.narration_speed = 1.2
    project.burn_captions = True
    project.cover_lead = 0.6

    recap_api.keep_look("t")
    saved = json.loads(recap_api.SAVED_LOOK.read_text(encoding="utf-8"))["settings"]
    assert saved["shape"] == "reels"
    assert saved["narration_speed"] == 1.2
    assert saved["burn_captions"] is True
    assert saved["cover_lead"] == 0.6


def test_a_new_project_starts_from_the_preset(project, tmp_path):
    project.shape = "reels"
    project.narration_speed = 1.2
    project.frame_count = 40
    recap_api.keep_look("t")

    fresh = Project(id="n", dir=tmp_path / "new")
    fresh.dir.mkdir(parents=True, exist_ok=True)
    recap_api.apply_saved_look(fresh)
    assert fresh.shape == "reels"
    assert fresh.narration_speed == 1.2
    assert fresh.frame_count == 40


def test_the_preset_cannot_reach_a_field_it_was_never_meant_to(project, tmp_path):
    """An old or hand-edited preset must not set arbitrary attributes."""
    recap_api.SAVED_LOOK.write_text(json.dumps({
        "settings": {"shape": "reels", "beats": [{"my": "not yours to set"}],
                     "gone_in_this_version": 1},
    }), encoding="utf-8")

    fresh = Project(id="n", dir=tmp_path / "new2")
    fresh.dir.mkdir(parents=True, exist_ok=True)
    recap_api.apply_saved_look(fresh)
    assert fresh.shape == "reels"
    assert fresh.beats == [], "the preset is settings, not content"
