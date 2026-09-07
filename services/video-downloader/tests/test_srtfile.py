"""
Getting the script back out as subtitles.

The two buttons on the script panel build an SRT from the beats -- the script
as pasted, not the cut. Asking for a language the script is not in returned an
empty file with a 200, so the download saved nothing and said nothing.
"""

import pytest

from ytdl.web import recap_api
from ytdl.recap.project import Project


@pytest.fixture
def project(tmp_path, monkeypatch):
    p = Project(id="t", dir=tmp_path)
    p.beats = [
        {"index": 0, "start": 2.0, "end": 8.0, "my": "မြန်မာစာ", "en": ""},
        {"index": 1, "start": 12.0, "end": 20.0, "my": "နောက်တစ်ကြောင်း", "en": ""},
    ]
    monkeypatch.setattr(recap_api.store, "get", lambda pid: p)
    return p


def test_the_script_becomes_subtitles_on_the_original_timing(project):
    body = recap_api.srt("t", lang="my", timing="original").body.decode("utf-8")
    assert "00:00:02,000 --> 00:00:08,000" in body
    assert "မြန်မာစာ" in body


def test_asking_for_a_language_the_script_lacks_is_refused(project):
    """It used to answer 200 with an empty body, which saves an empty file."""
    with pytest.raises(recap_api.HTTPException) as exc:
        recap_api.srt("t", lang="en", timing="original")
    assert exc.value.status_code == 400
    assert "Burmese" in exc.value.detail, "and it says which language it does have"


def test_recap_timing_needs_the_cut(project):
    with pytest.raises(recap_api.HTTPException) as exc:
        recap_api.srt("t", lang="my", timing="recap")
    assert "Rebuild the cut" in exc.value.detail
