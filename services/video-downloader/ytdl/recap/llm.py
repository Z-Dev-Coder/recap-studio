"""
Where each stage of the script gets its thinking done.

The pipeline asks four separate questions of a model -- read the video, choose
the moments, write the Burmese, check the Burmese -- and they are not equally
demanding. Reading produces structured facts, which a small local model does
adequately. Writing Burmese narration is the one whose quality is audible in
the finished video.

Gemini's free tier allows twenty requests a day on its better model, so four
calls a script means five scripts a day. That is the constraint this module
exists to lift: a stage can be pointed at whichever provider suits it, so the
scarce quota is spent only where it shows.

Three providers, one interface:

* gemini -- the best writing available here, on a small daily allowance.
* ollama -- runs on this machine. No quota, no key, no network. Weakest at
  Burmese prose; perfectly capable of structured extraction.
* groq   -- free, and generous enough not to think about. Open-weight models,
  somewhere between the two for Burmese.

A stage is named as "provider:model" -- "ollama:qwen2.5:3b", "gemini:", or
just "gemini-3.6-flash" for the provider the app has always used.
"""

from __future__ import annotations

import base64
import json
import re
import time

import requests

from .gemini import Gemini, GeminiError

OLLAMA_URL = "http://127.0.0.1:11434"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

PROVIDERS = ("gemini", "ollama", "groq")

# Groq is generous but not unlimited, and its limits are per minute as well as
# per day. A per-minute one is worth waiting out -- it is usually seconds --
# where a daily one is not, exactly as with Gemini.
RATE_RETRIES = 2
MAX_RATE_WAIT = 75.0

# The stages, in the order they run, with what each one is for. Named here so
# the settings page and the pipeline cannot disagree about what exists.
STAGES = (
    ("read", "Read the video", "Pulls out the events, causes and cast. Structured notes, not prose."),
    ("pick", "Choose the moments", "Picks which moments to use and writes the English line."),
    ("write", "Write the Burmese", "The narration itself. This is the one you hear."),
    ("review", "Check the Burmese", "Revises only the lines flagged as wrong."),
)


# Ready-made ways to spread the four stages across the three providers.
#
# Every provider here is free and every one is limited, but they are limited
# differently -- Gemini by a small daily count, Groq by tokens per minute, a
# local model only by how fast the machine is. Spreading the stages across
# them is what turns "five scripts a day" into "as many as you have patience
# for", and the only stage that really wants the best model is the one writing
# the Burmese.
#
# `needs` names what has to be set up for a preset to work, so the settings
# page can say which are actually available rather than offering all of them.
PRESETS = (
    {
        "id": "daily",
        "label": "Daily use, long videos",
        "note": "One Gemini call per script -- about 20 a day. Reading and "
                "choosing go to Groq, checking runs on this machine.",
        "recommended": True,
        "needs": ("groq", "ollama"),
        "stages": {
            "read": "groq:openai/gpt-oss-120b",
            "pick": "groq:openai/gpt-oss-120b",
            "write": "",                       # follows the quality budget
            "review": "ollama:qwen2.5:7b-instruct-q4_K_M",
        },
    },
    {
        "id": "groq",
        "label": "Mostly Groq",
        "note": "For when Ollama is not running. Everything but the Burmese "
                "goes to Groq; watch its per-minute token limit on long "
                "transcripts.",
        "recommended": False,
        "needs": ("groq",),
        "stages": {
            "read": "groq:openai/gpt-oss-120b",
            "pick": "groq:openai/gpt-oss-120b",
            "write": "",
            "review": "groq:openai/gpt-oss-20b",
        },
    },
    {
        "id": "offline",
        "label": "No quota at all",
        "note": "Everything on this machine. Nothing to run out of, and the "
                "Burmese will read noticeably plainer.",
        "recommended": False,
        "needs": ("ollama",),
        "stages": {
            "read": "ollama:qwen2.5:7b-instruct-q4_K_M",
            "pick": "ollama:qwen2.5:7b-instruct-q4_K_M",
            "write": "ollama:qwen2.5:7b-instruct-q4_K_M",
            "review": "ollama:qwen2.5:7b-instruct-q4_K_M",
        },
    },
    {
        "id": "quality",
        "label": "Best quality",
        "note": "Gemini throughout. The best results this app can produce, "
                "and the fewest runs before the daily limit.",
        "recommended": False,
        "needs": (),
        "stages": {"read": "", "pick": "", "write": "", "review": ""},
    },
)


