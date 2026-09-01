#!/usr/bin/env bash
#
# Copy the app into the install the user actually runs.
#
# Editing the repo changes nothing on screen: the running app is its own copy
# under D:\Toolbox, and every "why is the UI not updated" has come from that
# gap. This closes it. Pass a different install path as $1.
set -e

REPO="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-/d/Toolbox/resources/app}"
SVC=services/video-downloader

[ -d "$DEST" ] || { echo "no install at $DEST"; exit 1; }
echo "repo    $REPO"
echo "install $DEST"

# Only the Python package and the app shell. The install's own venv, its
# downloads and its projects live alongside and must survive untouched.
rm -rf "$DEST/$SVC/ytdl"
cp -a "$REPO/$SVC/ytdl" "$DEST/$SVC/ytdl"
find "$DEST/$SVC/ytdl" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
cp -a "$REPO/$SVC/requirements.txt" "$DEST/$SVC/" 2>/dev/null || true

for f in main.js preload.js package.json; do
  [ -f "$REPO/$f" ] && cp -a "$REPO/$f" "$DEST/$f"
done
for d in renderer modules; do
  [ -d "$REPO/$d" ] && { rm -rf "$DEST/$d"; cp -a "$REPO/$d" "$DEST/$d"; }
done

echo
echo "copied. what it takes to see each kind of change:"
echo "  .html  -- reload the page (Ctrl+R)"
echo "  .py    -- restart the service"
echo "  main.js / preload.js -- restart Toolbox"
