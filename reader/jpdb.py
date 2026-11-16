"""jpdb.io: difficulty of novels from its difficulty list, and with an API key, the words in your jpdb decks.

jpdb has no manga. The difficulty list is read from the page its search form opens; the API is
documented on jpdb.io's settings page, where the key is too.
"""

import html
import re

from . import titles, web

SITE = "https://jpdb.io"
API = SITE + "/api/v1"
API_KEY = ""
LINK = re.compile(r"jpdb\.io(/(?:novel|web-novel|visual-novel)/\d+(?:/[^?#\s]*)?)")
ENTRY_LINK = re.compile(r'href="(/(?:novel|web-novel|visual-novel)/\d+/[^"/]*)" class="outline"')
ROW = re.compile(r"<th>([^<]*)</th>\s*<td>([^<]*)</td>")
HEADING = re.compile(r"<h[1-5][^>]*>(.*?)</h[1-5]>", re.DOTALL)
FIELDS = {"Average difficulty": "difficulty", "Peak difficulty (90th percentile)": "peak",
          "Unique words": "uniqueWords", "Unique kanji": "uniqueKanji", "Characters": "characters",
          "Volumes": "volumes", "Length (in words)": "words"}


def number(text):
    match = re.match(r"\s*(\d+)", text.replace(",", ""))
    return int(match.group(1)) if match else None


def stats(chunk):
    found = {}
    for name, value in ROW.findall(chunk):
        key = FIELDS.get(html.unescape(name).strip())
        if key and key not in found:
            found[key] = number(html.unescape(value))
    return found


def entries(page):
    """(title, url, statistics) of each novel on a difficulty-list page."""
    chunks = page.split("<h5")[1:]
    for chunk in chunks:
        title = html.unescape(re.sub(r"<[^>]*>", "", chunk.split(">", 1)[-1].split("</h5>", 1)[0])).strip()
        link = ENTRY_LINK.search(chunk)
        if title and link:
            yield title, SITE + link.group(1), stats(chunk)


def result(title, url, found, score=None):
    return {"url": url, "title": title, **{key: found.get(key) for key in set(FIELDS.values())},
            **({"matchScore": round(score, 3)} if score is not None else {})}


def search(title, kind, aliases=()):
    if kind != "novel":
        return None  # jpdb has no manga
    for query in titles.clean_title_variants(title):
        found = list(entries(web.get_text(f"{SITE}/novel-difficulty-list?q=" + web.quote(query))))
        if not found:
            continue
        # jpdb names novels in romaji, so a Japanese title is compared with the other sites' names for it.
        scored = [(titles.best_similarity([query, *aliases], [name]), name, url, data) for name, url, data in found]
        score, name, url, data = max(scored, key=lambda item: item[0])
        if score >= titles.MATCH_THRESHOLD:
            return result(name, url, data, score)
        # Its search reads Japanese titles too: one result for a Japanese title of some length is the one.
        if len(found) == 1 and titles.JAPANESE.search(query) and len(query) >= 4:
            return result(name, url, data, titles.MATCH_THRESHOLD)
    return None


def from_link(link, kind):
    match = LINK.search(link)
    if not match:
        raise ValueError("Paste a jpdb.io novel link, such as https://jpdb.io/novel/7176/kino-no-tabi-the-beautiful-world-")
    url = SITE + match.group(1)
    page = web.get_text(url)
    main = page.split("Other novels of similar difficulty", 1)[0]
    heading = HEADING.search(main)
    data = stats(main)
    if not heading or data.get("difficulty") is None:
        raise web.LookupFailed("Could not find a difficulty on that jpdb page")
    return result(html.unescape(re.sub(r"<[^>]*>", "", heading.group(1))).strip(), url, data)


def refresh(previous, kind, title=None, aliases=()):
    return {**from_link(previous["url"], kind),
            **({"matchScore": previous["matchScore"]} if "matchScore" in previous else {})}


# ---------- Your jpdb decks (API key) ----------

def api(endpoint, **params):
    if not API_KEY:
        raise web.LookupFailed("No jpdb API key")
    reply = web.get_json(f"{API}/{endpoint}", {"Authorization": "Bearer " + API_KEY}, params)
    if not isinstance(reply, dict) or reply.get("error"):
        raise web.LookupFailed(f"jpdb: {reply.get('error_message') or reply.get('error') if isinstance(reply, dict) else reply}")
    return reply


# jpdb card states, worst first within each group; a word takes its best card's.
KNOWN_STATES = {"known", "never-forget", "blacklisted", "redundant"}
LEARNING_STATES = {"learning", "due", "failed", "suspended"}
NEW_STATES = {"new", "locked"}


def word_status(states):
    states = set(states or ())
    if states & KNOWN_STATES:
        return "known"
    if states & LEARNING_STATES:
        return "learning"
    if states & NEW_STATES:
        return "new"
    return None


def deck_words():
    """{"known": set, "learning": set, "new": set} of spellings in your jpdb decks, and the special decks."""
    decks = [deck[0] for deck in api("deck/list-user", fields=["id"]).get("decks") or []]
    vocabulary = set()
    for deck in [*decks, "never-forget", "blacklist"]:
        try:
            listed = api("deck/list-vocabulary", id=deck, fetch_occurences=False).get("vocabulary") or []
        except web.LookupFailed:
            if deck in ("never-forget", "blacklist"):
                continue  # special decks the account may not have
            raise
        vocabulary.update(tuple(pair) for pair in listed if isinstance(pair, list) and len(pair) == 2)
    result = {"known": set(), "learning": set(), "new": set()}
    pairs = sorted(vocabulary)
    for start in range(0, len(pairs), 5000):
        info = api("lookup-vocabulary", list=[list(pair) for pair in pairs[start:start + 5000]],
                   fields=["spelling", "reading", "card_state"]).get("vocabulary_info") or []
        for entry in info:
            if not isinstance(entry, list) or len(entry) != 3:
                continue
            spelling, _, states = entry
            status = word_status(states)
            if status and isinstance(spelling, str):
                result[status].add(spelling)
    return result


def ping():
    api("ping")
