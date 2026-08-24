"""
Chaptering, beat repair, and the staged pipeline as a whole.

The video shapes here -- short, long, talky, silent, many characters -- stand in
for the range the tool has to cope with. None of them is the video that
prompted the work.
"""

from __future__ import annotations

import pytest

from ytdl.recap import script as script_mod


# ------------------------------------------------------------- chaptering

@pytest.mark.parametrize("duration,count", [
    (12.0, 8),        # a very short clip
    (180.0, 11),      # a few minutes
    (7200.0, 24),     # two hours
])
def test_windows_cover_the_whole_video(duration, count):
    windows = script_mod.plan_windows(duration, count)
    assert len(windows) == count
    assert windows[0][0] == 0
    assert windows[-1][1] == pytest.approx(duration)
    for (_, a), (b, _) in zip(windows, windows[1:]):
        assert a == b                      # no gaps, no overlaps


def test_windows_survive_a_zero_length_video():
    assert script_mod.plan_windows(0.0, 5) == [(0.0, 0.0)]


def test_boundaries_snap_to_a_pause(make_cues):
    """A boundary should prefer a silence to the middle of a sentence."""
    rows = make_cues(
        (0, 9.5, "first speech"),
        (12.0, 20.0, "second speech"),     # a 2.5s gap around 10.75s
    )
    plain = script_mod.plan_windows(20.0, 2)
    snapped = script_mod.plan_windows(20.0, 2, rows)
    assert plain[0][1] == pytest.approx(10.0)
    assert snapped[0][1] == pytest.approx(10.75)


def test_boundaries_prefer_an_event_seam(make_cues):
    events = [{"start": 0.0, "end": 11.0}, {"start": 11.0, "end": 20.0}]
    snapped = script_mod.plan_windows(20.0, 2, None, events)
    assert snapped[0][1] == pytest.approx(11.0)


def test_boundaries_never_slide_far_enough_to_collapse_a_chapter(make_cues):
    """Coverage outranks a tidy cut: a chapter may not be squeezed away."""
    rows = make_cues((0, 1.0, "a"), (99.0, 100.0, "b"))   # a huge gap
    windows = script_mod.plan_windows(100.0, 5, rows)
    assert len(windows) == 5
    for start, end in windows:
        assert end - start > 100.0 / 5 * 0.4
    assert windows == sorted(windows)


def test_windows_with_no_cues_are_simply_equal():
    assert script_mod.plan_windows(100.0, 4, []) == [
        (0.0, 25.0), (25.0, 50.0), (50.0, 75.0), (75.0, 100.0)]


# ------------------------------------------------------------- beat repair

def test_beats_are_forced_back_inside_their_chapter():
    windows = [(0.0, 30.0), (30.0, 60.0)]
    raw = [
        {"index": 0, "start": 500.0, "end": 900.0, "en": "way outside"},
        {"index": 1, "start": -20.0, "end": 5.0, "en": "before its window"},
    ]
    out = script_mod.repair_beats(raw, windows, 6.0, 60.0)
    assert len(out) == 2
    for beat, (ws, we) in zip(out, windows):
        assert ws <= beat.start < beat.end <= we


def test_missing_beats_are_filled_so_coverage_holds():
    windows = [(0.0, 30.0), (30.0, 60.0), (60.0, 90.0)]
    out = script_mod.repair_beats([{"index": 1, "start": 35, "end": 41}],
                                  windows, 6.0, 90.0)
    assert len(out) == 3               # one per chapter, still


def test_beat_repair_survives_junk():
    windows = [(0.0, 30.0)]
    raw = ["not a dict", {"index": "x"}, {"index": 0, "start": "bad", "score": "bad"}]
    out = script_mod.repair_beats(raw, windows, 6.0, 30.0)
    assert len(out) == 1
    assert 1.0 <= out[0].score <= 10.0


