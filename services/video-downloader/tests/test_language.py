"""
Reading a script's language off the script.

Being made to declare it -- for every paste, on every project -- is a question
whose answer is sitting in the text. Burmese and English do not share an
alphabet, so this is looking rather than guessing.
"""

from ytdl.recap.script import language_of, parse_manual


def test_burmese_is_recognised():
    assert language_of("ဒါဟာ စာကြောင်းတစ်ကြောင်းပါ။") == "my"


def test_english_is_recognised():
    assert language_of("This is a line of narration.") == "en"


def test_one_myanmar_letter_settles_it():
    """English never contains one; Burmese often contains Latin punctuation."""
    assert language_of("Mickey ၏ နာရီစင်") == "my"


def test_latin_punctuation_alone_is_not_burmese():
    assert language_of("00:01:20,000 --> 00:01:31,000") == "en"


def test_nothing_at_all_is_not_burmese():
    assert language_of("") == "en"
    assert language_of(None) == "en"


def test_a_pasted_script_lands_in_the_language_it_is_written_in():
    beats = parse_manual("ဒါဟာ ပထမပါ။\n\nဒါက ဒုတိယပါ။", 0.0)
    assert all(b.my and not b.en for b in beats)

    beats = parse_manual("This is first.\n\nThis is second.", 0.0)
    assert all(b.en and not b.my for b in beats)


def test_saying_the_language_still_overrides_the_guess():
    """Someone who names it means it, even for a script that looks otherwise."""
    beats = parse_manual("This is english text.", 0.0, "my")
    assert beats[0].my and not beats[0].en
