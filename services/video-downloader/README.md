# Video Downloader

Personal command-line video downloader for Windows, built on
[yt-dlp](https://github.com/yt-dlp/yt-dlp). Made for YouTube, but anything
yt-dlp supports works.

For personal/offline use only — don't redistribute what you pull down.

## Setup

Needs Python 3.11+ and ffmpeg on PATH:

```
winget install Gyan.FFmpeg
```

Then, once:

```
setup.cmd
```

## Usage

### Web UI

```
ytdl-ui
```

Opens <http://127.0.0.1:8756> in your browser: paste URLs, pick options, watch
live progress. Every command-line option is on the page. It binds to localhost
only and has no authentication -- `--host 0.0.0.0` exposes it to your network,
so only do that on a network you trust. `-p` changes the port, `-n` skips
opening the browser.

### Command line

```
ytdl https://youtu.be/xxxx              best mp4 up to 1080p
ytdl -a https://youtu.be/xxxx           audio only, as mp3
ytdl -q 720 https://youtu.be/xxxx       cap at 720p
ytdl -q best https://youtu.be/xxxx      no cap (4K if available)
ytdl -s https://youtu.be/xxxx           embed English subtitles
ytdl -p <playlist-url>                  whole playlist
ytdl -o D:\Videos <url>                 different output folder
ytdl -f <url>                           list available formats, download nothing
ytdl -c edge <url>                      use Edge's cookies (age-restricted videos)
ytdl <url1> <url2> <url3>               batch
```

Files land in your Windows Downloads folder as `<title> [<id>].mp4`. The video id in the
name means a re-run skips whatever is already there, and two videos with
the same title never collide. Set `YTDL_OUT` to change the default folder
permanently, or use `-o` per download.

### Running from anywhere

`ytdl.cmd` anchors its own paths, so adding this folder to your PATH lets
you run `ytdl <url>` from any directory:

```powershell
[Environment]::SetEnvironmentVariable(
    "Path", $env:Path + ";D:\video-downloader", "User")
```

## Layout

```
ytdl/downloader.py   yt-dlp option building + the download calls
ytdl/cli.py          argument parsing
ytdl.cmd             entry point (activates the venv)
setup.cmd            one-time install
```

`build_options()` is a pure function, so the option logic can be checked
without touching the network:

```python
from ytdl import build_options
build_options(quality="720")["format"]
```

## Notes

- **"This video is unavailable"** on a video that plainly works in a
  browser usually means no JavaScript runtime was found — YouTube needs one
  to decipher stream URLs. Install Node (`winget install OpenJS.NodeJS`) or
  Deno; the downloader picks up either automatically.
- **Age-restricted / members-only**: pass `-c chrome` (or `edge`, `firefox`)
  to borrow cookies from a browser you're signed into.
- **YouTube changes break yt-dlp regularly.** When downloads start failing
  for no clear reason, update first:
  `venv\Scripts\python -m pip install -U yt-dlp`
