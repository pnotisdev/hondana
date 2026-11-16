"""Natively (learnnatively.com): community difficulty levels of Japanese books and manga.

Natively has no public API; this uses the search suggestions its own pages ask for, and for a link
pasted by hand, the page title ("ある日突然、ギャルの許嫁ができた | L27").
"""

import html
import re

from . import titles, web

SITE = "https://learnnatively.com"
LINK = re.compile(r"learnnatively\.com(/(?:series|book|item)/[0-9A-Za-z-]+/?)")
PAGE_TITLE = re.compile(r"<title>(.*?)\s*\|\s*L(\d+)\s*</title>", re.DOTALL)


def is_manga(item):
    return str(item.get("mediaType") or "").startswith("manga")


def result(item, score=None):
    rating = item.get("rating") or {}
    level = rating.get("lvl")
    url = item.get("url") or ""
    return {
        "url": SITE + url if url.startswith("/") else url,
        "title": item.get("title") or item.get("englishTitle") or "",
        "level": level if isinstance(level, int) else None,
        "levelText": rating.get("lvlDescriptor") or (f"L{level}" if isinstance(level, int) else ""),
        "provisional": bool(rating.get("temporary")),  # a level from few gradings, still settling
        "series": item.get("itemType") == "book_series",
        "volumes": item.get("numOfItems"),
        "rating": (item.get("reviewData") or {}).get("avgRating"),
        **({"matchScore": round(score, 3)} if score is not None else {}),
    }


def search(title, kind, aliases=()):
    best, best_score = None, 0.0
    for query in titles.clean_title_variants(title):
        items = web.get_json(f"{SITE}/api/autocomplete-api/?q=" + web.quote(query))
        if not isinstance(items, list):
            raise web.LookupFailed("Natively gave an unexpected answer")
        for entry in items:
            item = entry.get(entry.get("widget")) if isinstance(entry, dict) else None
            if not isinstance(item, dict) or item.get("libraryType", "books") != "books" or is_manga(item) != (kind == "manga"):
                continue
            names = [item.get("title"), item.get("englishTitle"), *str(item.get("alternativeTitles") or "").split(",")]
            score = titles.best_similarity([query, *aliases], [name.strip() for name in names if name])
            # A series is the better match for a library item: it covers every volume. Between equally
            # good names the main series wins over a compilation (キノの旅 over its Best Selection).
            if item.get("itemType") == "book_series":
                volumes = item.get("numOfItems") if isinstance(item.get("numOfItems"), int) else 0
                score += 0.01 + 0.001 * min(volumes, 40)
            if score > best_score:
                best, best_score = item, score
        if best_score >= titles.EXACT:
            break  # else a shorter variant may find the main series (キノの旅 for "キノの旅 the Beautiful World")
    return result(best, min(best_score, 1.0)) if best and best_score >= titles.MATCH_THRESHOLD else None


def from_link(link, kind):
    match = LINK.search(link)
    if not match:
        raise ValueError("Paste a learnnatively.com series or book link, such as https://learnnatively.com/series/4a5a9622b3/")
    url = SITE + match.group(1).rstrip("/") + "/"
    page = PAGE_TITLE.search(web.get_text(url))
    if not page:
        raise web.LookupFailed("Could not find a level on that Natively page")
    return {"url": url, "title": html.unescape(page.group(1)).strip(), "level": int(page.group(2)),
            "levelText": f"L{page.group(2)}", "provisional": False, "series": "/series/" in url,
            "volumes": None, "rating": None}


def refresh(previous, kind, title, aliases=()):
    """The level again: Natively has no lookup by id, so a found title is searched for again."""
    if previous.get("matchScore") is None:
        return from_link(previous["url"], kind)
    return search(title, kind, aliases) or previous
