"""
Staying inside a provider's tokens-per-minute limit instead of discovering it.

Groq's free tier allows 8,000 tokens a minute per model. One recap costs about
twelve thousand across four calls, and the pipeline made them back to back, so
the limit was hit every time -- not because of how many calls there were, but
because of how many tokens went through in the same minute:

    Rate limit reached ... on tokens per minute (TPM):
    Limit 8000, Used 7075, Requested 2843

The fix is to know the budget before spending it. This keeps a rolling
sixty-second record of what has been sent per provider-and-model, corrects it
against the figures the provider returns in its headers -- which are
authoritative where our estimate is not -- and makes the caller wait, or go
elsewhere, when the next request will not fit.

Deliberately not a queue or a scheduler. The pipeline is sequential, so one
lock and a list of timestamps is the whole mechanism.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

WINDOW = 60.0            # the limits are per minute
SAFETY = 0.92            # aim slightly under, since our estimate is an estimate


def estimate_tokens(text: str) -> int:
    """
    Roughly how many tokens a piece of text will cost.

    English runs about four characters to the token. Burmese is far denser --
    no spaces, and the tokenisers used here split it into short pieces -- so it
    is counted separately at about 1.5, which stops a Burmese prompt from being
    silently underestimated to a third of its real cost.
    """
    if not text:
        return 0
    burmese = sum(1 for c in text if "က" <= c <= "႟")
    return int((len(text) - burmese) / 4 + burmese / 1.5) + 8


@dataclass
class Limit:
    """What one model on one provider allows, as far as we currently know."""

    tokens_per_minute: int = 0        # 0 means "no limit known"
    spent: list[tuple[float, int]] = field(default_factory=list)
    reported_remaining: int | None = None
    reported_at: float = 0.0

    def prune(self, now: float) -> None:
        cutoff = now - WINDOW
        self.spent = [(t, n) for t, n in self.spent if t > cutoff]

    def used(self, now: float) -> int:
        self.prune(now)
        return sum(n for _t, n in self.spent)

    def remaining(self, now: float) -> int | None:
        """
        How much is left, preferring the provider's own figure.

        A header read seconds ago beats our arithmetic, because it counts what
        the provider actually charged rather than what we guessed -- including
        anything spent by another process using the same key.
        """
        if not self.tokens_per_minute:
            return None
        if self.reported_remaining is not None and now - self.reported_at < 10:
            return self.reported_remaining
        return max(0, int(self.tokens_per_minute * SAFETY) - self.used(now))

    def wait_for(self, want: int, now: float) -> float:
        """
        Seconds to wait before `want` tokens will fit. 0 if they fit now, and
        infinity if they never will -- a request larger than the whole minute's
        allowance cannot be waited into existence, and saying so immediately is
        better than sleeping a minute to fail anyway.
        """
        left = self.remaining(now)
        if left is None or want <= left:
            return 0.0
        if want > int(self.tokens_per_minute * SAFETY):
            return float("inf")
        # wait for enough of the oldest spending to age out of the window
        self.prune(now)
        need = want - left
        freed = 0
        for when, amount in self.spent:
            freed += amount
            if freed >= need:
                return max(0.0, when + WINDOW - now)
        return WINDOW if self.spent else 0.0


class TokenBudget:
    """
    What has been spent lately, per provider and model.

    Shared across the pipeline: the point is that four calls in a row see each
    other's spending, which is exactly what was missing.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._limits: dict[str, Limit] = {}

    def _limit(self, key: str) -> Limit:
        if key not in self._limits:
            self._limits[key] = Limit()
        return self._limits[key]

    def declare(self, key: str, tokens_per_minute: int) -> None:
        """Record a limit, learned from a response header or configuration."""
        if tokens_per_minute <= 0:
            return
        with self._lock:
            self._limit(key).tokens_per_minute = int(tokens_per_minute)

    def check(self, key: str, want: int) -> float:
        """How long to wait before spending `want` tokens. 0 means go ahead."""
        with self._lock:
            return self._limit(key).wait_for(want, time.time())

    def spend(self, key: str, tokens: int) -> None:
        with self._lock:
            limit = self._limit(key)
            limit.spent.append((time.time(), max(0, int(tokens))))
            limit.reported_remaining = None      # our own record is now stale

    def observe(self, key: str, headers) -> None:
        """
        Take the provider's word for it.

        Groq returns the limit and what is left on every response; those are
        worth more than our estimate, so they replace it.
        """
        def number(name: str) -> int | None:
            raw = (headers or {}).get(name)
            if raw is None:
                return None
            try:
                return int(float(str(raw).rstrip("s")))
            except (TypeError, ValueError):
                return None

        limit = number("x-ratelimit-limit-tokens")
        left = number("x-ratelimit-remaining-tokens")
        with self._lock:
            row = self._limit(key)
            if limit:
                row.tokens_per_minute = limit
            if left is not None:
                row.reported_remaining = left
                row.reported_at = time.time()

    def snapshot(self, key: str) -> dict:
        """For logging: what we believe about this model right now."""
        with self._lock:
            row = self._limit(key)
            now = time.time()
            return {
                "limit": row.tokens_per_minute,
                "used_60s": row.used(now),
                "remaining": row.remaining(now),
            }


# One budget for the process. The pipeline is sequential and single-process, so
# a module-level instance is the whole of the sharing that is needed.
BUDGET = TokenBudget()
