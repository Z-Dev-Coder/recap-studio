"""
Saying how far along a long job is.

Burning captions into a two-minute cut takes two minutes. Two minutes of a
button that looks stuck is indistinguishable from a button that is, which is
how "is it working or not?" happens.
"""

import ytdl.recap.media as media


def test_ffmpeg_progress_lines_are_read_as_a_fraction():
    text = "frame=120\nout_time_us=30000000\nspeed=1.2x\n"
    assert media._how_far(text, 60.0) == 0.5


def test_the_last_reading_in_a_chunk_wins():
    text = "out_time_us=10000000\nout_time_us=45000000\n"
    assert media._how_far(text, 90.0) == 0.5


def test_out_time_ms_is_microseconds_too():
    """A long-standing ffmpeg wart: out_time_ms is in microseconds."""
    assert media._how_far("out_time_ms=30000000\n", 60.0) == 0.5


def test_a_chunk_with_no_reading_says_so():
    assert media._how_far("frame=10\nspeed=2x\n", 60.0) is None


def test_progress_never_leaves_nought_to_one():
    assert media._how_far("out_time_us=999000000\n", 10.0) == 1.0
    assert media._how_far("out_time_us=-5000000\n", 10.0) == 0.0


def test_asking_for_progress_asks_ffmpeg_for_it(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(media, "_run",
                        lambda a, **k: seen.update(args=a, kw=k))
    # the length to divide by comes from probing the source, which would
    # otherwise go through the stubbed runner
    monkeypatch.setattr(media, "probe",
                        lambda p: media.Probe(120.0, 1080, 1920, 30.0, True))
    png = tmp_path / "c.png"
    png.write_bytes(b"x")
    media.burn_caption_images(tmp_path / "s.mp4",
                              [{"path": png, "start": 0, "end": 2}],
                              tmp_path / "d.mp4", on_progress=lambda f: None)
    assert seen["kw"]["on_progress"] is not None
    assert seen["kw"]["seconds"] == 120.0


def test_not_asking_for_it_leaves_the_command_alone(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(media, "_run", lambda a, **k: seen.update(kw=k))
    png = tmp_path / "c.png"
    png.write_bytes(b"x")
    media.burn_caption_images(tmp_path / "s.mp4",
                              [{"path": png, "start": 0, "end": 2}],
                              tmp_path / "d.mp4")
    assert seen["kw"].get("seconds", 0) == 0
