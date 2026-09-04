"""
Auditioning voices on a worker thread.

Moving the loop out of the endpoint left it reaching for a name the endpoint
had imported locally, which only fires when a voice is actually spoken -- so
every test passed and the first real press said "name 'localtts' is not
defined". These call the moved function for real, with the engine stubbed.
"""

import pytest

from ytdl.web import recap_api
from ytdl.recap.project import Project


@pytest.fixture
def project(tmp_path):
    p = Project(id="t", dir=tmp_path)
    (tmp_path / "voice").mkdir(parents=True, exist_ok=True)
    return p


def _silence() -> bytes:
    """A 0.5s WAV, so the plausibility check has something real to measure."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x01" * 12000)
    return buf.getvalue()


def test_the_audition_speaks_and_records_each_voice(project, monkeypatch):
    monkeypatch.setattr(recap_api.localtts_mod, "speak",
                        lambda *a, **k: _silence())
    monkeypatch.setattr(recap_api, "push", lambda p: None)

    out = project.voice_dir / "candidates"
    out.mkdir(parents=True, exist_ok=True)
    recap_api._audition(project, out, 0, 3, "sample", "my")

    assert len(project.voice_candidates) == 3
    assert [c["index"] for c in project.voice_candidates] == [0, 1, 2]
    assert all((project.dir / c["file"]).exists() for c in project.voice_candidates)
    assert project.steps["voice"].status == "done"


def test_a_failure_partway_keeps_what_was_managed(project, monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("the engine gave up")
        return _silence()

    monkeypatch.setattr(recap_api.localtts_mod, "speak", flaky)
    monkeypatch.setattr(recap_api, "push", lambda p: None)
    out = project.voice_dir / "candidates"
    out.mkdir(parents=True, exist_ok=True)
    recap_api._audition(project, out, 0, 4, "sample", "my")

    assert len(project.voice_candidates) == 2
    assert project.steps["voice"].status == "done"


def test_a_failure_on_the_first_voice_is_an_error(project, monkeypatch):
    monkeypatch.setattr(recap_api.localtts_mod, "speak",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no GPU")))
    monkeypatch.setattr(recap_api, "push", lambda p: None)
    out = project.voice_dir / "candidates"
    out.mkdir(parents=True, exist_ok=True)
    recap_api._audition(project, out, 0, 4, "sample", "my")

    assert project.voice_candidates == []
    assert project.steps["voice"].status == "error"
    assert "no GPU" in project.steps["voice"].error
