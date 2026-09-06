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


# --------------------------------------------- both cuts, asked for at the start

def test_a_project_can_ask_for_its_pair_up_front(tmp_path):
    p = Project(id="parent", dir=tmp_path)
    p.wants_pair = True
    p.save()
    assert Project.load(tmp_path).wants_pair is True


def test_a_project_that_did_not_ask_does_not_get_one(tmp_path):
    p = Project(id="solo", dir=tmp_path)
    p.save()
    assert Project.load(tmp_path).wants_pair is False


def test_making_the_pair_hands_over_what_is_expensive(tmp_path, monkeypatch):
    """The download and the reading of the video, which is the whole point."""
    from ytdl.web import recap_api

    monkeypatch.setattr(recap_api, "push", lambda p: None)
    monkeypatch.setattr(recap_api.store, "root", tmp_path, raising=False)

    parent = recap_api.store.create("https://example.test/v", "A Cartoon")
    try:
        parent.duration = 600.0
        parent.mode = "long"
        parent.transcript = [{"start": 2.0, "end": 6.0, "text": "a logo"}]
        parent.overlays = [{"kind": "logo"}]
        parent.caption_look = {"size": 54}
        parent.source_path.write_bytes(b"video")
        parent.save()

        made = recap_api._variant(parent, "reels")
        child = recap_api.store.get(made["id"])
        try:
            assert child.made_from == parent.id
            assert child.mode == "reels"
            assert child.target_seconds == 60.0
            assert child.transcript == parent.transcript
            assert child.overlays == parent.overlays
            assert child.caption_look == parent.caption_look
            assert child.source_path.exists()
            assert child.beats == []          # its own script to write
            assert child.steps["transcript"].status == "done"
        finally:
            recap_api.store.delete(child.id)
    finally:
        recap_api.store.delete(parent.id)