def test_beats_come_back_in_playing_order():
    windows = [(0.0, 30.0), (30.0, 60.0), (60.0, 90.0)]
    raw = [{"index": i, "start": w[0] + 1, "end": w[0] + 7}
           for i, w in reversed(list(enumerate(windows)))]
    out = script_mod.repair_beats(raw, windows, 6.0, 90.0)
    assert [b.start for b in out] == sorted(b.start for b in out)


# --------------------------------------------------------------- planning

@pytest.mark.parametrize("duration", [30.0, 300.0, 3600.0])
def test_beat_plan_scales_with_the_source(duration):
    count, clip = script_mod.beat_plan(duration, "long")
    assert 6 <= count <= 24
    assert clip >= 4.0


def test_reels_stay_within_their_budget():
    count, clip = script_mod.beat_plan(600.0, "reels", 60.0)
    assert count * clip == pytest.approx(60.0)


# ------------------------------------------------------- the whole pipeline

def _package(n):
    return {
        "title_en": "t", "title_my": "မ", "description_en": "d",
        "description_my": "မ", "hashtags": ["one", "#two"],
        "video_type": "short", "pacing": "fast",
        "hook_en": "h", "hook_my": "မ",
        "thumbnail_text_en": "x", "thumbnail_text_my": "မ",
        "beats": [{"index": i, "start": i * 10.0, "end": i * 10.0 + 6.0,
                   "en": f"english {i}", "score": 7, "why": "hook"}
                  for i in range(n)],
    }


def test_generate_runs_the_stages_in_order(monkeypatch, fake_gemini, story_reply):
    """read -> pick -> write Burmese -> review."""
    n, _ = script_mod.beat_plan(60.0, "long", 60.0)
    client = fake_gemini([
        story_reply,
        _package(n),
        {"lines": [{"index": i, "my": "မြန်မာ" * 12} for i in range(n)]},
        {"lines": [{"index": i, "ok": True} for i in range(n)]},
    ])
    monkeypatch.setattr(script_mod, "Gemini", lambda *a, **k: client)

    out = script_mod.generate(
        api_key="k", model="m", title="t", uploader="u",
        duration=60.0, cues=[], mode="long", target_seconds=60.0,
    )
    assert client.calls == 4
    assert all(b["my"] for b in out["beats"])
    assert out["burmese_written"] == n
    assert out["story"]["events"]


def test_beats_no_longer_ask_the_model_for_burmese(monkeypatch, fake_gemini, story_reply):
    """The moment-picking call must be English only."""
    client = fake_gemini([story_reply, _package(4), {"lines": []}])
    monkeypatch.setattr(script_mod, "Gemini", lambda *a, **k: client)
    script_mod.generate(api_key="k", model="m", title="t", uploader="u",
                        duration=40.0, cues=[], mode="long", target_seconds=40.0)
    beat_schema = client.schemas[1]["properties"]["beats"]["items"]
    assert "my" not in beat_schema["properties"]
    assert "my" not in beat_schema["required"]


def test_frames_are_sent_once_not_twice(monkeypatch, fake_gemini, story_reply):
    """Images are the expensive part; the reading stage already saw them."""
    client = fake_gemini([story_reply, _package(3), {"lines": []}])
    monkeypatch.setattr(script_mod, "Gemini", lambda *a, **k: client)
    frames = [("image/jpeg", b"x")] * 5
    script_mod.generate(api_key="k", model="m", title="t", uploader="u",
                        duration=30.0, cues=[], mode="long",
                        target_seconds=30.0, frames=frames)
    assert client.images[0] == 5      # the reading stage
    assert client.images[1] == 0      # the picking stage


def test_frames_still_reach_the_model_when_analysis_fails(
        monkeypatch, fake_gemini, gemini_error, story_reply):
    client = fake_gemini([gemini_error("no"), _package(3), {"lines": []}])
    monkeypatch.setattr(script_mod, "Gemini", lambda *a, **k: client)
    frames = [("image/jpeg", b"x")] * 4
    script_mod.generate(api_key="k", model="m", title="t", uploader="u",
                        duration=30.0, cues=[], mode="long",
                        target_seconds=30.0, frames=frames)
    assert client.images[1] == 4      # fell back to sending them


