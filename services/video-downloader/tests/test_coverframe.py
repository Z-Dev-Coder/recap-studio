"""
The thumbnail as the first frame of the finished video.

Cover art inside an mp4 is ignored by every social platform and by Windows
Explorer. What their cover pickers offer is frames FROM the video, so the only
way the chosen picture can be picked on a phone -- or fall into the profile
grid -- is for it to be one of those frames.
"""

import ytdl.recap.media as media


def _stub(monkeypatch, seen):
    monkeypatch.setattr(media, "_run", lambda a, **k: seen.setdefault("args", a))
    monkeypatch.setattr(
        media, "probe",
        lambda p: media.Probe(duration=60.0, width=1080, height=1920,
                              fps=30.0, has_audio=True),
    )


def test_the_still_is_fitted_to_the_videos_own_frame(tmp_path, monkeypatch):
    seen = {}
    _stub(monkeypatch, seen)
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x" * 2000)
    png = tmp_path / "thumbnail.png"
    png.write_bytes(b"png")

    media.prepend_still(video, png, seconds=0.6)
    chain = " ".join(seen["args"])
    assert "1080:1920" in chain, "a thumbnail of another shape must not stretch"
    assert "force_original_aspect_ratio=decrease" in chain
    assert "concat=n=2:v=1:a=0" in chain, "the still goes in front of the video"
    assert "anullsrc" in chain, "silence under the still, so the voice is not dragged forward"


def test_the_length_is_clamped_to_something_watchable(tmp_path, monkeypatch):
    seen = {}
    _stub(monkeypatch, seen)
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x" * 2000)
    png = tmp_path / "thumbnail.png"
    png.write_bytes(b"png")

    media.prepend_still(video, png, seconds=99)
    assert "-t" in seen["args"]
    assert seen["args"][seen["args"].index("-t") + 1] == "5.000"


def test_a_missing_picture_is_not_an_error(tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x" * 2000)
    assert media.prepend_still(video, tmp_path / "gone.png") is False
    assert video.exists(), "the video must survive a missing thumbnail"
