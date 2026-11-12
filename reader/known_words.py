"""Known-word coverage of manga volumes: Mokuro OCR text against the words reviewed in Anki."""

import hashlib
import html
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request

from . import PROJECT_DIR, config, furigana

STATUSES = ("new", "learning", "known")  # worst first; a word takes the best status of its cards


def configure(anki):
    """Use the "anki" settings from config.load(); config.DEFAULTS says what each one means."""
    global ANKI_URL, ANKI_SEARCH, KNOWN_INTERVAL, WORD_FIELDS, WORD_LIST_FIELDS, WORD_CARDS_ONLY
    ANKI_URL, ANKI_SEARCH, KNOWN_INTERVAL = anki["url"], anki["search"], anki["knownInterval"]
    WORD_FIELDS = dict(anki["wordFields"])
    WORD_LIST_FIELDS, WORD_CARDS_ONLY = set(anki["wordListFields"]), set(anki["wordCardsOnly"])


configure(config.DEFAULTS["anki"])


def notes_query(models=None):
    """Anki search for notes of the configured note types (or of models) within the configured search."""
    names = WORD_FIELDS if models is None else models
    return f"{ANKI_SEARCH} (" + " OR ".join(f'"note:{name}"' for name in names) + ")"


def anki_queries():
    notes = notes_query()
    return {
        # Suspended cards count by how far they got: most are duplicates, but some are a word's only card.
        "known": f"{notes} -is:new prop:ivl>={KNOWN_INTERVAL}",
        "learning": f"{notes} -is:new prop:ivl<{KNOWN_INTERVAL}",
        # Waiting cards that will come up; a waiting sentence card doesn't make each of its words a card.
        "new": f"{notes} is:new -is:suspended " + " ".join(
            f'-"note:{name}"' for name, field in WORD_FIELDS.items() if field in WORD_LIST_FIELDS),
    }
ANKI_REFRESH_SECONDS = 600
ANKI_RETRY_SECONDS = 30  # while Anki is offline
READ_ONLY_ACTIONS = {"findNotes", "notesInfo", "findCards", "cardsInfo"}  # nothing here writes to Anki
CACHE_DIR = os.path.join(PROJECT_DIR, "known_words_cache")
CACHE_VERSION = 1  # bump when tokenizing changes, so cached volumes are redone
MERGE_LIMIT = 6  # tokens tried as one word when a card spells it that way (よろしくお願いします)

SKIPPED_POS = {"補助記号", "記号", "空白"}
ALWAYS_KNOWN_POS = {"助詞", "助動詞"}  # particles and auxiliaries
JAPANESE = re.compile(r"[぀-ヿ㐀-鿿豈-﫿々〆]")
KANA_ONLY = re.compile(r"[぀-ヿ]+")


class AnkiOffline(Exception):
    pass


