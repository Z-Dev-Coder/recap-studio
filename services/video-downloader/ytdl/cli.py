"""Command line entry point: `python -m ytdl <url> [options]`."""

import argparse
import shutil
import sys
from pathlib import Path

from .downloader import DEFAULT_OUT, download, list_formats


def _check_ffmpeg() -> None:
    """
    Warn early rather than after a long download.

    Merging video+audio, extracting mp3, and embedding subtitles all shell
    out to ffmpeg; without it yt-dlp silently falls back to a lower-quality
    single-file format.
    """
    if shutil.which("ffmpeg") is None:
        print(
            "WARNING: ffmpeg not found on PATH. Quality will be capped and "
            "--audio/--subs will fail.\n"
            "         Install it with: winget install Gyan.FFmpeg\n",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ytdl",
        description="Download YouTube (or any yt-dlp supported) video for personal use.",
        epilog="Examples:\n"
               "  ytdl https://youtu.be/xxxx\n"
               "  ytdl -a https://youtu.be/xxxx            (mp3 only)\n"
               "  ytdl -q 720 -s https://youtu.be/xxxx     (720p with subtitles)\n"
               "  ytdl -p <playlist-url>                   (whole playlist)\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("urls", nargs="+", help="video, playlist, or channel URLs")
    parser.add_argument("-a", "--audio", action="store_true", help="audio only (mp3)")
    parser.add_argument("-q", "--quality", default="1080",
                        help="max height: 480/720/1080/1440/2160, or 'best' (default: 1080)")
    parser.add_argument("-o", "--out", default=str(DEFAULT_OUT),
                        help=f"output directory (default: {DEFAULT_OUT})")
    parser.add_argument("-s", "--subs", action="store_true",
                        help="embed English subtitles if available")
    parser.add_argument("-p", "--playlist", action="store_true",
                        help="download the whole playlist, not just the linked video")
    parser.add_argument("-k", "--keep", action="store_true",
                        help="keep the separate video/audio files after merging")
    parser.add_argument("-f", "--list-formats", action="store_true",
                        help="show available formats for the first URL and exit")
    parser.add_argument("-c", "--cookies-browser", default="", metavar="BROWSER",
                        help="read cookies from a browser (chrome, edge, firefox...) "
                             "for age-restricted or members-only videos")
    args = parser.parse_args(argv)

    if args.quality != "best" and not args.quality.isdigit():
        parser.error("--quality must be a number (e.g. 1080) or 'best'")

    if args.list_formats:
        list_formats(args.urls[0], args.cookies_browser)
        return 0

    _check_ffmpeg()
    out_dir = Path(args.out)
    print(f"Saving to {out_dir}")
    return download(
        args.urls,
        out_dir=out_dir,
        quality=args.quality,
        audio_only=args.audio,
        subs=args.subs,
        playlist=args.playlist,
        cookies_browser=args.cookies_browser,
        keep_originals=args.keep,
    )
