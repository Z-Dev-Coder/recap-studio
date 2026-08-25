"""
The short preview of the final mix.

The bug these guard against was a missing import: run_preview called cut()
without pipeline importing it, so the endpoint answered 500 and the reason
lived only in a log nobody reads. A test that runs the function with the media
layer stubbed catches that class of mistake without touching ffmpeg.
"""

from __future__ import annotations

import pytest

from ytdl.recap import pipeline


class FakeProject:
    """Only what run_preview actually reads."""

    def __init__(self, tmp_path, narration=True, timeline=True):
        self.dir = tmp_path
        self.voice_dir = tmp_path / "voice"
        self.voice_dir.mkdir(parents=True, exist_ok=True)
        self.recap_path = tmp_path / "recap.mp4"
        self.recap_path.write_bytes(b"video")
        self.captioned_path = tmp_path / "captioned.mp4"
        self.voice_lang = "my"
        self.original_volume = 0.25
        self.narration_volume = 1.0
        self.narration_speed = 1.0
        self.narration = []
        self.timeline = []
        if narration:
            clip = self.voice_dir / "line_000_my.wav"
            clip.write_bytes(b"RIFF" + b"0" * 2000)
            self.narration = [{"file": clip.name, "index": 0, "at": 0.4}]
        if timeline:
            self.timeline = [{"index": 0, "recap_start": 0.0, "recap_end": 10.0}]


@pytest.fixture
def stub_media(monkeypatch, tmp_path):
    calls = {"cut": [], "mux": []}

    def fake_cut(src, dest, start, end, **k):
        calls["cut"].append((float(start), float(end)))
        dest.write_bytes(b"clip")
        return dest

    def fake_mux(video, clips, dest, **k):
        calls["mux"].append([c["at"] for c in clips])
        dest.write_bytes(b"final")
        return dest

    monkeypatch.setattr(pipeline, "cut", fake_cut)
    monkeypatch.setattr(pipeline, "mux_narration", fake_mux)
    monkeypatch.setattr(pipeline, "have_ffmpeg", lambda: True)
    return calls


def test_a_preview_is_rendered(tmp_path, stub_media):
    p = FakeProject(tmp_path)
    dest = pipeline.run_preview(p, seconds=25.0)
    assert dest.exists()
    assert dest.name == "preview.mp4"


def test_only_the_opening_is_cut(tmp_path, stub_media):
    p = FakeProject(tmp_path)
    pipeline.run_preview(p, seconds=20.0)
    assert stub_media["cut"] == [(0.0, 20.0)]


def test_the_window_is_kept_sane(tmp_path, stub_media):
    """Neither a two-second preview nor a ten-minute one is useful."""
    p = FakeProject(tmp_path)
    pipeline.run_preview(p, seconds=1.0)
    assert stub_media["cut"][-1][1] >= 5.0
    pipeline.run_preview(p, seconds=9999.0)
    assert stub_media["cut"][-1][1] <= 90.0


def test_narration_inside_the_window_is_laid_down(tmp_path, stub_media):
    p = FakeProject(tmp_path)
    pipeline.run_preview(p, seconds=25.0)
    assert stub_media["mux"] == [[pipeline.VOICE_LEAD]]


def test_narration_after_the_window_is_left_out(tmp_path, stub_media):
    """A line starting at 40s has no business in a 25-second preview."""
    p = FakeProject(tmp_path)
    p.timeline = [{"index": 0, "recap_start": 40.0, "recap_end": 50.0}]
    pipeline.run_preview(p, seconds=25.0)
    assert stub_media["mux"] == [] or stub_media["mux"] == [[]]


def test_the_working_file_is_cleaned_up(tmp_path, stub_media):
    p = FakeProject(tmp_path)
    pipeline.run_preview(p, seconds=25.0)
    assert not (tmp_path / "_preview_base.mp4").exists()


def test_a_missing_cut_is_reported_not_crashed(tmp_path, stub_media):
    p = FakeProject(tmp_path)
    p.recap_path.unlink()
    with pytest.raises(pipeline.StepError) as err:
        pipeline.run_preview(p)
    assert "cut" in str(err.value)


def test_no_narration_is_reported_not_crashed(tmp_path, stub_media):
    p = FakeProject(tmp_path, narration=False)
    with pytest.raises(pipeline.StepError) as err:
        pipeline.run_preview(p)
    assert "narration" in str(err.value)
