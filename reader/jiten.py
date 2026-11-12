"""Jiten difficulty and summary lookup for manga and novel titles, cached in difficulty_cache.json."""

import json
import os
import re
import tempfile
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import PROJECT_DIR

DIFFICULTY_CACHE_PATH = os.path.join(PROJECT_DIR, "difficulty_cache.json")
JITEN_API = "https://api.jiten.moe"
# Jiten media-deck types: books in the EPUB library are looked up as novels.
JITEN_MEDIA_TYPES = {"manga": 9, "novel": 4}
MATCH_THRESHOLD = 0.72



def normalize_title(value):
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = re.sub(r"[‘’`´]", "'", value)
    value = re.sub(r"[“”]", '"', value)
    value = re.sub(r"[「」『』]", " ", value)
    value = re.sub(r"[()\[\]{}:;,.!?/\\|]", " ", value)
    value = re.sub(r"[\-‐‑‒–—―]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def title_tokens(value):
    return [t for t in normalize_title(value).split(" ") if len(t) > 1]


def collapse_long_vowels(value):
    # "ou"->"o", "uu"->"u" — the two unambiguous long-vowel romanizations
    # (お段+う, う段+う). Deliberately not touching "ei"->"e": that pattern
    # is far more often a real distinct sequence (e.g. "keitai") than a
    # long-vowel marker, so collapsing it risks false positive matches.
    return re.sub(r"uu", "u", re.sub(r"ou", "o", value))


def title_similarity(a, b):
    aa, bb = normalize_title(a), normalize_title(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if len(aa) >= 5 and (bb in aa or aa in bb):
        return 0.92
    # Catches cases like folder name "Yotsubato" vs Jiten's "Yotsuba to!" —
    # same title, just missing/extra whitespace between words.
    sa, sb = aa.replace(" ", ""), bb.replace(" ", "")
    if len(sa) >= 5 and sa == sb:
        return 0.9
    # Catches romanization variance like "Kyoko" vs the official "Kyouko".
    ca, cb = collapse_long_vowels(sa), collapse_long_vowels(sb)
    if len(ca) >= 5 and ca == cb:
        return 0.85
    at, bt = set(title_tokens(aa)), set(title_tokens(bb))
    common = len(at & bt)
    union = len(at | bt)
    return common / union if union else 0.0


def jiten_candidate_titles(result):
    return [
        v for v in (
            result.get("romajiTitle"),
            result.get("englishTitle"),
            result.get("originalTitle"),
        ) if v
    ]


def best_jiten_similarity(query, result):
    return max((title_similarity(query, t) for t in jiten_candidate_titles(result)), default=0.0)


# Common scanlation/reprint edition markers that get appended to a title
# in local file naming (e.g. "ドメスティックな彼女　よりぬきカラー版"). Jiten's
# search does near-exact prefix matching, so a query carrying one of these
# returns nothing even though the base title is indexed — strip them
# before searching. Longest-first so "よりぬきカラー版" doesn't get partially
# matched by "カラー版" and leave a dangling "よりぬき".
EDITION_SUFFIXES = sorted(
    ["よりぬきカラー版", "カラー版", "完全版", "新装版", "愛蔵版", "総集編", "決定版"],
    key=len, reverse=True
)


def strip_edition_suffix(name):
    for suffix in EDITION_SUFFIXES:
        new = re.sub(r"[\s　]+" + re.escape(suffix) + r"\s*$", "", name)
        if new != name:
            return new.strip()
    return name


def clean_title_variants(name):
    """
    Local library folder names are often decorated with release-group tags,
    volume-range suffixes, and edition markers (e.g. "Group.Name_Saike
    Mata Shitemo vol 01-15") that hurt fuzzy matching against Jiten's clean
    titles. Best effort only — try the raw name plus a few stripped
    variants, in order from least to most aggressively cleaned.
    """
    variants = [name]

    no_vol = re.sub(r"\s*vol\.?\s*\d+\s*-\s*\d+\s*$", "", name, flags=re.IGNORECASE).strip()
    if no_vol and no_vol not in variants:
        variants.append(no_vol)

    # EPUB titles often end in a publisher label or bonus note, e.g.
    # "三日間の幸福 (メディアワークス文庫)" or "五人一役でも君が好き【電子特典付き】".
    no_label = re.sub(r"(?:[\s　]*(?:[(（【<＜［\[][^)）】>＞］\]]*[)）】>＞］\]]|電子特典付き))+[\s　]*$", "",
                      no_vol or name).strip()
    if no_label and no_label not in variants:
        variants.append(no_label)

    no_edition = strip_edition_suffix(no_label or no_vol or name)
    if no_edition and no_edition not in variants:
        variants.append(no_edition)

    m = re.match(r"^[A-Za-z0-9.]+[_.]([A-Za-z].*)$", no_vol or name)
    if m:
        candidate = m.group(1).strip()
        if candidate and candidate not in variants:
            variants.append(candidate)

    return variants


class JitenLookupError(Exception):
    pass


def http_get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JitenLookupError(str(error)) from error


def jiten_search(query, kind="manga"):
    url = f"{JITEN_API}/api/media-deck/search-suggestions?query={urllib.parse.quote(query)}"
    data = http_get_json(url)
    return [r for r in data.get("suggestions", []) if r.get("mediaType") == JITEN_MEDIA_TYPES[kind]]


def jiten_detail(deck_id):
    url = f"{JITEN_API}/api/media-deck/{deck_id}/detail?offset=0"
    data = http_get_json(url)
    return (data.get("data") or {}).get("mainDeck")


def deck_fields(deck_id):
    detail = (jiten_detail(deck_id) if deck_id else None) or {}
    return {key: detail.get(key) for key in ("difficulty", "difficultyRaw", "description")}


def is_complete(entry):
    # Entries saved before summaries were stored have no "description"; those are refreshed once.
    return entry is None or "description" in entry


_difficulty_cache = None
_difficulty_lock = threading.Lock()


def load_difficulty_cache():
    global _difficulty_cache
    if _difficulty_cache is not None:
        return _difficulty_cache
    try:
        with open(DIFFICULTY_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if not isinstance(cache, dict):
            raise OSError("difficulty cache must contain a JSON object")
        _difficulty_cache = cache
    except FileNotFoundError:
        _difficulty_cache = {}
    except (OSError, json.JSONDecodeError) as error:
        raise OSError(f"Could not read difficulty cache: {error}") from error
    return _difficulty_cache


def save_difficulty_cache():
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(DIFFICULTY_CACHE_PATH),
                                         prefix=".difficulty-", suffix=".tmp", delete=False) as file:
            tmp_path = file.name
            json.dump(_difficulty_cache, file, ensure_ascii=False, indent=0)
        os.replace(tmp_path, DIFFICULTY_CACHE_PATH)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def get_difficulty(title, kind="manga"):
    key = f"{kind}:{normalize_title(title)}"
    with _difficulty_lock:
        cache = load_difficulty_cache()
        cached = cache.get(key)
        if key in cache and is_complete(cached):
            return cached

    if cached and cached.get("deckId"):
        try:
            final = {**cached, **deck_fields(cached["deckId"])}
        except JitenLookupError:
            return cached  # offline: keep showing the saved difficulty and refresh it next time
    else:
        best = None
        best_score = 0.0
        for query in clean_title_variants(title):
            for result in jiten_search(query, kind):
                score = best_jiten_similarity(query, result)
                if score > best_score:
                    best_score = score
                    best = result
            if best_score >= MATCH_THRESHOLD:
                break

        final = None
        if best and best_score >= MATCH_THRESHOLD:
            final = {
                "deckId": best.get("deckId"),
                "matchedTitle": best.get("romajiTitle") or best.get("englishTitle") or best.get("originalTitle"),
                "matchScore": round(best_score, 3),
                **deck_fields(best.get("deckId")),
            }

    with _difficulty_lock:
        if key in cache and is_complete(cache[key]):
            return cache[key]
        previous = cache.get(key)
        cache[key] = final
        try:
            save_difficulty_cache()
        except OSError:
            if previous is None:
                del cache[key]
            else:
                cache[key] = previous
            raise
    return final