def anki(action, **params):
    if action not in READ_ONLY_ACTIONS:
        raise ValueError(f"AnkiConnect action {action!r} is not allowed")
    body = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
    request = urllib.request.Request(ANKI_URL, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            reply = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as error:  # URLError and timeouts are OSErrors; bad JSON is a ValueError
        raise AnkiOffline(str(error)) from error
    if not isinstance(reply, dict) or reply.get("error"):
        raise AnkiOffline(f"AnkiConnect error: {reply.get('error') if isinstance(reply, dict) else reply}")
    return reply.get("result")


def clean_word(value):
    """'<b>家賃</b>[2]', ' 持[も]つ' and '（カレンダーが）掛かる' become 家賃, 持つ and 掛かる."""
    value = html.unescape(re.sub(r"<[^>]*>", "", value))
    value = re.sub(r"\[[^\]]*\]|[(（][^)）]*[)）]", "", value)
    return re.sub(r"\s+", "", value)


def note_words(note):
    model = note.get("modelName")
    field = WORD_FIELDS.get(model)
    value = ((note.get("fields") or {}).get(field) or {}).get("value", "")
    value = html.unescape(re.sub(r"<[^>]*>", "", value)).strip()
    if field in WORD_LIST_FIELDS:
        parts = value.split()
    elif model in WORD_CARDS_ONLY and re.search(r"\s", value):
        return []
    else:
        parts = [value]
    return [word for word in map(clean_word, parts) if word]


def fetch_anki_words():
    """{"known": [...], "learning": [...], "new": [...], "at": time}; each word is in its best list only."""
    result, better, queries = {"at": time.time()}, set(), anki_queries()
    for status in reversed(STATUSES):
        words = set()
        ids = anki("findNotes", query=queries[status])
        for start in range(0, len(ids), 1000):  # notesInfo is about three times faster than cardsInfo
            for note in anki("notesInfo", notes=ids[start:start + 1000]):
                words.update(note_words(note))
        result[status] = sorted(words - better)
        better |= words
    return result


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=os.path.dirname(path),
                                         suffix=".tmp", delete=False) as file:
            tmp_path = file.name
            json.dump(data, file, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return None  # missing or damaged cache: rebuilt from the source


def word_sets(saved, offline):
    words = {status: set(saved.get(status, ())) for status in STATUSES}  # lists saved before "new" existed lack it
    result = {**words, "knownCount": len(saved["known"]), "at": saved["at"], "offline": offline}
    try:
        for group in words.values():
            # A card and the text may spell a word differently (いい and 良い, わかる and 分かる):
            # the dictionary form both share is added, so either spelling matches.
            for word in list(group):
                keys = list(counted_words(word))
                if len(keys) == 1:
                    group.add(keys[0][0])
    except furigana.FuriganaUnavailable:
        pass  # nothing can be counted without the tokenizer anyway
    return result


_anki_lock = threading.Lock()
_anki_state = {"words": None, "checked": 0.0}


def refresh_anki_words():
    words = _anki_state["words"]
    try:
        saved = fetch_anki_words()
    except AnkiOffline:
        words = words and {**words, "offline": True}
    else:
        try:
            write_json(os.path.join(CACHE_DIR, "anki-words.json"), saved)
        except OSError:
            pass  # the list still works until the server stops
        words = word_sets(saved, offline=False)
    _anki_state["words"] = words
    return words


def forget_anki_words():
    """Fetch the word list again on next use, as after changing the AnkiConnect settings."""
    with _anki_lock:
        _anki_state.update(words=None, checked=0.0)


def anki_words():
    """Words from Anki by status ("known", "learning", "new"), refreshed every few minutes.

    While Anki is not running, the last saved list is returned marked offline, or None if there is none.
    """
    with _anki_lock:
        words = _anki_state["words"]
        wait = ANKI_REFRESH_SECONDS if words and not words["offline"] else ANKI_RETRY_SECONDS
        if time.time() - _anki_state["checked"] < wait:
            return words
        _anki_state["checked"] = time.time()
        if words is None:
            try:
                # After a restart the list saved by the last run shows at once.
                words = word_sets(read_json(os.path.join(CACHE_DIR, "anki-words.json")), offline=False)
                anki("findNotes", query="nid:1")  # a quick question, only to learn whether Anki is running
            except AnkiOffline:
                words["offline"] = True
            except (AttributeError, TypeError, KeyError):
                return refresh_anki_words()  # no usable saved list: wait for Anki
            _anki_state["words"] = words
            if words["offline"]:
                return words
    # Reading every note takes seconds, so a list already in hand is refreshed behind this request.
    threading.Thread(target=refresh_anki_words, daemon=True).start()
    return words


def word_key(word):
    """(lemma, base form, reading, always known) of a tagger word; None for punctuation, numbers and other scripts."""
    feature, surface = word.feature, word.surface
    if feature.pos1 in SKIPPED_POS or feature.pos2 == "数詞" or not JAPANESE.search(surface):
        return None
    lemma = (feature.lemma or surface).split("-")[0]  # UniDic writes loanwords as "テレビ-television"
    return (lemma, feature.orthBase or surface, furigana._hiragana(feature.lForm or surface),
            feature.pos1 in ALWAYS_KNOWN_POS)


def counted_words(text):
    """(lemma, base form, reading, always known, UniDic feature) for each counted word in text."""
    for word in furigana._tagger()(text):
        key = word_key(word)
        if key:
            yield (*key, word.feature)


def word_types(text):
    """Counted words in text as {(lemma, base form, reading, always known): count}."""
    types = {}
    for *key, _ in counted_words(text):
        types[tuple(key)] = types.get(tuple(key), 0) + 1
    return types


def bubbles(path):
    """The text of each speech bubble in a .mokuro volume."""
    with open(path, encoding="utf-8") as file:
        volume = json.load(file)
    for page in volume.get("pages", []):
        for block in page.get("blocks", []):
            # One bubble at a time, so a word split across OCR lines is still one word.
            yield "".join(block.get("lines", []))


def volume_types(path):
    """Word types of a .mokuro volume, cached in CACHE_DIR until the file changes."""
    stat = os.stat(path)
    cache_path = os.path.join(CACHE_DIR, hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest() + ".json")
    stamp = [CACHE_VERSION, stat.st_mtime_ns, stat.st_size]
    cached = read_json(cache_path)
    if isinstance(cached, dict) and cached.get("stamp") == stamp:
        return [tuple(entry) for entry in cached["types"]]
    types = {}
    for text in bubbles(path):
        for key, count in word_types(text).items():
            types[key] = types.get(key, 0) + count
    result = [(*key, count) for key, count in types.items()]
    try:
        write_json(cache_path, {"stamp": stamp, "types": result})
    except OSError:
        pass  # recounted next time
    return result


def word_state(words):
    """A function giving "known", "learning", "new" or None for (lemma, base form, reading, always known)."""
    groups = [(status, words[status], {furigana._hiragana(w) for w in words[status] if KANA_ONLY.fullmatch(w)})
              for status in reversed(STATUSES)]

    def state(lemma, base, reading, always_known):
        if always_known:
            return "known"
        # Readings only match words written in kana (コレ for これ): 都 is not known because と is.
        kana = KANA_ONLY.fullmatch(base)
        for status, group, group_kana in groups:
            if lemma in group or base in group or (kana and reading in group_kana):
                return status
        return None
    return state


def annotator(words):
    """A function splitting text into pieces: [text] or, for a counted word, [text, status, dictionary form].

    status is 0 for a word not in Anki, else 1 + its index in STATUSES. Particles, auxiliaries, names
    that have no card, numbers and punctuation are not counted.
    """
    state = word_state(words)
    cards = {word: status for status in STATUSES for word in words[status]}  # the best status is set last

    def annotate(text):
        tokens, cursor = [], 0
        for word in furigana._tagger()(text):
            start = text.find(word.surface, cursor)
            if start < 0:
                raise ValueError("Tokenizer returned text that does not match the input")
            cursor = start + len(word.surface)
            tokens.append((start, cursor, word))
        pieces, cursor, i = [], 0, 0

        def add(end, *word):
            nonlocal cursor
            if word or not pieces or len(pieces[-1]) > 1:
                pieces.append([text[cursor:end], *word])
            else:
                pieces[-1][0] += text[cursor:end]  # one piece per stretch that isn't counted
            cursor = end

        while i < len(tokens):
            start, end, word = tokens[i]
            if start > cursor:
                add(start)
            # A card for a longer word wins: UniDic splits 自転車 into 自転 and 車, and 気がついた into four.
            for j in range(min(len(tokens), i + MERGE_LIMIT) - 1, i, -1):
                stem, last = text[start:tokens[j][0]], tokens[j][2]
                form = next((f for f in (stem + last.surface, stem + (last.feature.orthBase or "")) if f in cards), None)
                if form:
                    add(tokens[j][1], 1 + STATUSES.index(cards[form]), form)
                    i = j
                    break
            else:
                key = word_key(word)
                status = state(*key) if key and not key[3] else None
                if key is None or key[3] or (status is None and word.feature.pos2 == "固有名詞"):
                    add(end)
                else:
                    add(end, 1 + STATUSES.index(status) if status else 0, key[0])
            i += 1
        if cursor < len(text):
            add(len(text))
        return pieces
    return annotate


def count_known(types, words):
    """Counts of running and unique words, and how many of them are known or still being learned."""
    word_status = word_state(words)
    result = {"words": 0, "known": 0, "learning": 0, "unique": 0, "uniqueKnown": 0}
    unique = {}
    for lemma, base, reading, always_known, count in types:
        state = word_status(lemma, base, reading, always_known)
        result["words"] += count
        if state in result:
            result[state] += count
        # A word written in kana in one place and in kanji in another is still one word.
        if unique.get((lemma, reading)) != "known":
            unique[(lemma, reading)] = state
    result["unique"] = len(unique)
    result["uniqueKnown"] = sum(state == "known" for state in unique.values())
    return result
