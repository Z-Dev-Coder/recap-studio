"""
Taking the narration out of the app to edit somewhere else.

Forty lines downloaded one at a time is not a workflow, and forty unlabelled
clips in an editor are not one either: nothing says which moment each belongs
to. The bundle carries the running order, the position of each line in the
recap, its length and its text.
"""

import io
import wave
import zipfile

import pytest

from ytdl.web import recap_api
from ytdl.recap.project import Project


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\0\0" * 2400)
    return buf.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    p = Project(id="t", dir=tmp_path / "proj", title="A Cartoon")
    p.voice_dir.mkdir(parents=True, exist_ok=True)
    p.voice_lang = "my"
    for i, at in ((0, 8.0), (1, 2.0)):
        (p.voice_dir / f"line_{i:03d}_my.wav").write_bytes(_wav())
    p.narration = [
        {"file": "line_000_my.wav", "at": 8.0, "seconds": 4.0, "index": 0,
         "text": "the second thing said"},
        {"file": "line_001_my.wav", "at": 2.0, "seconds": 3.0, "index": 1,
         "text": "the first thing said"},
    ]
    monkeypatch.setattr(recap_api.store, "get", lambda pid: p)
    return p


def test_the_bundle_holds_every_line_and_an_index(project):
    out = recap_api.voice_bundle("t")
    with zipfile.ZipFile(out.path) as z:
        names = z.namelist()
        index = z.read("lines.txt").decode("utf-8")
    assert len([n for n in names if n.endswith(".wav")]) == 2
    assert "lines.txt" in names
    assert "the first thing said" in index


def test_the_files_are_named_for_where_the_line_belongs(project):
    """
    A folder of line_000_my.wav says nothing about where anything goes. The
    leading number sorts the folder the way the recap runs; the time is where
    to drop the clip on the timeline.
    """
    out = recap_api.voice_bundle("t")
    with zipfile.ZipFile(out.path) as z:
        wavs = sorted(n for n in z.namelist() if n.endswith(".wav"))
    assert wavs == ["001_00m02s000_3.0s.wav", "002_00m08s000_4.0s.wav"]


def test_the_time_in_the_name_is_not_rounded_part_by_part(project):
    """75.5s formatted a piece at a time came out as 01m16s500."""
    (project.voice_dir / "line_001_my.wav").unlink()
    project.narration = [{"file": "line_000_my.wav", "at": 75.5, "seconds": 6.0,
                          "index": 0, "text": "a line"}]
    out = recap_api.voice_bundle("t")
    with zipfile.ZipFile(out.path) as z:
        assert "001_01m15s500_6.0s.wav" in z.namelist()


def test_nothing_to_download_is_said_rather_than_an_empty_zip(project):
    project.narration = []
    for clip in project.voice_dir.glob("line_*.wav"):
        clip.unlink()
    with pytest.raises(recap_api.HTTPException) as exc:
        recap_api.voice_bundle("t")
    assert exc.value.status_code == 400


def test_lines_from_a_run_that_was_stopped_can_still_be_taken(project):
    """
    project.narration is written when a RUN finishes. A run stopped half way
    leaves real clips it says nothing about, and those are the lines already
    paid for -- so they are read off the disk instead.
    """
    project.narration = []
    project.beats = [
        {"index": 0, "start": 8.0, "end": 14.0, "my": "the second thing said"},
        {"index": 1, "start": 2.0, "end": 6.0, "my": "the first thing said"},
    ]

    out = recap_api.voice_bundle("t")
    with zipfile.ZipFile(out.path) as z:
        names = sorted(n for n in z.namelist() if n.endswith(".wav"))
        index = z.read("lines.txt").decode("utf-8")

    assert len(names) == 2
    assert names[0].startswith("001_00m02s"), "position comes from the beat"
    assert "the first thing said" in index, "and so does the text"


def test_a_missing_clip_does_not_break_the_bundle(project):
    (project.voice_dir / "line_000_my.wav").unlink()
    out = recap_api.voice_bundle("t")
    with zipfile.ZipFile(out.path) as z:
        assert len([n for n in z.namelist() if n.endswith(".wav")]) == 1
