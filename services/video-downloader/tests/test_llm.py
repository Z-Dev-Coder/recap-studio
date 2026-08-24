"""
The provider layer: one interface over Gemini, a local Ollama and Groq.

None of these tests reach the network. What they pin down is the parsing --
only Gemini is asked for JSON by schema, so the others answer with fences,
preamble and trailing commentary often enough that pulling the object out is
part of the job rather than an error case.
"""

from __future__ import annotations

import pytest

from ytdl.recap import llm


def test_a_bare_model_name_still_means_gemini():
    """Every setting in this app meant Gemini before there was a choice."""
    assert llm.split_spec("gemini-3.6-flash") == ("gemini", "gemini-3.6-flash")
    assert llm.split_spec("") == ("gemini", "")


def test_provider_prefixes_are_understood():
    assert llm.split_spec("ollama:qwen2.5:7b") == ("ollama", "qwen2.5:7b")
    assert llm.split_spec("groq:llama-3.3-70b-versatile") == ("groq", "llama-3.3-70b-versatile")
    assert llm.split_spec("GEMINI:x") == ("gemini", "x")


def test_an_unknown_prefix_is_treated_as_a_model_name():
    """A mistyped provider should cost quality, not the whole run."""
    provider, model = llm.split_spec("nonsense:thing")
    assert provider == "gemini"


@pytest.mark.parametrize("body", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    'Here is the JSON you asked for:\n{"a": 1}',
    '{"a": 1}\n\nLet me know if you want changes.',
])
def test_json_is_recovered_from_however_it_is_wrapped(body):
    assert llm._json_from(body) == {"a": 1}


def test_unusable_output_is_reported_clearly():
    with pytest.raises(llm.LLMError):
        llm._json_from("I am afraid I cannot do that.")
    with pytest.raises(llm.LLMError):
        llm._json_from("")


def test_the_schema_is_spelled_out_for_models_without_schema_mode():
    note = llm._schema_note({"type": "object"})
    assert "ONE JSON object" in note
    assert llm._schema_note({}) == ""


def test_build_returns_the_right_backend():
    assert isinstance(llm.build("ollama:x"), llm.OllamaBackend)
    assert isinstance(llm.build("groq:x", keys={"groq": "k"}), llm.GroqBackend)
    assert isinstance(llm.build("gemini:x", keys={"gemini": "k"}), llm.GeminiBackend)


def test_groq_says_what_is_missing_rather_than_failing_obscurely():
    with pytest.raises(llm.LLMError) as err:
        llm.GroqBackend("m", api_key="").generate_json("p", {})
    assert "API key" in str(err.value)


def test_ollama_reports_an_unreachable_server_usefully(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(llm.requests, "post", boom)
    with pytest.raises(llm.LLMError) as err:
        llm.OllamaBackend("qwen").generate_json("p", {})
    assert "Is it running?" in str(err.value)


def test_ollama_names_the_missing_model(monkeypatch):
    class R:
        status_code = 404
        text = ""

        def json(self):
            return {}

    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: R())
    with pytest.raises(llm.LLMError) as err:
        llm.OllamaBackend("qwen2.5:7b").generate_json("p", {})
    assert "ollama pull qwen2.5:7b" in str(err.value)


def test_every_stage_is_named_once():
    ids = [s[0] for s in llm.STAGES]
    assert ids == ["read", "pick", "write", "review"]
    assert len(set(ids)) == len(ids)
    assert all(label and what for _i, label, what in llm.STAGES)
