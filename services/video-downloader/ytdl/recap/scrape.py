"""
Reading the video's own page with Playwright.

A transcript says what was said; the page says how it landed -- the real
title and description, the tags the creator chose, the view and like counts,
and the comments people actually left. Feeding that to the recap writer is
the difference between a summary and a post that knows its audience.

Playwright is optional. Without it the recap still works, just with less
context, so every failure here degrades to an empty dict rather than
stopping the pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path

# Sites render the description behind a "more" button and comments only after
# a scroll, so the scrape has to interact a little rather than read the raw
# HTML. These are the selectors that expose them, newest layout first.
_EXPANDERS = (
    "tp-yt-paper-button#expand",             # YouTube description
    "#expand",
    "ytd-text-inline-expander #expand",
    "div[role='button']:has-text('See more')",
    "div[role='button']:has-text('More')",
)

# Scoped to the primary column, because a page-wide match drags in the sidebar.
# Even scoped, the expanded description block on YouTube carries UI chrome --
# the chapter list, "show all" labels in whatever language the browser asked
# for, related-video chips -- so what comes out here is only kept for reference.
# The description that reaches the prompt comes from yt-dlp, which returns the
# field itself with none of the surrounding furniture.
_DESCRIPTION = (
    "#above-the-fold #description-inline-expander",
    "ytd-watch-metadata #description-inline-expander",
    "ytd-watch-metadata #description",
    "#primary #description",
)

_COMMENTS = (
    "ytd-comment-thread-renderer #content-text",
    "#content-text",
    "div[role='article'] div[dir='auto']",
)


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def install_hint() -> str:
    return (
        "Page scraping needs Playwright. Install it once with:\n"
        r"  venv\Scripts\python.exe -m pip install playwright"
        "\n"
        r"  venv\Scripts\python.exe -m playwright install chromium"
    )


def _clean(text: str, limit: int = 4000) -> str:
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]


def scrape(url: str, screenshot: Path | None = None, timeout: int = 45000) -> dict:
    """
    Open the page and read what is on it.

    Returns {} when Playwright is missing or the page will not load -- the
    caller treats context as a bonus, never a requirement.
    """
    if not available():
        return {"error": install_hint()}

    from playwright.sync_api import sync_playwright

    data: dict = {"url": url}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            )
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(2500)

            data["page_title"] = _clean(page.title(), 300)

            for prop in ("og:title", "keywords"):
                try:
                    value = page.get_attribute(
                        f"meta[property='{prop}'], meta[name='{prop}']", "content"
                    )
                except Exception:      # noqa: BLE001
                    value = None
                if value:
                    data[prop.replace("og:", "og_")] = _clean(value, 1200)

            for selector in _EXPANDERS:
                try:
                    node = page.locator(selector).first
                    if node.is_visible(timeout=800):
                        node.click(timeout=1500)
                        page.wait_for_timeout(600)
                        break
                except Exception:      # noqa: BLE001 - no expander on this layout
                    continue

            for selector in _DESCRIPTION:
                try:
                    if selector.startswith("meta"):
                        text = page.get_attribute(selector, "content")
                    else:
                        text = page.locator(selector).first.inner_text(timeout=1500)
                except Exception:      # noqa: BLE001
                    continue
                if text and len(text.strip()) > 40:
                    data["page_description"] = _clean(text)
                    break

            # counts live in aria-labels far more reliably than in visible text
            body = ""
            try:
                body = page.locator("body").inner_text(timeout=3000)
            except Exception:      # noqa: BLE001
                pass
            counts = re.findall(
                r"([\d.,]+\s*[KMB]?)\s*(views?|likes?|comments?)", body, re.IGNORECASE
            )
            if counts:
                data["engagement"] = [f"{n.strip()} {w.lower()}" for n, w in counts[:6]]

            try:
                page.mouse.wheel(0, 2600)
                page.wait_for_timeout(2200)
            except Exception:      # noqa: BLE001
                pass

            for selector in _COMMENTS:
                try:
                    nodes = page.locator(selector)
                    total = min(nodes.count(), 12)
                except Exception:      # noqa: BLE001
                    continue
                comments = []
                for i in range(total):
                    try:
                        text = _clean(nodes.nth(i).inner_text(timeout=800), 300)
                    except Exception:      # noqa: BLE001
                        continue
                    if len(text) > 15:
                        comments.append(text)
                if comments:
                    data["comments"] = comments[:10]
                    break

            if screenshot:
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(screenshot), full_page=False)
                    data["screenshot"] = screenshot.name
                except Exception:      # noqa: BLE001
                    pass

            browser.close()
    except Exception as exc:      # noqa: BLE001 - context is optional, never fatal
        return {"error": f"Could not read the page: {exc}"}

    return data


def as_prompt_block(data: dict) -> str:
    """Fold whatever was scraped into a block for the recap prompt."""
    if not data or data.get("error"):
        return ""
    bits = []
    if data.get("page_title"):
        bits.append(f"Page title: {data['page_title']}")
    # page_description is captured into the JSON for reference but deliberately
    # kept OUT of the prompt: the scraped block arrives wrapped in chapter
    # lists and localised UI labels. yt-dlp returns the same description
    # cleanly, so that is what the recap writer is given.
    if data.get("keywords"):
        bits.append(f"Tags on the page: {data['keywords']}")
    if data.get("engagement"):
        bits.append("Engagement: " + ", ".join(data["engagement"]))
    if data.get("comments"):
        joined = "\n".join(f"- {c}" for c in data["comments"])
        bits.append(
            "Top comments (what the audience actually reacted to -- use these to "
            f"pick angles and hooks):\n{joined}"
        )
    if not bits:
        return ""
    return "PAGE CONTEXT\n" + "\n\n".join(bits)
