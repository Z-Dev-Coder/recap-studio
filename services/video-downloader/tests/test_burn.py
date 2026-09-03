"""
Laying many captions over a video.

Every caption is another ffmpeg input and another link in the filter chain,
and a command line is bounded -- on Windows at 32,767 characters, which a long
script reaches at about 128 captions. It arrived as an OSError from subprocess
rather than anything this module raises, so it escaped every handler and
surfaced as a bare Internal Server Error.
"""

import pytest

import ytdl.recap.media as media


@pytest.fixture
def runs(monkeypatch):
    """Record the length of each ffmpeg command instead of running it."""
    seen = []
    monkeypatch.setattr(media, "_run",
                        lambda a, **k: seen.append(sum(len(x) + 1 for x in a)))
    return seen


def _rows(tmp_path, n):
    png = tmp_path / "cap.png"
    png.write_bytes(b"x")
    return [{"path": png, "start": i * 2.0, "end": i * 2.0 + 1.8,
             "x": 0.5, "y": 0.86} for i in range(n)]


def test_a_few_captions_are_one_pass(tmp_path, runs):
    media.burn_caption_images(tmp_path / "s.mp4", _rows(tmp_path, 10),
                              tmp_path / "d.mp4")
    assert len(runs) == 1


def test_many_captions_stay_inside_the_command_line_limit(tmp_path, runs):
    media.burn_caption_images(tmp_path / "s.mp4", _rows(tmp_path, 300),
                              tmp_path / "d.mp4")
    assert len(runs) == 8                      # passes, not one giant call
    assert max(runs) < 32767                   # the limit that was being hit
    assert max(runs) < 12000                   # and comfortably under it


def test_the_pass_boundary_is_where_it_says(tmp_path, runs):
    media.burn_caption_images(tmp_path / "s.mp4",
                              _rows(tmp_path, media.PER_PASS), tmp_path / "d.mp4")
    assert len(runs) == 1
    runs.clear()
    media.burn_caption_images(tmp_path / "s.mp4",
                              _rows(tmp_path, media.PER_PASS + 1), tmp_path / "d.mp4")
    assert len(runs) == 2


def test_no_captions_is_a_readable_error_not_a_crash(tmp_path):
    with pytest.raises(media.MediaError, match="no caption images"):
        media.burn_caption_images(tmp_path / "s.mp4", [], tmp_path / "d.mp4")
