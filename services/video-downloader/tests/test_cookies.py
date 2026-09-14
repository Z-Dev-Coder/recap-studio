"""
Age-restricted and members-only videos.

yt-dlp needs a signed-in browser's cookies for these. The API took a
cookies_browser option but the page never sent one, so such a video could not
be downloaded from Recap Studio at all -- and the error named command-line
flags nobody using the app can type.
"""

import pytest

from ytdl.recap import pipeline
from ytdl.recap.project import Project


class _Refusing:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        raise Exception("ERROR: [youtube] abc: Sign in to confirm your age. "
                        "Use --cookies-from-browser or --cookies")


@pytest.fixture
def project(tmp_path, monkeypatch):
    import yt_dlp
    seen = {}

    def make(opts):
        seen["opts"] = opts
        return _Refusing(opts)

    monkeypatch.setattr(yt_dlp, "YoutubeDL", make)
    p = Project(id="t", dir=tmp_path, url="https://www.youtube.com/watch?v=abc")
    p.dir.mkdir(parents=True, exist_ok=True)
    p.seen = seen
    return p


def test_an_age_gate_says_where_the_setting_is(project):
    with pytest.raises(pipeline.StepError) as err:
        pipeline.run_source(project)
    said = str(err.value)
    assert "Browser cookies" in said, "point at the control, not at a CLI flag"
    assert "Sign in to confirm" in said, "and keep yt-dlp's own words underneath"


def test_the_chosen_browser_reaches_yt_dlp(project):
    with pytest.raises(pipeline.StepError):
        pipeline.run_source(project, cookies_browser="chrome")
    assert project.seen["opts"]["cookiesfrombrowser"] == ("chrome",)


def test_a_browser_already_set_is_mentioned(project):
    """Then the likely fault is that browser being open or signed out."""
    with pytest.raises(pipeline.StepError) as err:
        pipeline.run_source(project, cookies_browser="edge")
    assert "already set to edge" in str(err.value)
