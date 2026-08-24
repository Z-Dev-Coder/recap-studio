"""
Google Gemini client (free tier).

Deliberately thin: one JSON-returning call, built on requests, with the
failure modes that actually happen -- no key, bad key, quota exhausted --
turned into sentences a person can act on rather than raw HTTP codes.
"""

from __future__ import annotations

import json

import time

import requests

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

# What we ask each generation for. Burmese costs several tokens per syllable,
# so a package carrying both languages runs long; too small a cap does not
# error, it truncates mid-object and the Burmese written second is what goes
# missing.
# How many times to sit out a rate limit, and the longest wait worth
# sitting out. Beyond this it is a daily cap rather than a burst, and the user
# needs telling rather than a frozen button.
# The free tier's binding limit is requests per DAY, not per minute:
# gemini-3.6-flash allows 20, while gemini-3.1-flash-lite allows 500. Four
# calls per script therefore means five scripts a day on the better model and
# a hundred and twenty-five on the lighter one -- so the stages that do not
# need the better model should not spend its quota. See LIGHT_MODEL.
LIGHT_MODEL = "gemini-3.1-flash-lite"

RATE_LIMIT_RETRIES = 2
MAX_RATE_WAIT = 75.0

MAX_OUTPUT_TOKENS = 32768

# Free-tier models, best first. Google retires these from under new keys --
# a key issued today is refused by last year's default with a 404 that reads
# like a broken key rather than a stale model name -- so a failed default
# falls back to whatever this key can actually see. Keep the newest first.
DEFAULT_MODELS = (
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
)


class GeminiError(RuntimeError):
    """Anything that stops a generation, phrased for the person reading it."""


def retry_after(resp) -> float:
    """
    Seconds until the quota resets, straight from Google's own answer.

    A 429 carries a RetryInfo block saying exactly how long to wait; guessing
    "a minute" when the API already said "37s" just makes people wait longer
    than they need to.
    """
    try:
        for detail in resp.json().get("error", {}).get("details", []) or []:
            delay = detail.get("retryDelay") or detail.get("retry_delay")
            if isinstance(delay, str) and delay.endswith("s"):
                return float(delay[:-1] or 0)
            if isinstance(delay, dict):
                return float(delay.get("seconds") or 0)
    except (ValueError, TypeError):
        pass
    try:
        return float(resp.headers.get("Retry-After") or 0)
    except (TypeError, ValueError):
        return 0.0


def retry_hint(resp) -> str:
    secs = retry_after(resp)
    if secs <= 0:
        return "Wait a minute and try again, or pick another model in Settings."
    if secs < 90:
        return f"It resets in about {int(secs)}s -- try again then."
    return f"It resets in about {int(secs / 60)} min -- try again then."