def test_pipeline_still_produces_a_package_when_analysis_fails(
        monkeypatch, fake_gemini, gemini_error):
    n, _ = script_mod.beat_plan(50.0, "long", 50.0)
    client = fake_gemini([
        gemini_error("analysis down"),
        _package(n),
        {"lines": [{"index": i, "my": "မြန်မာ" * 12} for i in range(n)]},
        {"lines": [{"index": i, "ok": True} for i in range(n)]},
    ])
    monkeypatch.setattr(script_mod, "Gemini", lambda *a, **k: client)
    out = script_mod.generate(api_key="k", model="m", title="t", uploader="u",
                              duration=50.0, cues=[], mode="long", target_seconds=50.0)
    assert len(out["beats"]) == n
    assert out["story"] == {}
    assert all(b["my"] for b in out["beats"])


def test_no_review_call_when_burmese_never_arrived(monkeypatch, fake_gemini, story_reply):
    """Nothing to review means no call to make."""
    client = fake_gemini([story_reply, _package(3), {"lines": []}])
    monkeypatch.setattr(script_mod, "Gemini", lambda *a, **k: client)
    script_mod.generate(api_key="k", model="m", title="t", uploader="u",
                        duration=30.0, cues=[], mode="long", target_seconds=30.0)
    assert client.calls == 3


def test_output_contract_is_unchanged(monkeypatch, fake_gemini, story_reply):
    """Existing consumers must keep finding what they read before."""
    client = fake_gemini([story_reply, _package(3),
                          {"lines": [{"index": i, "my": "မ" * 80} for i in range(3)]},
                          {"lines": []}])
    monkeypatch.setattr(script_mod, "Gemini", lambda *a, **k: client)
    out = script_mod.generate(api_key="k", model="m", title="t", uploader="u",
                              duration=30.0, cues=[], mode="long", target_seconds=30.0)
    for key in ("beats", "title", "description", "hashtags", "thumbnail_text",
                "coverage", "mode", "video_type", "pacing", "hook",
                "burmese_expanded"):
        assert key in out
    assert out["hashtags"] == ["one", "two"]       # '#' still stripped
    beat = out["beats"][0]
    for key in ("index", "start", "end", "en", "my", "score", "why"):
        assert key in beat


# ------------------------------------------------------------ cancellation

def test_stop_is_honoured_between_stages(monkeypatch, fake_gemini, story_reply):
    """
    A model call cannot be interrupted, so Stop can only land between stages.

    Without a check at those boundaries the whole step ran to completion
    however early Stop was pressed, which is what "the stop button does
    nothing" looked like.
    """
    import threading
    from ytdl.recap.media import Cancelled

    stop = threading.Event()

    class StopAfterFirst(fake_gemini):
        def generate_json(self, *a, **k):
            out = super().generate_json(*a, **k)
            stop.set()                     # pressed while stage 1 was in flight
            return out

    client = StopAfterFirst([story_reply, _package(6), {"lines": []}, {"lines": []}])
    monkeypatch.setattr(script_mod, "Gemini", lambda *a, **k: client)

    with pytest.raises(Cancelled):
        script_mod.generate(api_key="k", model="m", title="t", uploader="u",
                            duration=60.0, cues=[], mode="long",
                            target_seconds=60.0, cancel=stop)
    assert client.calls == 1        # stopped before the second stage began


def test_no_cancel_means_no_interruption(monkeypatch, fake_gemini, story_reply):
    n, _ = script_mod.beat_plan(60.0, "long", 60.0)
    client = fake_gemini([story_reply, _package(n),
                          {"lines": [{"index": i, "my": "မ" * 80} for i in range(n)]},
                          {"lines": []}])
    monkeypatch.setattr(script_mod, "Gemini", lambda *a, **k: client)
    out = script_mod.generate(api_key="k", model="m", title="t", uploader="u",
                              duration=60.0, cues=[], mode="long", target_seconds=60.0)
    assert len(out["beats"]) == n


