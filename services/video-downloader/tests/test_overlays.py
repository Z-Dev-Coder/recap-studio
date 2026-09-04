"""
Things laid over the picture: a channel logo, free text, and the captions.

All three are drawn by the page -- Chromium is the only renderer here that
shapes Myanmar -- and composited by the same ffmpeg pass, so the service only
ever handles pictures with a place and a span.
"""

import json
from pathlib import Path

from ytdl.recap.project import Project


def _project(tmp_path) -> Project:
    return Project(id="t", dir=tmp_path)


def test_a_project_starts_with_nothing_over_the_picture(tmp_path):
    p = _project(tmp_path)
    assert p.overlays == []
    assert p.caption_look == {}


def test_overlays_and_the_caption_look_survive_a_save(tmp_path):
    p = _project(tmp_path)
    p.overlays = [{"kind": "logo", "x": 0.12, "y": 0.09, "size": 140}]
    p.caption_look = {"font": "Padauk Book", "size": 54, "box_alpha": 0.8}
    p.save()

    back = Project.load(tmp_path)
    assert back.overlays == p.overlays
    assert back.caption_look["font"] == "Padauk Book"
    assert back.caption_look["box_alpha"] == 0.8


def test_the_snapshot_says_whether_there_is_a_logo(tmp_path):
    p = _project(tmp_path)
    assert p.snapshot()["has_logo"] is False
    p.logo_path.write_bytes(b"png")
    assert p.snapshot()["has_logo"] is True


def test_a_derived_flag_is_not_read_back_as_a_field(tmp_path):
    """has_logo is computed from the disk; loading must not try to set it."""
    p = _project(tmp_path)
    p.logo_path.write_bytes(b"png")
    p.save()
    saved = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    saved["has_logo"] = True
    (tmp_path / "project.json").write_text(json.dumps(saved), encoding="utf-8")
    assert Project.load(tmp_path).logo_path.exists()      # no TypeError
