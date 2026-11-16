"""Online information about library titles from Jiten, Natively and jpdb, cached in lookup_cache.json.

Titles are matched by name. When a match is wrong or missing, a link to the right page (or "none")
can be saved for the title; it is kept until removed, and only its details are refreshed.
"""

import json
import os
import tempfile
import threading
import time

from . import PROJECT_DIR, jiten, jpdb, natively, titles, web

SOURCES = {"jiten": jiten, "natively": natively, "jpdb": jpdb}
KINDS = ("manga", "novel")
ENABLED = {name: True for name in SOURCES}
CACHE_PATH = os.path.join(PROJECT_DIR, "lookup_cache.json")
OLD_CACHE_PATH = os.path.join(PROJECT_DIR, "difficulty_cache.json")  # Jiten matches saved by earlier versions
REFRESH_SECONDS = 14 * 86400   # details of a found title
RETRY_SECONDS = 7 * 86400      # a title not found, as the sites add titles
PERSONAL_SECONDS = 86400       # Jiten coverage with an API key changes as you learn
NO_MATCH = "none"              # a saved link meaning "not on this site"
# Bump when title matching changes: titles found by an older matcher are searched for again once.
MATCHER_VERSION = 2

_cache = None
_lock = threading.Lock()
_busy = {}  # cache key -> lock, so parallel requests for one title look it up once


class Unavailable(Exception):
    """The source is turned off, or could not be reached and nothing is saved for the title."""


def configure(settings):
    for name in SOURCES:
        ENABLED[name] = settings[name]
    if jiten.API_KEY != settings["apiKeys"]["jiten"]:
        jiten.API_KEY = settings["apiKeys"]["jiten"]
        with _lock:  # coverage was saved for another account, or for none
            for entry in (_cache or {}).values():
                if entry.get("source") == "jiten":
                    entry["at"] = 0
    jpdb.API_KEY = settings["apiKeys"]["jpdb"]


def cache_key(source, kind, title):
    return f"{source}:{kind}:{titles.normalize_title(title)}"


def load_cache():
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(CACHE_PATH, encoding="utf-8") as file:
            cache = json.load(file)
        if not isinstance(cache, dict):
            raise ValueError("it must contain a JSON object")
    except FileNotFoundError:
        cache = import_old_cache()
    except (OSError, ValueError) as error:
        raise OSError(f"Could not read the lookup cache {CACHE_PATH}: {error}") from error
    _cache = cache
    return cache


def import_old_cache():
    """Jiten matches from difficulty_cache.json, so they are refreshed instead of searched again."""
    try:
        with open(OLD_CACHE_PATH, encoding="utf-8") as file:
            old = json.load(file)
    except (OSError, ValueError):
        return {}
    cache = {}
    for key, value in (old.items() if isinstance(old, dict) else ()):
        kind, _, title = key.partition(":")
        if kind in KINDS and isinstance(value, dict) and isinstance(value.get("deckId"), int):
            cache[cache_key("jiten", kind, title)] = {"source": "jiten", "at": 0, "link": None, "matcher": MATCHER_VERSION,
                                                    "result": {"deckId": value["deckId"], "matchScore": value.get("matchScore", 1)}}
    return cache


def save_cache():
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=os.path.dirname(CACHE_PATH),
                                         prefix=".lookup-", suffix=".tmp", delete=False) as file:
            tmp_path = file.name
            json.dump(_cache, file, ensure_ascii=False, indent=0)
        os.replace(tmp_path, CACHE_PATH)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def stale(entry, source, now):
    if entry is None:
        return True
    if entry.get("link") == NO_MATCH:
        return False
    if entry.get("result") is None:
        return now - entry.get("at", 0) > RETRY_SECONDS
    limit = PERSONAL_SECONDS if source == "jiten" and jiten.API_KEY else REFRESH_SECONDS
    searched_by_old_matcher = not entry.get("link") and entry.get("matcher") != MATCHER_VERSION
    return now - entry.get("at", 0) > limit or "url" not in entry["result"] or searched_by_old_matcher


def answer(source, entry, stale_reason=None):
    return {"source": source, "result": entry.get("result"), "manual": bool(entry.get("link")),
            "checkedAt": entry.get("at"), **({"stale": stale_reason} if stale_reason else {})}


def aliases(source, kind, title):
    """Other names for the title from its Jiten match, already looked up, for sites that use romaji."""
    if source == "jiten" or not ENABLED["jiten"]:
        return ()
    with _lock:
        entry = load_cache().get(cache_key("jiten", kind, title)) or {}
    return tuple((entry.get("result") or {}).get("titles") or ())


def lookup(source, kind, title):
    """{"source", "result" (None if not found), "manual", "checkedAt"} for a title, from the cache when fresh."""
    if source not in SOURCES or kind not in KINDS or not title.strip():
        raise ValueError("Unknown source or media type")
    if not ENABLED[source]:
        raise Unavailable(f"{source} lookups are turned off in Settings")
    key = cache_key(source, kind, title)
    with _lock:
        busy = _busy.setdefault(key, threading.Lock())
    with busy:  # a second request for the same title waits for the first one's answer
        with _lock:
            entry = load_cache().get(key)
        now = time.time()
        if not stale(entry, source, now):
            return answer(source, entry)
        module = SOURCES[source]
        link = (entry or {}).get("link")
        try:
            if link:
                result = module.from_link(link, kind)
            elif entry and entry.get("result") and entry.get("matcher") == MATCHER_VERSION:
                result = module.refresh(entry["result"], kind, title, aliases(source, kind, title))
            else:
                result = module.search(title, kind, aliases(source, kind, title))
        except (web.LookupFailed, ValueError, KeyError, TypeError) as error:
            if entry:
                return answer(source, entry, str(error))  # offline: the saved answer, refreshed next time
            raise Unavailable(str(error)) from error
        store(key, {"source": source, "at": now, "link": link, "result": result, "matcher": MATCHER_VERSION})
        return answer(source, _cache[key])


def store(key, entry):
    with _lock:
        cache = load_cache()
        previous = cache.get(key)
        cache[key] = entry
        try:
            save_cache()
        except OSError:
            if previous is None:
                del cache[key]
            else:
                cache[key] = previous
            raise


def set_link(source, kind, title, link):
    """Save the page a title is on ("" to search again, "none" for not on this site); return lookup()'s answer."""
    if source not in SOURCES or kind not in KINDS or not title.strip():
        raise ValueError("Unknown source or media type")
    key = cache_key(source, kind, title)
    link = link.strip()
    if not link:
        with _lock:
            if load_cache().pop(key, None) is not None:
                save_cache()
        return lookup(source, kind, title)
    if link.lower() == NO_MATCH:
        store(key, {"source": source, "at": time.time(), "link": NO_MATCH, "result": None})
        return answer(source, _cache[key])
    try:
        result = SOURCES[source].from_link(link, kind)
    except web.LookupFailed as error:
        raise Unavailable(str(error)) from error
    store(key, {"source": source, "at": time.time(), "link": link, "result": result})
    return answer(source, _cache[key])
