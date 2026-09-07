"""
Burmese captions and the one-click run.

ffmpeg places Myanmar glyphs in storage order whatever font it is given, so
the burn happens in the page, where Chromium shapes them. That left a hole:
the chain built the cut, rendered the final from the UNcaptioned copy, and
reported done -- a finished-looking video missing something the user chose.
"""

import pytest

from ytdl.recap.project import Project


def _ready(tmp_path) -> Project:
    p = Project(id="t", dir=tmp_path)
    p.dir.mkdir(parents=True, exist_ok=True)
    p.burn_captions = True
    p.voice_lang = "my"
    p.recap_path.write_bytes(b"cut")
    return p


def test_the_page_is_told_when_the_burn_is_owed(tmp_path):
    assert _ready(tmp_path).snapshot()["needs_caption_burn"] is True


def test_nothing_is_owed_once_the_captioned_cut_exists(tmp_path):
    p = _ready(tmp_path)
    p.captioned_path.write_bytes(b"captioned")
    assert p.snapshot()["needs_caption_burn"] is False


def test_nothing_is_owed_before_there_is_a_cut(tmp_path):
    p = _ready(tmp_path)
    p.recap_path.unlink()
    assert p.snapshot()["needs_caption_burn"] is False


def test_english_needs_no_page(tmp_path):
    """ffmpeg can shape Latin text, so that burn happens with the cut."""
    p = _ready(tmp_path)
    p.caption_lang = "en"
    assert p.snapshot()["needs_caption_burn"] is False


def test_captions_switched_off_owe_nothing(tmp_path):
    p = _ready(tmp_path)
    p.burn_captions = False
    assert p.snapshot()["needs_caption_burn"] is False


def test_the_derived_answer_is_not_stored_back(tmp_path):
    import json
    p = _ready(tmp_path)
    p.save()
    data = json.loads((p.dir / "project.json").read_text(encoding="utf-8"))
    assert "needs_caption_burn" not in data


def test_a_chain_parks_before_the_final_rather_than_rendering_without_captions(
        tmp_path, monkeypatch):
    """
    The burn happens in the browser, so the service cannot call it. Rendering
    anyway produced a finished-looking video missing what was asked for.
    """
    from ytdl.recap.project import STEPS
    from ytdl.web import recap_api

    p = _ready(tmp_path)
    monkeypatch.setattr(recap_api.store, "get", lambda pid: p)
    monkeypatch.setattr(recap_api, "push", lambda *a, **k: None)
    ran = []
    monkeypatch.setattr(recap_api, "run_step",
                        lambda pid, step, opts, release=True: ran.append(step))
    # run the chain body on this thread, so the test does not race it
    monkeypatch.setattr(recap_api.threading, "Thread",
                        lambda target, daemon=None: type(
                            "T", (), {"start": lambda _s: target()})())

    recap_api.run_chain("t", ["video", "thumbnail", "final"], {})

    assert ran == ["video", "thumbnail"], "the final must wait for the captions"
    assert p.chain_pending == ["final"], "and what is left must be remembered"
    assert "waiting for the Burmese captions" in p.steps["final"].message


def test_a_chain_runs_straight_through_when_no_burn_is_owed(tmp_path, monkeypatch):
    from ytdl.web import recap_api

    p = _ready(tmp_path)
    p.captioned_path.write_bytes(b"captioned")
    monkeypatch.setattr(recap_api.store, "get", lambda pid: p)
    monkeypatch.setattr(recap_api, "push", lambda *a, **k: None)
    ran = []
    monkeypatch.setattr(recap_api, "run_step",
                        lambda pid, step, opts, release=True: ran.append(step))
    monkeypatch.setattr(recap_api.threading, "Thread",
                        lambda target, daemon=None: type(
                            "T", (), {"start": lambda _s: target()})())

    recap_api.run_chain("t", ["video", "thumbnail", "final"], {})
    assert ran == ["video", "thumbnail", "final"]
    assert p.chain_pending == []


def test_a_second_burn_cannot_delete_a_running_ones_pictures(tmp_path, monkeypatch):
    """
    The claim used to be taken after the captions folder had been emptied, so
    a burn arriving mid-encode deleted the files ffmpeg was reading and it
    stopped on a picture that had existed a moment earlier.
    """
    import base64
    from ytdl.web import recap_api

    p = _ready(tmp_path)
    monkeypatch.setattr(recap_api.store, "get", lambda pid: p)
    monkeypatch.setattr(recap_api, "push", lambda *a, **k: None)
    monkeypatch.setattr(recap_api, "_claim", lambda pid, what: False)   # busy

    png = base64.b64encode(b"not really a png").decode()
    shots = p.dir / "captions"
    shots.mkdir(parents=True, exist_ok=True)
    (shots / "cap_0000.png").write_bytes(b"the running burn's input")

    req = recap_api.CaptionImagesRequest(
        images=[recap_api.CaptionImage(index=0, start=0.0, end=1.0, png_base64=png)])
    with pytest.raises(recap_api.HTTPException) as exc:
        recap_api.burn_caption_images("t", req)

    assert exc.value.status_code == 409
    assert (shots / "cap_0000.png").exists(), "the refused call must touch nothing"
