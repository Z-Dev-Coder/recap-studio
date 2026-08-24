"""
The reading stage: what it produces, and what it does with bad input.

The point of these tests is that a video nobody has seen must still come out
the other side. So they check structure -- ordering, clamping, survival of
malformed fields -- rather than any particular content.
"""

from __future__ import annotations

from ytdl.recap import story as story_mod


def test_analyse_returns_structured_story(fake_gemini, story_reply, make_cues):
    client = fake_gemini([story_reply])
    result = story_mod.analyse(
        client, title="anything", duration=150.0,
        cues=make_cues((0, 2, "hello")),
    )
    assert result
    assert len(result.events) == 3
    assert result.characters[0]["name"] == "Renn"
    assert result.ending


def test_analyse_asks_for_notes_not_narration(fake_gemini, story_reply, make_cues):
    """The reading stage must never be asked to write the recap."""
    client = fake_gemini([story_reply])
    story_mod.analyse(client, title="t", duration=60.0, cues=make_cues((0, 2, "x")))
    prompt = client.prompts[0].lower()
    assert "you are not writing the recap" in prompt
    # and it must not be told to produce Burmese
    assert "burmese" not in prompt


def test_analyse_survives_a_failed_call(fake_gemini, gemini_error, make_cues):
    """A failure costs the analysis, never the step."""
    client = fake_gemini([gemini_error("no")])
    result = story_mod.analyse(client, title="t", duration=60.0,
                               cues=make_cues((0, 2, "x")))
    assert not result
    assert result.events == []


def test_analyse_survives_empty_and_malformed_replies(fake_gemini, make_cues):
    for reply in ({}, {"events": None}, {"events": "not a list"},
                  {"events": [{"event": ""}, "junk", 5]}):
        client = fake_gemini([reply])
        result = story_mod.analyse(client, title="t", duration=60.0,
                                   cues=make_cues((0, 2, "x")))
        assert result.events == []


def test_events_are_clamped_and_ordered():
    story = story_mod.from_dict({
        "events": [
            {"start": 90, "end": 120, "event": "third"},
            {"start": -50, "end": 10, "event": "first"},
            {"start": 40, "end": 5000, "event": "second"},
            {"start": "bad", "end": 3, "event": "dropped"},
        ]
    }, duration=100.0)
    assert [e["event"] for e in story.events] == ["first", "second", "third"]
    assert all(0 <= e["start"] <= 100 and e["end"] <= 100 for e in story.events)


def test_events_in_selects_by_overlap(story_reply):
    story = story_mod.from_dict(story_reply, duration=150.0)
    assert len(story.events_in(0, 30)) == 1
    assert len(story.events_in(85, 95)) == 2       # straddles a boundary
    assert story.events_in(500, 600) == []


def test_event_block_carries_cause_and_visual_marker(story_reply):
    story = story_mod.from_dict(story_reply, duration=150.0)
    block = story_mod.event_block(story.events)
    assert "because" in block            # cause travels with the event
    assert "which leads to" in block
    assert "not said out loud" in block  # visual-only events are marked


def test_cast_block_pins_names(story_reply):
    story = story_mod.from_dict(story_reply, duration=150.0)
    block = story.cast_block()
    assert "Renn" in block and "Odie" in block
    assert "do not rename" in block.lower()


def test_cast_block_empty_without_characters():
    assert story_mod.from_dict({"events": []}).cast_block() == ""


def test_transcript_is_sampled_not_truncated(make_cues):
    """A long transcript must keep reaching the end of the video."""
    rows = make_cues(*[(i * 2.0, i * 2.0 + 1.5, f"sentence {i} " + "x" * 80)
                       for i in range(2000)])
    block = story_mod._transcript_block(rows, duration=4000.0, budget=4000)
    assert "sampled evenly" in block
    # the last cue's timestamp region must still be represented
    stamps = [int(line[1:line.index("s]")]) for line in block.splitlines()
              if line.startswith("[")]
    assert max(stamps) > 3000


def test_no_transcript_is_stated_not_faked(make_cues):
    block = story_mod._transcript_block([], duration=60.0)
    assert "visually" in block