def preset(pid: str) -> dict | None:
    for row in PRESETS:
        if row["id"] == pid:
            return row
    return None


class LLMError(RuntimeError):
    """Anything that stops a generation, phrased for the person reading it."""


def split_spec(spec: str) -> tuple[str, str]:
    """
    "provider:model" -> ("provider", "model").

    A bare model name means Gemini, because that is what every setting in this
    app meant before there was a choice.
    """
    spec = (spec or "").strip()
    if not spec:
        return "gemini", ""
    head, sep, rest = spec.partition(":")
    if sep and head.lower() in PROVIDERS:
        return head.lower(), rest.strip()
    return "gemini", spec


def _json_from(text: str) -> dict:
    """
    Get a JSON object out of whatever the model said.

    Only Gemini is asked for JSON by schema. The others are asked in the
    prompt, and answer with fenced code blocks, a sentence of preamble, or a
    trailing explanation often enough that pulling the object out is part of
    the job rather than an error case.
    """
    text = (text or "").strip()
    if not text:
        raise LLMError("the model returned nothing")

    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except ValueError:
        pass

    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    raise LLMError("the model did not return usable JSON")


def _schema_note(schema: dict) -> str:
    """The shape being asked for, spelled out for models with no schema mode."""
    if not schema:
        return ""
    return (
        "\n\nReturn ONE JSON object and nothing else -- no explanation, no "
        "code fence -- matching exactly this shape:\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
    )


class GeminiBackend:
    """The provider this app was built on."""

    name = "gemini"

    def __init__(self, model: str, api_key: str = "", timeout: int = 180):
        self.model = model
        self.client = Gemini(api_key, model, timeout=timeout)

    def generate_json(self, prompt, schema, temperature=0.7, images=None, cancel=None):
        try:
            return self.client.generate_json(prompt, schema, temperature,
                                             images=images, cancel=cancel)
        except GeminiError as exc:
            raise LLMError(str(exc)) from exc


class OllamaBackend:
    """
    A model running on this machine. No quota and no key.

    Images are passed through when given: whether they mean anything depends
    on the model, and a text-only model ignoring them is better than refusing
    to run at all.
    """

    name = "ollama"

    def __init__(self, model: str, url: str = "", timeout: int = 600):
        self.model = model or "qwen2.5:3b"
        self.url = (url or OLLAMA_URL).rstrip("/")
        self.timeout = timeout

    def generate_json(self, prompt, schema, temperature=0.7, images=None, cancel=None):
        body = {
            "model": self.model,
            "prompt": prompt + _schema_note(schema),
            "stream": False,
            "format": "json",
            "options": {"temperature": temperature},
        }
        if images:
            body["images"] = [base64.b64encode(blob).decode("ascii")
                              for _mime, blob in images]
        try:
            resp = requests.post(f"{self.url}/api/generate", json=body,
                                 timeout=self.timeout)
        except requests.RequestException as exc:
            raise LLMError(
                "Could not reach Ollama at {}. Is it running? ({})".format(self.url, exc)
            ) from exc

        if resp.status_code == 404:
            raise LLMError(
                "Ollama has no model called {!r}. Pull it first: ollama pull {}"
                .format(self.model, self.model)
            )
        if resp.status_code >= 400:
            raise LLMError(f"Ollama refused the request ({resp.status_code}): {resp.text[:200]}")

        try:
            return _json_from(resp.json().get("response", ""))
        except ValueError as exc:
            raise LLMError(f"Ollama returned something unreadable: {exc}") from exc


def _groq_wait(resp) -> float:
    """How long Groq says to wait, from the header or the message."""
    try:
        header = float(resp.headers.get("retry-after") or 0)
        if header > 0:
            return header
    except (TypeError, ValueError):
        pass
    # otherwise it is in the prose: "Please try again in 7.5s"
    found = re.search(r"try again in ([\d.]+)\s*(m|s)", resp.text or "", re.I)
    if found:
        value = float(found.group(1))
        return value * 60 if found.group(2).lower() == "m" else value
    return 0.0


