"""Jiten (jiten.moe): difficulty, word statistics, summaries and per-volume decks of manga and novels.

With an API key (jiten.moe → Settings → Advanced → API Key) Jiten also reports how much of each title
you know, from the words you have marked or synced on Jiten.
"""

import re

from . import titles, web

API = "https://api.jiten.moe"
SITE = "https://jiten.moe"
API_KEY = ""
# Jiten media-deck types: books in the EPUB library are looked up as novels.
MEDIA_TYPES = {"manga": 9, "novel": 4}
VOLUME_TITLE = re.compile(r"(?:vol(?:ume)?\.?|第)\s*(\d+(?:\.\d+)?)|^\s*(\d+(?:\.\d+)?)\s*(?:巻)?\s*$", re.IGNORECASE)
LINK = re.compile(r"jiten\.moe/decks/media/(\d+)")
MAX_VOLUME_PAGES = 8  # 25 volumes a page


def get(path):
    headers = {"X-Api-Key": API_KEY} if API_KEY else {}
    result = web.get_json(API + path, headers)
    if not isinstance(result, dict):
        raise web.LookupFailed("Jiten gave an unexpected answer")
    return result


def search(title, kind, aliases=()):
    """The best-matching deck of this media type as a result (see fetch), or None."""
    best, best_score = None, 0.0
    for query in titles.clean_title_variants(title):
        data = get("/api/media-deck/search-suggestions?query=" + web.quote(query))
        for result in data.get("suggestions") or []:
            if result.get("mediaType") != MEDIA_TYPES[kind] or not isinstance(result.get("deckId"), int):
                continue
            names = (result.get("romajiTitle"), result.get("englishTitle"), result.get("originalTitle"))
            score = titles.best_similarity([query, *aliases], names)
            if score > best_score:
                best, best_score = result, score
        if best_score >= titles.EXACT:
            break  # else a cleaner variant of the name may match better
    if not best or best_score < titles.MATCH_THRESHOLD:
        return None
    return {**fetch(best["deckId"]), "matchScore": round(best_score, 3)}


def deck_id_from_link(link):
    link = link.strip()
    match = LINK.search(link) or re.fullmatch(r"(\d+)", link)  # a link, or the deck number alone
    if not match:
        raise ValueError("Paste a jiten.moe title link, such as https://jiten.moe/decks/media/12345/detail")
    return int(match.group(1))


def from_link(link, kind):
    return fetch(deck_id_from_link(link))


def refresh(result, kind, title=None, aliases=()):
    return {**fetch(result["deckId"]), **({"matchScore": result["matchScore"]} if "matchScore" in result else {})}


def volume_number(title):
    match = VOLUME_TITLE.search(title or "")
    return float(match.group(1) or match.group(2)) if match else None


def coverage(deck):
    """The viewer's coverage of a deck in percent, when Jiten knows who is asking."""
    value = deck.get("coverage")
    return round(value, 1) if API_KEY and isinstance(value, (int, float)) else None


def difficulty(deck):
    value = deck.get("difficultyRaw", deck.get("difficulty"))
    return round(value, 2) if isinstance(value, (int, float)) and value >= 0 else None


def fetch(deck_id):
    """Difficulty (0-5), statistics, summary and volumes of a Jiten deck."""
    data = get(f"/api/media-deck/{deck_id}/detail?offset=0").get("data") or {}
    deck = data.get("mainDeck")
    if not deck:
        raise web.LookupFailed(f"Jiten has no deck {deck_id}")
    subdecks, total = list(data.get("subDecks") or []), deck.get("childrenDeckCount") or 0
    offset = len(subdecks)
    while subdecks and offset < min(total, 25 * MAX_VOLUME_PAGES):
        page = (get(f"/api/media-deck/{deck_id}/detail?offset={offset}").get("data") or {}).get("subDecks") or []
        if not page:
            break
        subdecks += page
        offset += len(page)
    volumes = []
    for sub in subdecks:
        number = volume_number(sub.get("originalTitle"))
        if number is not None and isinstance(sub.get("deckId"), int):
            volumes.append({"number": number, "deckId": sub["deckId"], "difficulty": difficulty(sub),
                            "characters": sub.get("characterCount"), "coverage": coverage(sub)})
    try:
        milestones = get(f"/api/media-deck/{deck_id}/stats").get("milestones") or {}
    except web.LookupFailed:
        milestones = {}  # a deck without computed statistics still has its difficulty
    names = [deck.get(key) for key in ("originalTitle", "romajiTitle", "englishTitle")]
    return {
        "deckId": deck_id,
        "url": f"{SITE}/decks/media/{deck_id}/detail",
        "title": deck.get("originalTitle") or deck.get("romajiTitle") or deck.get("englishTitle"),
        "titles": [name for name in names if name],
        "difficulty": difficulty(deck),
        "description": deck.get("description") or "",
        "characters": deck.get("characterCount"),
        "uniqueWords": deck.get("uniqueWordCount"),
        "uniqueKanji": deck.get("uniqueKanjiCount"),
        # How many of the title's most frequent words give this share of its text.
        "wordsFor": {share: milestones[share] for share in ("90%", "95%", "98%") if isinstance(milestones.get(share), int)},
        "coverage": coverage(deck),
        "uniqueCoverage": round(deck["uniqueCoverage"], 1) if API_KEY and isinstance(deck.get("uniqueCoverage"), (int, float)) else None,
        "volumes": sorted(volumes, key=lambda volume: volume["number"]),
    }
