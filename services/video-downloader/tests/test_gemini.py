"""
The Gemini client's behaviour around the free tier's rate limit.

A 429 on the free tier means "you have made too many calls this minute" and
carries the exact wait. Failing the step on that threw away the calls already
paid for, so these tests pin the waiting behaviour -- and, just as importantly,
that a long wait is still reported rather than silently sat through.
"""

from __future__ import annotations

import pytest

from ytdl.recap import gemini as gm


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


def limited(seconds):
    return FakeResponse(429, {"error": {"details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo",
         "retryDelay": f"{seconds}s"}]}})


def ok():
    return FakeResponse(200, {"candidates": [
        {"content": {"parts": [{"text": '{"answer": 1}'}]}}]})


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(gm.time, "sleep", lambda s: slept.append(s))
    return slept


def test_a_short_limit_is_waited_out(monkeypatch, no_sleep):
    replies = [limited(22), ok()]
    monkeypatch.setattr(gm.requests, "post", lambda *a, **k: replies.pop(0))
    out = gm.Gemini("key", "model").generate_json("p", {})
    assert out == {"answer": 1}
    assert no_sleep and no_sleep[0] >= 22       # waited, with a little margin


def test_it_gives_up_after_the_retry_budget(monkeypatch, no_sleep):
    monkeypatch.setattr(gm.requests, "post", lambda *a, **k: limited(5))
    with pytest.raises(gm.GeminiError) as err:
        gm.Gemini("key", "model").generate_json("p", {})
    assert "limit reached" in str(err.value)
    assert len(no_sleep) == gm.RATE_LIMIT_RETRIES


def test_a_daily_cap_is_reported_not_waited_out(monkeypatch, no_sleep):
    """A wait measured in hours is news, not something to sit through."""
    monkeypatch.setattr(gm.requests, "post", lambda *a, **k: limited(7200))
    with pytest.raises(gm.GeminiError):
        gm.Gemini("key", "model").generate_json("p", {})
    assert no_sleep == []


def test_waiting_stops_when_the_user_stops(monkeypatch, no_sleep):
    import threading
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(gm.requests, "post", lambda *a, **k: limited(10))
    with pytest.raises(gm.GeminiError):
        gm.Gemini("key", "model").generate_json("p", {}, cancel=stop)
    assert no_sleep == []


def test_other_failures_are_not_retried(monkeypatch, no_sleep):
    calls = []

    def post(*a, **k):
        calls.append(1)
        return FakeResponse(401)

    monkeypatch.setattr(gm.requests, "post", post)
    with pytest.raises(gm.GeminiError) as err:
        gm.Gemini("key", "model").generate_json("p", {})
    assert "API key" in str(err.value)
    assert len(calls) == 1
