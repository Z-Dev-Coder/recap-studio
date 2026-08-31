"""
Transcribing, and knowing when there is nothing to transcribe.

A 1937 cartoon stopped the pipeline with "no speech could be found in this
video" while its dialogue was plainly audible at -14.8 dB. Two faults: the
voice-activity filter judged the whole soundtrack non-speech, and a video
without speech was treated as an error rather than as a kind of video.
"""

from __future__ import annotations

from ytdl.recap.transcript import Cue, _is_hallucinated


def cues(*texts):
    return [Cue(start=i * 30.0, end=i * 30.0 + 2, text=t) for i, t in enumerate(texts)]


def test_the_same_short_word_repeated_is_not_a_transcript():
    """
    Without voice-activity filtering Whisper narrates music as one short phrase
    over and over. That came back as "You" every thirty seconds -- passing it
    on would have had the whole recap built from it.
    """
    assert _is_hallucinated(cues("You", "You", "You", "You", "You", "You"))


def test_a_handful_of_filler_lines_is_caught():
    assert _is_hallucinated(cues("Thanks.", "Thanks.", "Bye.", "Thanks.", "Bye."))


def test_a_real_transcript_is_kept():
    assert not _is_hallucinated(cues(
        "The builder assembles the machine in his workshop.",
        "He switches it on without testing it first.",
        "The machine tears across the field outside.",
        "They shut it down before it reaches the house.",
    ))


def test_a_short_but_genuine_transcript_survives():
    """Too few lines to judge -- keeping them beats discarding real speech."""
    assert not _is_hallucinated(cues("Hello there.", "Come inside."))


def test_nothing_at_all_is_not_mistaken_for_speech():
    assert not _is_hallucinated([])
    assert _is_hallucinated(cues("", "", "", "", ""))


def test_burmese_speech_is_not_judged_as_filler():
    """
    The threshold counts characters, and Burmese says more per character than
    English -- a real Burmese transcript must not read as too short.
    """
    assert not _is_hallucinated(cues(
        "ဒီနေ့မှာ သူတို့ဟာ စက်ကို စမ်းသပ်ကြပါတယ်။",
        "အဲဒီနောက် စက်က ထိန်းမရအောင် ဖြစ်သွားပါတယ်။",
        "နောက်ဆုံးမှာတော့ သူတို့ စက်ကို ပိတ်လိုက်ပါတယ်။",
        "ဒါပေမယ့် လယ်ကွင်းက ပျက်စီးသွားပါပြီ။",
    ))
