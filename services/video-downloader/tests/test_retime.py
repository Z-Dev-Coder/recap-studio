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


# ------------------------------------------------------- more action per line

def test_a_livelier_pace_takes_more_footage_and_compresses_it(monkeypatch):
    """At pace 1.5 a 6s line shows 9s of action, quickened to fit."""
    from ytdl.recap import video as vid

    asked = {}
    def fake_plan(beats, wants, *a, **k):
        asked["wants"] = list(wants)
        return [(10.0, 19.0)]
    monkeypatch.setattr(vid, "plan_fitted", fake_plan)

    cuts = []
    def fake_cut(src, dest, start, end, **k):
        cuts.append(k.get("fit_to"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")
        return dest
    monkeypatch.setattr(vid, "cut", fake_cut)
    monkeypatch.setattr(vid, "probe", lambda p: type("P", (), {"duration": 6.0, "width": 1920, "height": 1080})())
    monkeypatch.setattr(vid, "concat", lambda parts, dest, cancel=None: dest)

    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        vid.build(pathlib.Path(tmp) / "src.mp4",
                  [{"index": 0, "start": 10.0, "end": 16.0, "my": "x"}],
                  pathlib.Path(tmp) / "out.mp4",
                  work_dir=pathlib.Path(tmp) / "w",
                  fit_seconds=[6.0], pace=1.5)

    assert asked["wants"] == [9.0]      # 1.5x the footage was sought
    assert cuts == [6.0]                # and compressed back into the line


def test_the_plain_cut_is_still_the_default(monkeypatch):
    from ytdl.recap import video as vid
    asked = {}
    def fake_plan(beats, wants, *a, **k):
        asked["wants"] = list(wants)
        return [(10.0, 16.0)]
    monkeypatch.setattr(vid, "plan_fitted", fake_plan)

    def fake_cut(src, dest, start, end, **k):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")
        return dest
    monkeypatch.setattr(vid, "cut", fake_cut)
    monkeypatch.setattr(vid, "probe", lambda p: type("P", (), {"duration": 6.0, "width": 1920, "height": 1080})())
    monkeypatch.setattr(vid, "concat", lambda parts, dest, cancel=None: dest)

    import pathlib, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        vid.build(pathlib.Path(tmp) / "src.mp4",
                  [{"index": 0, "start": 10.0, "end": 16.0, "my": "x"}],
                  pathlib.Path(tmp) / "out.mp4",
                  work_dir=pathlib.Path(tmp) / "w",
                  fit_seconds=[6.0])
    assert asked["wants"] == [6.0]
