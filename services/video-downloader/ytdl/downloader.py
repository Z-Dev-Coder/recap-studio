"""
Download logic. Thin, deliberate wrapper over yt-dlp's Python API.

Everything here is a pure function of its arguments except `download`
itself, so the option-building can be exercised without hitting the
network.
"""

import ctypes
import ctypes.wintypes
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# KNOWNFOLDERID for Downloads. Not derivable from USERPROFILE: users can
# relocate the folder, and OneDrive backup silently does it for them.
_FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


def _windows_downloads() -> Path | None:
    """Ask Windows where Downloads actually is, or None if that fails."""
    try:
        path_ptr = ctypes.c_wchar_p()
        guid = ctypes.create_string_buffer(16)
        if ctypes.windll.ole32.CLSIDFromString(_FOLDERID_DOWNLOADS, guid) != 0:
            return None
        if ctypes.windll.shell32.SHGetKnownFolderPath(
            guid, 0, None, ctypes.byref(path_ptr)
        ) != 0:
            return None
        try:
            return Path(path_ptr.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
    except (AttributeError, OSError):
        # Not Windows, or the shell API is unavailable.
        return None


def default_out_dir() -> Path:
    """
    Where files land when --out isn't given.

    YTDL_OUT wins if set, then the real Windows Downloads folder, then a
    plain guess. Downloads is where a browser would have put the file, so
    it's where you'll look for it.
    """
    if env := os.getenv("YTDL_OUT"):
        return Path(env)
    return _windows_downloads() or Path.home() / "Downloads"


DEFAULT_OUT = default_out_dir()


def _format_selector(quality: str, audio_only: bool) -> str:
    if audio_only:
        return "bestaudio/best"
    if quality == "best":
        return "bestvideo+bestaudio/best"
    # Prefer mp4/m4a so the merged file plays anywhere without a remux, then
    # fall back to whatever exists at that height (VP9/Opus-only uploads).
    #
    # The height-free tail matters off YouTube: Facebook, Instagram and TikTok
    # often report formats with no height at all, and a selector built only
    # from [height<=N] filters matches nothing and fails the download instead
    # of taking the single stream on offer.
    h = int(quality)
    return (
        f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={h}]+bestaudio/"
        f"best[height<={h}]/"
        "bestvideo+bestaudio/best"
    )


def _js_runtimes() -> dict:
    """
    JS runtimes to enable for YouTube stream-URL deciphering.

    yt-dlp enables only deno by default and fails with a misleading "This
    video is unavailable" when no runtime is found. Node is far more
    commonly installed, so enable whichever of the two is actually on PATH.
    """
    runtimes = {name: {} for name in ("deno", "node") if shutil.which(name)}
    return runtimes or {"deno": {}}


def build_options(
    out_dir: Path = DEFAULT_OUT,
    quality: str = "1080",
    audio_only: bool = False,
    subs: bool = False,
    playlist: bool = False,
    cookies_browser: str = "",
    keep_originals: bool = False,
) -> dict:
    """Build the yt-dlp options dict for a personal download."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    options: dict = {
        "format": _format_selector(quality, audio_only),
        # "<title> [<id>].<ext>" -- the id keeps two same-titled videos
        # apart and lets a re-run skip what's already on disk.
        "outtmpl": str(out_dir / "%(title).150B [%(id)s].%(ext)s"),
        "noplaylist": not playlist,
        "windowsfilenames": True,   # drop characters NTFS rejects
        "continuedl": True,
        "ignoreerrors": True,       # one dead video shouldn't kill a batch
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "keepvideo": keep_originals,
        "postprocessors": [],
        "js_runtimes": _js_runtimes(),
    }

    if audio_only:
        options["postprocessors"].append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        })
    else:
        # mkv as the second choice: some sites only offer streams that cannot
        # legally live in an mp4 container, and a failed merge loses the
        # download entirely.
        options["merge_output_format"] = "mp4/mkv"

    if subs and not audio_only:
        options.update({
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "en-US", "en-GB"],
            "subtitlesformat": "srt/best",
        })
        options["postprocessors"].append({
            "key": "FFmpegEmbedSubtitle",
            "already_have_subtitle": False,
        })

    # Title/artist/date in the container, so the file is still identifiable
    # in a media player months later.
    options["postprocessors"].append({"key": "FFmpegMetadata", "add_metadata": True})

    if cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)

    return options


def download(urls: list[str], **kwargs) -> int:
    """Download each URL. Returns yt-dlp's exit code (0 = everything fine)."""
    import yt_dlp

    with yt_dlp.YoutubeDL(build_options(**kwargs)) as ydl:
        return ydl.download(urls)


def list_formats(url: str, cookies_browser: str = "") -> None:
    """Print every format YouTube offers for `url`, then return."""
    import yt_dlp

    options = {
        "listformats": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": _js_runtimes(),
    }
    if cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=False)
