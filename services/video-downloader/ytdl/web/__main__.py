"""`python -m ytdl.web` -- start the local web UI."""

import argparse

from .server import run

parser = argparse.ArgumentParser(prog="ytdl-ui", description="Local web UI for the downloader.")
parser.add_argument("-p", "--port", type=int, default=8756, help="port (default: 8756)")
parser.add_argument("--host", default="127.0.0.1",
                    help="bind address. 0.0.0.0 exposes it to your network -- "
                         "there is no authentication, so only do that on a network you trust.")
parser.add_argument("-n", "--no-browser", action="store_true", help="don't open a browser")
args = parser.parse_args()

raise SystemExit(run(host=args.host, port=args.port, open_browser=not args.no_browser))
