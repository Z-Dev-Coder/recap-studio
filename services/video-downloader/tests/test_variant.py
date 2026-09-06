"""
Cutting one source two ways: a reel beside the long recap.

One project holds one script, one narration and one cut, which is right --
a sixty-second reel and a ten-minute recap are different scripts, voiced and
cut separately. What they share is everything expensive: the download and the
reading of the video.
"""

import json

from ytdl.recap.project import Project


def test_a_project_records_where_it_came_from(tmp_path):
    p = Project(id="child", dir=tmp_path)
    p.made_from = "parent"
    p.save()
    assert Project.load(tmp_path).made_from == "parent"


def test_a_project_made_on_its_own_came_from_nothing(tmp_path):
    p = Project(id="solo", dir=tmp_path)
    p.save()
    assert Project.load(tmp_path).made_from == ""


def test_the_link_survives_a_save_with_everything_else(tmp_path):
    p = Project(id="child", dir=tmp_path)
    p.made_from = "parent"
    p.mode = "reels"
    p.target_seconds = 60.0
    p.transcript = [{"start": 0.0, "end": 4.0, "text": "shared"}]
    p.overlays = [{"kind": "logo"}]
    p.save()

    back = Project.load(tmp_path)
    assert back.made_from == "parent"
    assert back.mode == "reels"
    assert back.target_seconds == 60.0
    assert len(back.transcript) == 1
    assert back.overlays == [{"kind": "logo"}]


def test_the_snapshot_carries_it_so_the_page_can_show_it(tmp_path):
    p = Project(id="child", dir=tmp_path)
    p.made_from = "parent"
    assert p.snapshot()["made_from"] == "parent"
