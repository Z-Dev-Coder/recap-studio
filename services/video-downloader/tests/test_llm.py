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


# ------------------------------------------------------------------ presets

def test_every_preset_covers_every_stage():
    stage_ids = {s[0] for s in llm.STAGES}
    for row in llm.PRESETS:
        assert set(row["stages"]) == stage_ids, row["id"]


def test_exactly_one_preset_is_recommended():
    assert sum(1 for p in llm.PRESETS if p["recommended"]) == 1


def test_the_recommended_preset_keeps_the_burmese_on_gemini():
    """
    The writing is the one stage whose quality is audible, so the preset meant
    for everyday use must not hand it to a weaker model.
    """
    daily = llm.preset("daily")
    assert daily["recommended"]
    assert daily["stages"]["write"] == ""        # follows the budget, ie Gemini
    assert all(daily["stages"][s] for s in ("read", "pick", "review"))


def test_the_offline_preset_asks_nothing_of_the_network():
    for spec in llm.preset("offline")["stages"].values():
        assert llm.split_spec(spec)[0] == "ollama"


def test_the_quality_preset_leaves_every_stage_on_the_budget():
    assert set(llm.preset("quality")["stages"].values()) == {""}


def test_presets_declare_what_they_need():
    assert "groq" in llm.preset("groq")["needs"]
    assert "ollama" in llm.preset("offline")["needs"]
    assert llm.preset("quality")["needs"] == ()


def test_an_unknown_preset_is_not_invented():
    assert llm.preset("nonsense") is None


def test_preset_models_are_real_specs():
    for row in llm.PRESETS:
        for spec in row["stages"].values():
            if spec:
                provider, model = llm.split_spec(spec)
                assert provider in llm.PROVIDERS
                assert model


# --------------------------------------------------- reasoning-model answers

class GroqReply:
    """A Groq response, shaped the way the real one is."""

    status_code = 200
    headers: dict = {}

    def __init__(self, content="", reasoning="", finish="stop"):
        self._body = {"choices": [{"finish_reason": finish, "message": {
            "role": "assistant", "content": content, "reasoning": reasoning}}]}
        self.text = ""

    def json(self):
        return self._body


def test_a_normal_answer_is_used(monkeypatch):
    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **k: GroqReply(content='{"a": 1}'))
    assert llm.GroqBackend("m", api_key="k").generate_json("p", {}) == {"a": 1}


def test_json_is_salvaged_from_the_thinking_when_the_answer_is_empty(monkeypatch):
    """
    gpt-oss splits its output between reasoning and answering. When the answer
    comes back empty the JSON is sometimes in the reasoning anyway, and using
    it is better than failing the step.
    """
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: GroqReply(
        content="", reasoning='I should answer with {"a": 2}'))
    assert llm.GroqBackend("m", api_key="k").generate_json("p", {}) == {"a": 2}


def test_running_out_of_room_says_so(monkeypatch):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: GroqReply(
        content="", reasoning="thinking...", finish="length"))
    with pytest.raises(llm.LLMError) as err:
        llm.GroqBackend("openai/gpt-oss-120b", api_key="k").generate_json("p", {})
    assert "ran out of room" in str(err.value)


def test_an_empty_answer_suggests_what_to_do(monkeypatch):
    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **k: GroqReply(content="", reasoning=""))
    with pytest.raises(llm.LLMError) as err:
        llm.GroqBackend("m", api_key="k").generate_json("p", {})
    assert "another model on this stage" in str(err.value)


def test_the_answer_is_given_room(monkeypatch):
    sent = {}

    def post(url, json=None, **k):
        sent.update(json or {})
        return GroqReply(content='{"a": 1}')

    monkeypatch.setattr(llm.requests, "post", post)
    llm.GroqBackend("openai/gpt-oss-120b", api_key="k").generate_json("p", {})
    assert sent["max_completion_tokens"] == llm.MAX_COMPLETION_TOKENS
    assert sent["reasoning_effort"] == "low"      # spend it answering


def test_a_model_without_that_knob_is_not_sent_it(monkeypatch):
    sent = {}

    def post(url, json=None, **k):
        sent.clear()
        sent.update(json or {})
        return GroqReply(content='{"a": 1}')

    monkeypatch.setattr(llm.requests, "post", post)
    llm.GroqBackend("qwen/qwen3.6-27b", api_key="k").generate_json("p", {})
    assert "reasoning_effort" not in sent


# ------------------------------------------------- the budget, through Groq

def test_groq_waits_rather_than_earning_a_429(monkeypatch):
    """
    The whole point: a request that will not fit must not be sent. Before this,
    the pipeline learned the limit by breaking it.
    """
    from ytdl.recap import budget as bmod

    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(llm, "BUDGET", bmod.TokenBudget())
    llm.BUDGET.declare("groq:m", 8000)
    llm.BUDGET.spend("groq:m", 7075)

    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **k: GroqReply(content='{"a": 1}'))
    llm.GroqBackend("m", api_key="k").generate_json("p" * 400, {}, max_tokens=1024)
    assert slept, "it sent a request that could not fit"


def test_a_wait_too_long_to_be_worth_it_says_so(monkeypatch):
    from ytdl.recap import budget as bmod

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "BUDGET", bmod.TokenBudget())
    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **k: GroqReply(content='{"a": 1}'))
    llm.BUDGET.declare("groq:m", 8000)

    with pytest.raises(llm.LLMError) as err:
        # more than a whole minute's allowance: no amount of waiting helps
        llm.GroqBackend("m", api_key="k").generate_json("p" * 200, {}, max_tokens=8192)
    assert "another provider" in str(err.value)


def test_what_it_really_cost_is_recorded(monkeypatch):
    from ytdl.recap import budget as bmod

    class Priced(GroqReply):
        def json(self):
            body = super().json()
            body["usage"] = {"total_tokens": 3210}
            return body

    monkeypatch.setattr(llm, "BUDGET", bmod.TokenBudget())
    llm.BUDGET.declare("groq:m", 8000)
    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **k: Priced(content='{"a": 1}'))
    llm.GroqBackend("m", api_key="k").generate_json("p", {})
    assert llm.BUDGET.snapshot("groq:m")["used_60s"] == 3210


def test_the_answer_size_is_the_caller_s_to_choose(monkeypatch):
    sent = {}

    def post(url, json=None, **k):
        sent.clear(); sent.update(json or {})
        return GroqReply(content='{"a": 1}')

    monkeypatch.setattr(llm.requests, "post", post)
    llm.GroqBackend("m", api_key="k").generate_json("p", {}, max_tokens=1024)
    assert sent["max_completion_tokens"] == 1024


def test_the_default_answer_size_is_not_a_whole_minute():
    """
    A flat sixteen thousand reserved twice the minute's entire allowance for
    one call, which is why it had to be sized to the task instead.
    """
    assert llm.MAX_COMPLETION_TOKENS <= 8000
