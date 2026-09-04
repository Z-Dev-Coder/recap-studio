"""
Asking Ollama for a window the prompt fits in.

Ollama defaults to 2048 tokens of context and silently drops the rest. A recap
script is far longer, so the instructions fell off the front and the model --
left with a tail of Burmese and no task -- invented a subject. A documentary
about the bombing of Hiroshima came back tagged #darkenergy and #blackholes.
"""

from ytdl.recap import llm


def test_a_short_prompt_needs_no_more_than_the_default():
    assert llm._context_for("hello", 512) == 2048


def test_a_script_sized_prompt_asks_for_more():
    assert llm._context_for("x" * 6000, 1024) >= 8192


def test_the_answer_is_counted_too():
    """A window that fits the prompt exactly leaves nowhere to reply."""
    prompt = "x" * 2000
    assert llm._context_for(prompt, 4096) > llm._context_for(prompt, 128)


def test_it_never_asks_for_more_than_a_machine_can_give():
    assert llm._context_for("x" * 500000, 4096) == max(llm._CTX_STEPS)


def test_burmese_is_counted_as_the_denser_script_it_is():
    """At 1.5 characters a token, 6000 characters is 4000 tokens, not 1500."""
    assert llm._context_for("x" * 6000, 0) > 4096
