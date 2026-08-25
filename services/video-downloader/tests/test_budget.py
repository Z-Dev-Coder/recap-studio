"""
Staying inside a tokens-per-minute limit.

The failure these guard against is specific and was reproduced from the real
API: four calls in quick succession, about twelve thousand tokens between them,
against a budget of eight thousand a minute. Nothing was retried too eagerly
and nothing was duplicated -- the pipeline simply spent more than it had, and
only found out from the provider.
"""

from __future__ import annotations

import pytest

from ytdl.recap.budget import SAFETY, TokenBudget, estimate_tokens


@pytest.fixture
def budget():
    return TokenBudget()


# ------------------------------------------------------------- estimating

def test_burmese_is_not_counted_as_if_it_were_english():
    """
    Burmese has no spaces and tokenises far denser. Counting it at four
    characters a token would underestimate a Burmese prompt threefold, which
    is exactly the prompt this pipeline sends most of.
    """
    english = estimate_tokens("a" * 400)
    burmese = estimate_tokens("က" * 400)
    assert burmese > english * 2


def test_empty_text_costs_nothing():
    assert estimate_tokens("") == 0


# --------------------------------------------------------------- the budget

def test_a_request_that_fits_does_not_wait(budget):
    budget.declare("groq:m", 8000)
    assert budget.check("groq:m", 3000) == 0


def test_the_scenario_from_the_real_error(budget):
    """
    Limit 8000, Used 7075, Requested 2843 -- the exact figures the API
    returned. The point is that this must not be sent and must not become
    another 429.
    """
    budget.declare("groq:m", 8000)
    budget.spend("groq:m", 7075)
    wait = budget.check("groq:m", 2843)
    assert wait > 0, "a request that cannot fit was allowed through"
    assert wait <= 60


def test_spending_accumulates_across_calls(budget):
    """Four calls in a row must see each other's spending."""
    budget.declare("groq:m", 8000)
    for _ in range(3):
        budget.spend("groq:m", 2000)
    assert budget.check("groq:m", 3000) > 0


def test_room_frees_up_as_the_window_slides(budget, monkeypatch):
    import ytdl.recap.budget as mod

    now = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: now[0])

    budget.declare("groq:m", 8000)
    budget.spend("groq:m", 7000)
    assert budget.check("groq:m", 3000) > 0

    now[0] += 61                      # the spending has aged out
    assert budget.check("groq:m", 3000) == 0


def test_an_unknown_limit_never_blocks(budget):
    """A provider that publishes no limit must not be throttled by guesswork."""
    assert budget.check("ollama:qwen3:8b", 999_999) == 0


def test_headers_outrank_our_arithmetic(budget):
    """
    The provider counts what it actually charged, including anything spent by
    another process on the same key. Its figure wins.
    """
    budget.declare("groq:m", 8000)
    budget.spend("groq:m", 500)
    budget.observe("groq:m", {"x-ratelimit-limit-tokens": "8000",
                              "x-ratelimit-remaining-tokens": "200"})
    assert budget.check("groq:m", 3000) > 0


def test_headers_are_read_even_when_oddly_formatted(budget):
    budget.observe("groq:m", {"x-ratelimit-limit-tokens": "8000",
                              "x-ratelimit-remaining-tokens": "7500.0"})
    assert budget.snapshot("groq:m")["limit"] == 8000


def test_rubbish_headers_are_ignored_rather_than_crashing(budget):
    budget.declare("groq:m", 8000)
    budget.observe("groq:m", {"x-ratelimit-limit-tokens": "lots"})
    budget.observe("groq:m", None)
    assert budget.snapshot("groq:m")["limit"] == 8000


def test_the_safety_margin_keeps_us_under_the_line(budget):
    budget.declare("groq:m", 8000)
    left = budget.snapshot("groq:m")["remaining"]
    assert left < 8000
    assert left == int(8000 * SAFETY)


def test_models_are_budgeted_separately(budget):
    """The limits are per model, so one model's spending is not another's."""
    budget.declare("groq:big", 8000)
    budget.declare("groq:small", 8000)
    budget.spend("groq:big", 7800)
    assert budget.check("groq:big", 2000) > 0
    assert budget.check("groq:small", 2000) == 0


def test_a_snapshot_says_what_we_believe(budget):
    budget.declare("groq:m", 8000)
    budget.spend("groq:m", 1000)
    snap = budget.snapshot("groq:m")
    assert snap["limit"] == 8000
    assert snap["used_60s"] == 1000
    assert snap["remaining"] == int(8000 * SAFETY) - 1000