class Gemini:
    def __init__(self, api_key: str, model: str = "", timeout: int = 180):
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip() or DEFAULT_MODELS[0]
        self.timeout = timeout
        if not self.api_key:
            raise GeminiError(
                "No Gemini API key set. Get a free one at "
                "https://aistudio.google.com/apikey and paste it into Recap "
                "Studio settings."
            )

    # ------------------------------------------------------------------
    def generate_json(
        self,
        prompt: str,
        schema: dict,
        temperature: float = 0.7,
        images: list[tuple[str, bytes]] | None = None,
        cancel=None,
    ) -> dict:
        """
        Ask for JSON and get a dict back, or raise something readable.

        `images` are (mime, bytes) pairs sent alongside the prompt -- frames
        from the video, so the model can describe what is actually on screen
        instead of inferring it from the words alone.
        """
        import base64

        parts: list[dict] = [{"text": prompt}]
        for mime, blob in images or []:
            parts.append({
                "inline_data": {
                    "mime_type": mime,
                    "data": base64.b64encode(blob).decode("ascii"),
                }
            })

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
            },
        }
        url = f"{API_ROOT}/models/{self.model}:generateContent"

        # The free tier's limit is per minute, and a 429 says exactly how long
        # to wait -- usually seconds. Failing the whole step on that threw away
        # the calls already paid for and made the user press the button again
        # for no reason, so wait it out instead. Only a short wait is worth
        # sitting through; a daily cap reports a delay far longer than this and
        # falls through to the error, where it belongs.
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                resp = requests.post(
                    url,
                    params={"key": self.api_key},
                    json=body,
                    timeout=self.timeout,
                    headers={"Content-Type": "application/json"},
                )
            except requests.RequestException as exc:
                raise GeminiError(f"Could not reach Gemini: {exc}") from exc

            if resp.status_code != 429:
                break
            wait = retry_after(resp)
            if attempt >= RATE_LIMIT_RETRIES or not 0 < wait <= MAX_RATE_WAIT:
                break
            if cancel is not None and cancel.is_set():
                break
            # a second of margin: coming back exactly on the boundary re-trips it
            time.sleep(wait + 1.0)

        if resp.status_code == 429:
            raise GeminiError(
                "Gemini free-tier limit reached. " + retry_hint(resp)
            )
        if resp.status_code in (401, 403):
            raise GeminiError("Gemini rejected the API key. Check it in settings.")
        if resp.status_code == 404:
            # the model was retired: find one this key can use and say so
            alternative = self._first_usable()
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except ValueError:
                pass
            raise GeminiError(
                f"Model '{self.model}' is not available for this key."
                + (f" Try '{alternative}' in settings." if alternative else "")
                + (f"\n{detail}" if detail else "")
            )
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except ValueError:
                detail = resp.text[:300]
            raise GeminiError(f"Gemini error {resp.status_code}: {detail}")

        try:
            payload = resp.json()
            candidates = payload.get("candidates") or []
            if not candidates:
                blocked = (payload.get("promptFeedback") or {}).get("blockReason")
                raise GeminiError(
                    f"Gemini returned nothing (blocked: {blocked})" if blocked
                    else "Gemini returned no candidates."
                )
            parts = candidates[0].get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                # hit the output cap mid-object: the JSON is unusable
                reason = candidates[0].get("finishReason", "")
                raise GeminiError(f"Gemini returned an empty answer ({reason}).")
            return json.loads(text)
        except (ValueError, KeyError) as exc:
            raise GeminiError(f"Gemini sent something that was not JSON: {exc}") from exc

    def _first_usable(self) -> str:
        """A model this key can actually see, for the error message to name."""
        try:
            for name in self.list_models(self.api_key):
                if "flash" in name and "image" not in name and "tts" not in name:
                    return name
        except GeminiError:
            pass
        return ""

    # ------------------------------------------------------------------
    @staticmethod
    def model_details(api_key: str) -> list[dict]:
        """Every usable model with the token ceilings Google reports for it."""
        try:
            resp = requests.get(
                f"{API_ROOT}/models", params={"key": api_key}, timeout=30
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise GeminiError(f"Could not list models: {exc}") from exc

        rows = []
        for m in resp.json().get("models", []):
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            name = (m.get("name") or "").removeprefix("models/")
            if not name:
                continue
            rows.append({
                "name": name,
                "input_limit": m.get("inputTokenLimit") or 0,
                "output_limit": m.get("outputTokenLimit") or 0,
                "display": m.get("displayName") or name,
            })
        rows.sort(key=lambda r: ("flash" not in r["name"], "3." not in r["name"], r["name"]))
        return rows

    @staticmethod
    def list_models(api_key: str) -> list[str]:
        """Model ids this key may actually use, newest-looking first."""
        try:
            resp = requests.get(
                f"{API_ROOT}/models", params={"key": api_key}, timeout=30
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise GeminiError(f"Could not list models: {exc}") from exc

        names = []
        for m in resp.json().get("models", []):
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            name = (m.get("name") or "").removeprefix("models/")
            if name:
                names.append(name)
        names.sort(key=lambda n: ("flash" not in n, "2." not in n, n))
        return names
