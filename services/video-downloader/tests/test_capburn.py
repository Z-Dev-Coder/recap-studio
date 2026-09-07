"""
Burmese captions and the one-click run.

ffmpeg places Myanmar glyphs in storage order whatever font it is given, so
the burn happens in the page, where Chromium shapes them. That left a hole:
the chain built the cut, rendered the final from the UNcaptioned copy, and
reported done -- a finished-looking video missing something the user chose.
"""

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
