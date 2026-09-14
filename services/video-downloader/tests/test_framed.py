"""
A framed look for every shape.

Blurred bars only appeared when the video's shape differed from the cut's -- a
wide video cut wide passed straight through with no treatment at all. The
framed look sets the picture a little inside a blurred copy of itself, and it
has to work for 16:9 as much as for 9:16.
"""

import ytdl.recap.media as media


def _args(monkeypatch, **kw):
    seen = {}
    monkeypatch.setattr(media, "_run", lambda a, **k: seen.setdefault("args", a))
    media.cut(media.Path("in.mp4"), media.Path("out.mp4"), 0.0, 3.0, **kw)
    return seen["args"]


def _filter(args):
    for flag in ("-filter_complex", "-vf"):
        if flag in args:
            return flag, args[args.index(flag) + 1]
    raise AssertionError("no filter")


def test_a_wide_cut_gets_a_frame(monkeypatch, tmp_path):
    flag, vf = _filter(_args(monkeypatch, vertical=False, framing="frame"))
    assert flag == "-filter_complex", "split needs a complex graph"
    assert "1920:1080" in vf and "boxblur" in vf
    assert f"{int(1920 * media.FRAME_SCALE) // 2 * 2}" in vf, "the picture sits inside the frame"


def test_a_tall_cut_gets_the_same_treatment(monkeypatch):
    _flag, vf = _filter(_args(monkeypatch, vertical=True, framing="frame"))
    assert "1080:1920" in vf and "boxblur" in vf


def test_the_inner_picture_has_even_dimensions(monkeypatch):
    """libx264 refuses odd sizes, and 0.86 of 1080 is not whole."""
    _flag, vf = _filter(_args(monkeypatch, vertical=True, framing="frame"))
    inner = vf.split("[fg]scale=")[1].split(":force")[0]
    w, h = (int(x) for x in inner.split(":"))
    assert w % 2 == 0 and h % 2 == 0


def test_the_plain_wide_cut_is_unchanged(monkeypatch):
    """Nobody who has not chosen it should get it."""
    flag, vf = _filter(_args(monkeypatch, vertical=False, framing="blur"))
    assert flag == "-vf" and "boxblur" not in vf
