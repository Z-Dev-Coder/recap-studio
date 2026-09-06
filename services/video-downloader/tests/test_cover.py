"""
Putting the thumbnail inside the finished video.

A thumbnail sitting beside a video in a folder is one somebody has to remember
to upload. Carried in the file it survives being moved, copied and handed on,
and the folder shows the picture that was chosen rather than a frame a player
picked.
"""

import ytdl.recap.media as media


def test_a_cover_is_attached_not_added_as_a_second_video(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(media, "_run", lambda a, **k: seen.setdefault("args", a))
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x" * 2000)
    png = tmp_path / "thumbnail.png"
    png.write_bytes(b"png")

    # the run is stubbed, so the output never appears and the swap is skipped
    media.set_cover(video, png)
    args = seen["args"]
    assert "attached_pic" in args
    assert "-disposition:v:1" in args
    assert "copy" in args, "the video must be remuxed, not re-encoded"


def test_a_missing_picture_is_not_an_error(tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x")
    assert media.set_cover(video, tmp_path / "nothing.png") is False


def test_a_missing_video_is_not_an_error(tmp_path):
    png = tmp_path / "thumbnail.png"
    png.write_bytes(b"png")
    assert media.set_cover(tmp_path / "nothing.mp4", png) is False


def test_a_failed_attach_leaves_the_render_alone(tmp_path, monkeypatch):
    """Losing a finished render over a cover would be a poor trade."""
    def boom(*a, **k):
        raise media.MediaError("ffmpeg said no")
    monkeypatch.setattr(media, "_run", boom)

    video = tmp_path / "final.mp4"
    video.write_bytes(b"the finished render")
    png = tmp_path / "thumbnail.png"
    png.write_bytes(b"png")

    assert media.set_cover(video, png) is False
    assert video.read_bytes() == b"the finished render"
    assert not (tmp_path / "final_cover.mp4").exists()
