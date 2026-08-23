# Toolbox

One Electron app with a dashboard that hosts several personal tools. Built to grow:
adding a tool is a folder plus a manifest entry, not a code change.

Run it with **`run.cmd`** (or `npm start`). `run.cmd` clears `ELECTRON_RUN_AS_NODE`,
which some terminals set and which would otherwise boot Electron in plain Node mode.

## What's in it

| Tool | Kind | Notes |
|---|---|---|
| **Screen Recorder** | in-app page | Full screen / region / window / webcam / audio-only / screenshot |
| **Video Downloader** | local service | Wraps the existing yt-dlp project at `D:\video-downloader` |

The dashboard shows tool cards, recent recordings and recent downloads, with
service status dots in the sidebar.

### Screen Recorder

- Mic + system sound mixed live; either, both, or neither
- Webcam picture-in-picture overlay — four corners, three sizes, rounded and mirrored
- Global hotkeys: **F9** start/stop, **F10** pause, **F11** screenshot (rebindable)
- Floating always-on-top control bar while recording (drag, pause, stop, timer)
- Countdown (off/3/5/10s), auto-stop timer, 15–60 fps, four quality presets
- Output MP4 (H.264 via ffmpeg), WebM (no re-encode) or animated GIF
- Library tab with thumbnails, play, reveal, delete
- Saves to `%USERPROFILE%\Videos\ScreenRecorder` by default

### Video Downloader

Starts `python -m ytdl.web` from the venv in `D:\video-downloader` on a random free
localhost port, waits for `/api/config`, then loads its UI in the module frame. The
process is killed when Toolbox quits. All download logic stays in the Python package —
run `setup.cmd` there once if the venv is missing.

## Adding another tool

1. Create `modules\<your-tool>\index.html` (link `../../renderer/base.css` for the shared look).
2. Add an entry to `modules\manifest.json`:

```json
{
  "id": "notes",
  "name": "Notes",
  "tagline": "Quick scratchpad",
  "icon": "\u270E",
  "accent": "#3ddc97",
  "type": "page",
  "entry": "modules/notes/index.html",
  "enabled": true
}
```

3. Restart. It appears in the sidebar and on the dashboard.

For a tool that is really a local server (Python, Node, anything), use
`"type": "service-web"` with a `"service"` id and add that id under `services` in the
manifest — `projectRoot`, the interpreter, the args (`{port}` is substituted) and a
`healthPath` to poll. The shell shows a spinner while it boots and a readable error
box with the process log if it doesn't.

Module pages get the same `window.api` bridge as the shell (preload runs in subframes),
so a module can use settings, file, capture and hotkey APIs without its own IPC.

## Layout

```
main.js                     window, module registry, service manager, capture/ffmpeg IPC
preload.js                  window.api bridge, relays broadcasts into module frames
renderer/shell.html|css|js  dashboard, sidebar, module host frame
renderer/base.css           shared design tokens and components
renderer/overlay.*          dimmed drag-to-select region picker
renderer/panel.*            floating recording control bar
modules/manifest.json       the tool registry
modules/screen-recorder/    the recorder module
```

## Build an installer

```
npm run dist
```

Requires ffmpeg on `PATH` (already installed here) or `FFMPEG_PATH` set.

---

## Recap Studio

Turns one video link into everything a post needs: a recap cut, a narration
script aligned across the whole original, a description, hashtags, a thumbnail,
subtitles, and spoken narration laid over the footage.

Everything lands in one folder per project under `Downloads/RecapStudio/`, so
the folder itself is the deliverable.

### The steps

| Step | What it does |
| --- | --- |
| Download | yt-dlp fetches the source video and its real metadata |
| Transcript | platform captions when they exist, local Whisper when they do not |
| Script | Gemini writes the beats, title, description, hashtags, in English and Burmese |
| Recap cut | ffmpeg splices the chosen moments; a slider runs from the full original down to a few seconds |
| Frames | candidate thumbnails, ranked by the script's own scores |
| Voice | narration spoken over the cut, with the original audio held underneath |

Each step reruns on its own, everything is editable by hand, and anything
running can be stopped.

### Setup

```
cd services/video-downloader
setup.cmd                     # creates the venv
```

Optional extras, installed into that venv when you want them:

```
pip install faster-whisper    # videos with no captions
pip install playwright && playwright install chromium   # page context
pip install voxcpm            # local narration, no API quota
```

A free Gemini API key from https://aistudio.google.com/apikey goes in
Settings. It is stored locally and is deliberately not committed.
