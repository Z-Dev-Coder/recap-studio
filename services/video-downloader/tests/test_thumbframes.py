"""
Which video the thumbnail's candidate frames come from.

A 9:16 recap fronted by a 16:9 frame lifted from the source is the wrong
picture in the wrong shape. The frames have to come from the cut -- and the
beats have to be moved into the cut's own time, or they point at whatever
happens to be playing at those seconds.
"""

from ytdl.recap.pipeline import thumbnail_frames_from
from ytdl.recap.project import Project


def _project(tmp_path):
    p = Project(id="p", dir=tmp_path / "p")
    p.dir.mkdir(parents=True, exist_ok=True)
    p.source_path.write_bytes(b"source")
    p.beats = [{"index": 0, "start": 26.0, "end": 32.0, "score": 9.0}]
    return p


def test_frames_come_from_the_cut_when_there_is_one(tmp_path):
    p = _project(tmp_path)
    p.recap_path.write_bytes(b"cut")
    p.timeline = [{"index": 0, "source_start": 26.0, "source_end": 32.0,
                   "recap_start": 0.0, "recap_end": 5.8, "score": 9.0}]

    video, beats = thumbnail_frames_from(p)
    assert video == p.recap_path
    assert (beats[0]["start"], beats[0]["end"]) == (0.0, 5.8), \
        "the beat must be read in the cut's time, not the source's"
    assert beats[0]["score"] == 9.0, "ranking still comes from the script"


def test_the_source_is_used_before_there_is_a_cut(tmp_path):
    p = _project(tmp_path)
    video, beats = thumbnail_frames_from(p)
    assert video == p.source_path
    assert beats[0]["start"] == 26.0


def test_a_cut_with_no_timeline_falls_back(tmp_path):
    """Without the timeline there is no way to place the beats in the cut."""
    p = _project(tmp_path)
    p.recap_path.write_bytes(b"cut")
    p.timeline = []

    video, _ = thumbnail_frames_from(p)
    assert video == p.source_path