def _groq_daily(resp) -> bool:
    """Whether the allowance that ran out was the daily one."""
    text = (resp.text or "").lower()
    return "per day" in text or "requests per day" in text or "rpd" in text


def _groq_limit_message(resp, model: str) -> str:
    if _groq_daily(resp):
        return (
            "Groq's daily allowance for {} is used up. It resets on a rolling "
            "24-hour window. Point this stage at another provider in Settings, "
            "or pick a different Groq model -- the limits are per model."
        ).format(model)
    wait = _groq_wait(resp)
    when = " Try again in about {:.0f}s.".format(wait) if wait > 0 else ""
    return (
        "Groq is rate limiting this key for {}.{} The per-minute limit counts "
        "tokens as well as requests, and a long transcript is a lot of tokens."
    ).format(model, when)


class GroqBackend:
    """Free, fast, and generous enough that the daily limit stops mattering."""

    name = "groq"

    def __init__(self, model: str, api_key: str = "", timeout: int = 180):
        self.model = model or "openai/gpt-oss-120b"
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    def generate_json(self, prompt, schema, temperature=0.7, images=None, cancel=None):
        if not self.api_key:
            raise LLMError("Groq needs an API key. Add one in Settings.")
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt + _schema_note(schema)}],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}

        # Not every model on Groq honours JSON mode -- some reject the request
        # outright with a 400 rather than answering. Asking again without it
        # still works, because the shape is in the prompt as well and the
        # answer is parsed out of whatever wrapping comes back.
        for enforce_json in (True, False):
            if not enforce_json:
                body.pop("response_format", None)

            # A per-minute limit is worth waiting out; a daily one is not.
            for attempt in range(RATE_RETRIES + 1):
                try:
                    resp = requests.post(GROQ_URL, json=body, timeout=self.timeout,
                                         headers=headers)
                except requests.RequestException as exc:
                    raise LLMError(f"Could not reach Groq: {exc}") from exc
                if resp.status_code != 429 or _groq_daily(resp):
                    break
                wait = _groq_wait(resp)
                if attempt >= RATE_RETRIES or not 0 < wait <= MAX_RATE_WAIT:
                    break
                if cancel is not None and cancel.is_set():
                    break
                time.sleep(wait + 0.5)

            if not (resp.status_code == 400 and enforce_json
                    and "json" in resp.text.lower()):
                break

        if resp.status_code == 401:
            raise LLMError("Groq rejected the API key. Check it in Settings.")
        if resp.status_code == 429:
            raise LLMError(_groq_limit_message(resp, self.model))
        if resp.status_code >= 400:
            raise LLMError(f"Groq refused the request ({resp.status_code}): {resp.text[:200]}")

        try:
            choice = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Groq returned something unexpected: {exc}") from exc
        return _json_from(choice)


def build(spec: str, keys: dict | None = None, ollama_url: str = "") -> object:
    """
    A backend for "provider:model".

    Unknown providers fall back to Gemini rather than failing, because a
    mistyped setting should cost quality, not the whole run.
    """
    keys = keys or {}
    provider, model = split_spec(spec)
    if provider == "ollama":
        return OllamaBackend(model, url=ollama_url)
    if provider == "groq":
        return GroqBackend(model, api_key=keys.get("groq", ""))
    return GeminiBackend(model, api_key=keys.get("gemini", ""))


def ollama_models(url: str = "", timeout: int = 4) -> list[str]:
    """What this machine can run right now, for the settings page."""
    try:
        resp = requests.get(f"{(url or OLLAMA_URL).rstrip('/')}/api/tags", timeout=timeout)
        if resp.status_code >= 400:
            return []
        return sorted(m.get("name", "") for m in resp.json().get("models", []) if m.get("name"))
    except (requests.RequestException, ValueError):
        return []


def ollama_available(url: str = "") -> bool:
    return bool(ollama_models(url))
