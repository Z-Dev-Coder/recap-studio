"""
One model in the card at a time.

The voice model needs 5.9GB of a 6GB card. A language model left loaded from
the script step holds four of those, so the narration runs half on the CPU at
a crawl, or fails outright -- on a machine that looks idle, because nothing is
running. Whoever wants the card asks for it back first.
"""

import pytest

from ytdl.recap import llm


class _Resp:
    status_code = 200

    def __init__(self, payload=None):
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_everything_loaded_is_dropped(monkeypatch):
    posted = []
    monkeypatch.setattr(llm.requests, "get",
                        lambda *a, **k: _Resp({"models": [{"name": "qwen2.5:7b"},
                                                          {"name": "gemma3:4b"}]}))
    monkeypatch.setattr(llm.requests, "post",
                        lambda url, json=None, **k: posted.append(json) or _Resp())

    assert llm.unload_ollama() == ["qwen2.5:7b", "gemma3:4b"]
    assert all(p["keep_alive"] == 0 for p in posted), "keep_alive 0 is the unload"
    assert all(p["prompt"] == "" for p in posted), "and it must generate nothing"


def test_nothing_loaded_means_nothing_to_do(monkeypatch):
    monkeypatch.setattr(llm.requests, "get", lambda *a, **k: _Resp({"models": []}))
    monkeypatch.setattr(llm.requests, "post",
                        lambda *a, **k: pytest.fail("nothing should be unloaded"))
    assert llm.unload_ollama() == []


def test_no_ollama_at_all_is_not_an_error(monkeypatch):
    """This is housekeeping before real work, not the work itself."""
    def refused(*a, **k):
        raise llm.requests.ConnectionError("refused")

    monkeypatch.setattr(llm.requests, "get", refused)
    assert llm.unload_ollama() == []


def test_one_model_can_be_named(monkeypatch):
    posted = []
    monkeypatch.setattr(llm.requests, "get",
                        lambda *a, **k: pytest.fail("no need to ask what is loaded"))
    monkeypatch.setattr(llm.requests, "post",
                        lambda url, json=None, **k: posted.append(json) or _Resp())

    assert llm.unload_ollama(model="qwen2.5:7b") == ["qwen2.5:7b"]
    assert posted[0]["model"] == "qwen2.5:7b"


def test_a_generate_call_does_not_hold_the_card_for_five_minutes(monkeypatch):
    """Ollama's default keep_alive outlasts the script step by a long way."""
    seen = {}
    monkeypatch.setattr(llm.requests, "post",
                        lambda url, json=None, **k: seen.update(json or {})
                        or _Resp({"response": "{}"}))
    llm.OllamaBackend("m").generate_json("p", {})
    assert seen["keep_alive"] == "90s"