# ------------------------------------------------------- a script of your own

def test_plain_lines_become_beats_across_the_video():
    beats = script_mod.parse_manual("first line\n\nsecond line\n\nthird line", 90.0, "my")
    assert [b.my for b in beats] == ["first line", "second line", "third line"]
    assert all(b.en == "" for b in beats)          # not machine-translated
    assert beats[0].start == 0
    assert beats[-1].end <= 90.0
    assert [b.start for b in beats] == sorted(b.start for b in beats)


def test_single_newlines_work_when_there_are_no_blank_lines():
    beats = script_mod.parse_manual("one\ntwo\nthree", 60.0, "en")
    assert [b.en for b in beats] == ["one", "two", "three"]


def test_an_srt_keeps_its_own_timings():
    srt = (
        "1\n00:00:05,000 --> 00:00:09,500\nfirst\n\n"
        "2\n00:01:02,250 --> 00:01:06,000\nsecond\n"
    )
    beats = script_mod.parse_manual(srt, 300.0, "my")
    assert len(beats) == 2
    assert beats[0].start == 5.0 and beats[0].end == 9.5
    assert beats[1].start == 62.25
    assert beats[1].my == "second"


def test_burmese_survives_unchanged():
    text = "ပထမကြောင်း\n\nဒုတိယကြောင်း"
    beats = script_mod.parse_manual(text, 60.0, "my")
    assert beats[0].my == "ပထမကြောင်း"
    assert beats[1].my == "ဒုတိယကြောင်း"


def test_an_empty_script_yields_nothing():
    assert script_mod.parse_manual("", 60.0) == []
    assert script_mod.parse_manual("   \n\n  \n", 60.0) == []


def test_a_manual_script_works_without_a_known_duration():
    beats = script_mod.parse_manual("a\n\nb", 0.0, "my")
    assert len(beats) == 2
    assert beats[0].end > beats[0].start


def test_long_form_defaults_to_the_whole_video():
    """
    With no length chosen, the script must be planned for the whole source.

    It used to plan for 22% of it, which was survivable while the cut had an
    independent length -- but the cut is fitted to the narration now, so that
    default silently became the length of the finished video.
    """
    count, clip = script_mod.beat_plan(420.0, "long")
    assert count * clip == pytest.approx(420.0, rel=0.02)


def test_an_explicit_length_still_wins():
    count, clip = script_mod.beat_plan(420.0, "long", 120.0)
    assert count * clip == pytest.approx(120.0, rel=0.02)


def test_reels_are_unaffected_by_the_source_length():
    count, clip = script_mod.beat_plan(4000.0, "reels")
    assert count * clip == pytest.approx(60.0)


def test_added_lines_join_the_script_rather_than_replacing_it():
    rows = [{"en": "one", "my": "တစ်"}, {"en": "two", "my": "နှစ်"}]
    fresh = [{"en": "", "my": "သုံး"}]
    beats = script_mod.respread(rows + fresh, 120.0)
    assert [b.my for b in beats] == ["တစ်", "နှစ်", "သုံး"]
    assert [b.en for b in beats] == ["one", "two", ""]


def test_respread_covers_the_whole_video_again():
    rows = [{"my": f"line {i}"} for i in range(5)]
    beats = script_mod.respread(rows, 100.0)
    assert beats[0].start == 0
    assert beats[-1].end <= 100.0
    assert [b.index for b in beats] == [0, 1, 2, 3, 4]
    assert [b.start for b in beats] == sorted(b.start for b in beats)


def test_respread_drops_blank_rows():
    beats = script_mod.respread([{"my": "a"}, {"my": "   "}, {"en": "b"}], 60.0)
    assert len(beats) == 2
