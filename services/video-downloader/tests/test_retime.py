"""
Retiming a clip so the picture lasts as long as the line spoken over it.

A beat against the start or end of the video has nowhere to grow into, so the
plan cannot always give it the footage its narration needs. Slowing the
picture to meet the line is an ordinary edit; letting it run out from under
the voice is a fault.
"""

import ytdl.recap.media as media


def _args(monkeypatch):
    """Run cut() without ffmpeg and return the command it would have run."""
    seen = {}
    monkeypatch.setattr(media, "_run", lambda a, **k: seen.setdefault("args", a))
    return seen


def _filters(args):
    joined = " ".join(args)
    return joined


def test_a_longer_line_slows_the_picture(tmp_path, monkeypatch):
    seen = _args(monkeypatch)
    media.cut(tmp_path / "s.mp4", tmp_path / "d.mp4", 100, 106, fit_to=9.0)
    text = _filters(seen["args"])
    assert "setpts=1.5000*PTS" in text          # 6s of footage over 9s
    assert "atempo=0.6667" in text              # its audio travels with it


def test_a_shorter_line_quickens_it(tmp_path, monkeypatch):
    seen = _args(monkeypatch)
    media.cut(tmp_path / "s.mp4", tmp_path / "d.mp4", 100, 106, fit_to=4.0)
    text = _filters(seen["args"])
    assert "setpts=0.6667*PTS" in text
    assert "atempo=1.5000" in text


def test_a_clip_already_the_right_length_is_left_alone(tmp_path, monkeypatch):
    seen = _args(monkeypatch)
    media.cut(tmp_path / "s.mp4", tmp_path / "d.mp4", 100, 106, fit_to=6.0)
    assert "setpts" not in _filters(seen["args"])


def test_no_request_means_no_retiming(tmp_path, monkeypatch):
    seen = _args(monkeypatch)
    media.cut(tmp_path / "s.mp4", tmp_path / "d.mp4", 100, 106)
    assert "setpts" not in _filters(seen["args"])


def test_an_absurd_stretch_is_clamped_rather_than_obeyed(tmp_path, monkeypatch):
    # five times slower is not an edit, it is a fault that looks deliberate
    seen = _args(monkeypatch)
    media.cut(tmp_path / "s.mp4", tmp_path / "d.mp4", 100, 106, fit_to=30.0)
    assert f"setpts={media.FASTEST:.4f}*PTS" in _filters(seen["args"])


def test_the_source_span_is_bounded_on_the_input_not_the_output(tmp_path, monkeypatch):
    """-t after the input would cap the clip and discard the added length."""
    seen = _args(monkeypatch)
    media.cut(tmp_path / "s.mp4", tmp_path / "d.mp4", 100, 106, fit_to=9.0)
    args = seen["args"]
    assert args.index("-t") < args.index("-i")
