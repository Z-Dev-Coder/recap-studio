"""
Getting words with timestamps out of a video.

Platform captions first -- they are instant, free and already aligned. Only
when a video has none (most Facebook, TikTok and Instagram posts) does this
fall back to transcribing the audio locally.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Cue:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict:
        return asdict(self)


class TranscriptError(RuntimeError):
    """No captions, and no way to make any."""


# ---------------------------------------------------------------- parsing

_TS = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})"
)


def _seconds(match: re.Match) -> float:
    return (
        int(match["h"]) * 3600
        + int(match["m"]) * 60
        + int(match["s"])
        + int(match["ms"].ljust(3, "0")) / 1000
    )


def parse_timed_text(raw: str) -> list[Cue]:
    """
    Parse SRT or WebVTT into cues.

    The two formats differ only in the header and the decimal separator, and
    yt-dlp hands back whichever the site happened to offer, so one parser that
    tolerates both saves an entire branch of format juggling.
    """
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        stamps = None
        text_from = 0
        for i, line in enumerate(lines[:2]):
            found = list(_TS.finditer(line))
            if len(found) >= 2:
                stamps = (_seconds(found[0]), _seconds(found[1]))
                text_from = i + 1
                break
        if not stamps:
            continue
        text = " ".join(lines[text_from:]).strip()
        # VTT karaoke tags and stray markup
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text).strip()
        if not text:
            continue
        if cues and cues[-1].text == text:
            cues[-1].end = max(cues[-1].end, stamps[1])   # YouTube rolling captions
            continue
        cues.append(Cue(start=stamps[0], end=max(stamps[1], stamps[0] + 0.2), text=text))
    return cues


def to_srt(cues: list[Cue]) -> str:
    def stamp(t: float) -> str:
        t = max(0.0, t)
        h, rem = divmod(int(t), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{int(round((t - int(t)) * 1000)):03d}"

    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{stamp(c.start)} --> {stamp(c.end)}\n{c.text}\n")
    return "\n".join(out)


def plain_text(cues: list[Cue], with_times: bool = True) -> str:
    if not with_times:
        return " ".join(c.text for c in cues)
    lines = []
    for c in cues:
        m, s = divmod(int(c.start), 60)
        h, m = divmod(m, 60)
        stamp = f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"
        lines.append(f"[{stamp}] {c.text}")
    return "\n".join(lines)


# ---------------------------------------------------------------- sources

def from_platform(url: str, cookies_browser: str = "") -> tuple[list[Cue], str]:
    """
    Pull captions the site already has. Returns (cues, language).

    Manual captions beat automatic ones, and English beats everything else
    only because the recap prompts read better from a clean transcript --
    any language is accepted rather than failing.
    """
    import yt_dlp

    from ..downloader import _js_runtimes

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "js_runtimes": _js_runtimes(),
        "noplaylist": True,
    }
    if cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    for pool in ("subtitles", "automatic_captions"):
        tracks = info.get(pool) or {}
        if not tracks:
            continue
        langs = sorted(
            tracks,
            key=lambda code: (
                0 if code.startswith("en") else 1 if code.startswith("my") else 2,
                len(code),
            ),
        )
        for lang in langs:
            for fmt in tracks[lang]:
                if fmt.get("ext") not in ("vtt", "srt"):
                    continue
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        fmt["url"], headers={"User-Agent": "Mozilla/5.0"}
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        raw = resp.read().decode("utf-8", "replace")
                except Exception:      # noqa: BLE001 - try the next track
                    continue
                cues = parse_timed_text(raw)
                if cues:
                    return cues, lang
    return [], ""


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def from_whisper(audio: Path, model_size: str = "small", language: str = "", cancel=None) -> tuple[list[Cue], str]:
    """
    Transcribe locally. Optional dependency: nothing else here needs it, and
    it pulls in a few hundred MB, so it is imported only when actually used.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptError(
            "This video has no captions, so it needs local transcription.\n"
            "Install it once with:\n"
            r"  venv\Scripts\python.exe -m pip install faster-whisper"
        ) from exc

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio), language=language or None, vad_filter=True
    )
    # segments is a generator that transcribes as it is consumed, so checking
    # here is what makes a long transcription stoppable
    from .media import Cancelled

    cues = []
    for s in segments:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if s.text and s.text.strip():
            cues.append(Cue(start=float(s.start), end=float(s.end), text=s.text.strip()))
    return cues, getattr(info, "language", "") or language
