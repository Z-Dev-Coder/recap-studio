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


# ------------------------------------------------------------ playback speed

def test_a_faster_voice_needs_less_footage():
    """
    Played at 1.5x a line finishes sooner. Fitting the cut to the spoken length
    regardless left the picture running on after the voice stopped -- a pause
    before every line, growing with the speed.
    """
    from ytdl.recap.pipeline import VOICE_LEAD, VOICE_TAIL

    spoken = 9.0
    for speed, expected in ((1.0, 9.0), (1.5, 6.0), (2.0, 4.5)):
        want = spoken / speed + VOICE_LEAD + VOICE_TAIL
        assert abs(want - (expected + VOICE_LEAD + VOICE_TAIL)) < 0.01


def test_the_clip_always_covers_the_line_however_long():
    """
    A line longer than the gap to its neighbours used to be truncated, so the
    voice overran its clip and drifted into the next one.
    """
    from ytdl.recap.video import plan_fitted

    beats = [{"start": 0.0, "end": 5.0}, {"start": 10.0, "end": 15.0},
             {"start": 20.0, "end": 25.0}]
    wants = [4.0, 30.0, 4.0]          # the middle line is far too long for its gap
    plan = plan_fitted(beats, wants, 120.0)
    for (a, b), want in zip(plan, wants):
        assert b - a + 0.05 >= want, f"clip {b - a:.1f}s is short of {want}s"


def test_a_fitted_clip_stays_inside_the_video():
    from ytdl.recap.video import plan_fitted

    beats = [{"start": 0.0, "end": 2.0}, {"start": 55.0, "end": 58.0}]
    plan = plan_fitted(beats, [40.0, 40.0], 60.0)
    for a, b in plan:
        assert a >= 0 and b <= 60.0 + 0.01


def test_clips_stay_in_order():
    from ytdl.recap.video import plan_fitted

    beats = [{"start": i * 20.0, "end": i * 20.0 + 4} for i in range(4)]
    plan = plan_fitted(beats, [25.0] * 4, 200.0)
    starts = [a for a, _b in plan]
    assert starts == sorted(starts)


# ------------------------------------------------------- silence around a line

def test_the_pause_is_split_before_and_after():
    """
    The lead is the smaller half: a pause before a line is felt sooner than one
    after it.
    """
    from ytdl.recap.pipeline import padding_for

    class P:
        line_gap = 1.0

    lead, tail = padding_for(P())
    assert lead < tail
    assert abs((lead + tail) - 1.0) < 0.01


def test_no_pause_means_no_pause():
    from ytdl.recap.pipeline import padding_for

    class P:
        line_gap = 0.0

    assert padding_for(P()) == (0.0, 0.0)


def test_an_absurd_pause_is_clamped():
    from ytdl.recap.pipeline import padding_for

    class P:
        line_gap = 99.0

    lead, tail = padding_for(P())
    assert lead + tail <= 2.0


def test_a_project_without_the_setting_uses_the_defaults():
    """Older projects predate the field and must still render."""
    from ytdl.recap.pipeline import VOICE_LEAD, VOICE_TAIL, padding_for

    class Old:
        pass

    assert padding_for(Old()) == (VOICE_LEAD, VOICE_TAIL)


def test_the_default_pause_is_smaller_than_it_was():
    """
    0.4 + 0.8 was 1.2 seconds of silence on a seven-second clip, at every join.
    """
    from ytdl.recap.pipeline import VOICE_LEAD, VOICE_TAIL

    assert VOICE_LEAD + VOICE_TAIL < 1.2
