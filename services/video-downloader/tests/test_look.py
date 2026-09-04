"""
A channel's own look, kept once and used by every new video.

The same logo in the same corner and the same caption style, every week.
Placing it again for each video produces nothing.
"""

import json

from ytdl.web import recap_api
from ytdl.recap.project import Project


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(recap_api, "SAVED_LOOK", tmp_path / "look.json")
    monkeypatch.setattr(recap_api, "SAVED_LOGO", tmp_path / "logo.png")
    return Project(id="p", dir=tmp_path / "p")


def test_a_new_project_starts_from_the_kept_look(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.dir.mkdir(parents=True, exist_ok=True)
    recap_api.SAVED_LOOK.write_text(json.dumps({
        "overlays": [{"kind": "logo", "x": 0.12, "y": 0.09, "size": 140}],
        "caption_look": {"font": "Padauk Book", "size": 54},
        "caption_x": 0.5, "caption_y": 0.8,
    }), encoding="utf-8")
    recap_api.SAVED_LOGO.write_bytes(b"png-bytes")

    recap_api.apply_saved_look(p)
    assert p.overlays[0]["kind"] == "logo"
    assert p.caption_look["font"] == "Padauk Book"
    assert p.caption_y == 0.8
    assert p.logo_path.read_bytes() == b"png-bytes"


def test_a_project_that_has_its_own_look_is_left_alone(tmp_path, monkeypatch):
    """Reapplying it later would throw away a layout made for this video."""
    p = _fresh(tmp_path, monkeypatch)
    p.dir.mkdir(parents=True, exist_ok=True)
    p.overlays = [{"kind": "text", "text": "just for this one"}]
    recap_api.SAVED_LOOK.write_text(json.dumps({
        "overlays": [{"kind": "logo"}], "caption_look": {}}), encoding="utf-8")

    recap_api.apply_saved_look(p)
    assert p.overlays == [{"kind": "text", "text": "just for this one"}]


def test_no_kept_look_changes_nothing(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.dir.mkdir(parents=True, exist_ok=True)
    recap_api.apply_saved_look(p)
    assert p.overlays == []
    assert p.caption_look == {}


def test_a_broken_look_file_is_ignored_rather_than_fatal(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.dir.mkdir(parents=True, exist_ok=True)
    recap_api.SAVED_LOOK.write_text("{ not json", encoding="utf-8")
    recap_api.apply_saved_look(p)          # must not raise
    assert p.overlays == []
