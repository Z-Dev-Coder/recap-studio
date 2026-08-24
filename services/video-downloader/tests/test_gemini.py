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


def daily_limited(seconds=55):
    """A daily cap as Google actually reports it: a short retry delay attached."""
    return FakeResponse(429, {"error": {"details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
             "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo",
         "retryDelay": f"{seconds}s"}]}})


def minute_limited(seconds=20):
    return FakeResponse(429, {"error": {"details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo",
         "retryDelay": f"{seconds}s"}]}})


def test_the_two_limits_are_told_apart():
    assert gm.quota_scope(daily_limited()) == "day"
    assert gm.quota_scope(minute_limited()) == "minute"
    assert gm.quota_scope(limited(30)) == ""          # no violation block


def test_a_daily_cap_is_not_waited_out_however_short_the_delay(monkeypatch, no_sleep):
    """
    The reported delay is the per-minute window, so a daily cap arrives saying
    'retry in 55s'. Waiting on that twice makes the failure slower, not softer.
    """
    calls = []

    def post(*a, **k):
        calls.append(1)
        return daily_limited(55)

    monkeypatch.setattr(gm.requests, "post", post)
    with pytest.raises(gm.GeminiError) as err:
        gm.Gemini("key", "gemini-3.6-flash").generate_json("p", {})
    message = str(err.value)
    assert "per day" in message
    assert "midnight Pacific" in message
    assert "gemini-3.6-flash" in message
    assert no_sleep == []          # never waited
    assert len(calls) == 1         # never retried


def test_a_per_minute_limit_is_still_waited_out(monkeypatch, no_sleep):
    replies = [minute_limited(20), ok()]
    monkeypatch.setattr(gm.requests, "post", lambda *a, **k: replies.pop(0))
    assert gm.Gemini("key", "m").generate_json("p", {}) == {"answer": 1}
    assert no_sleep
