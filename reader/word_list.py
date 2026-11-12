"""The most common words in the manga library that Anki doesn't know yet, written to a CSV file.

Run from the project folder:  py -3 -m reader.word_list  (or .\\.venv\\Scripts\\python.exe -m reader.word_list)
"""

import argparse
import collections
import csv
import os
import sys

from . import PROJECT_DIR, config, furigana, known_words, server

OUTPUT_PATH = os.path.join(PROJECT_DIR, "words_to_learn.csv")
LIMIT = 1000
# Names, prefixes and suffixes (お, たち) and interjections (うん) would crowd the top of the list.
LEFT_OUT_POS = {"接頭辞", "接尾辞", "感動詞"}


def waiting_cards():
    """{word: deck} for words on Anki cards not studied yet (Jlab is left out: its cards teach sentences)."""
    models = [name for name, field in known_words.WORD_FIELDS.items() if field not in known_words.WORD_LIST_FIELDS]
    query = known_words.notes_query(models) + " is:new -is:suspended"
    ids = known_words.anki("findCards", query=query)
    waiting = {}
    for start in range(0, len(ids), 500):
        for card in known_words.anki("cardsInfo", cards=ids[start:start + 500]):
            for word in known_words.note_words(card):
                waiting.setdefault(word, card["deckName"].split("::")[-1])
    return waiting


def rank_words(root, words, waiting):
    """Rows for unknown words in every .mokuro volume under root, most frequent first."""
    state = known_words.word_state(words)
    found = {}
    seen = set()
    for series in sorted(os.listdir(root)):
        folder = os.path.join(root, series)
        if not os.path.isdir(folder) or series in server.SKIP_MANGA_FOLDERS:
            continue
        for name in sorted(n for n in os.listdir(folder) if n.lower().endswith(".mokuro")):
            try:
                texts = list(known_words.bubbles(os.path.join(folder, name)))
            except (OSError, ValueError) as error:
                print(f"Skipped {series}/{name}: {error}", file=sys.stderr)
                continue
            for text in texts:
                # Each line once: publisher pages repeat in every volume, and shouted names in every chapter.
                if text in seen:
                    continue
                seen.add(text)
                for lemma, base, reading, always_known, feature in known_words.counted_words(text):
                    # Single kana and words ending in っ (ふっ, すっ) are mostly OCR noise or cut-off sounds.
                    noise = (len(base) == 1 and known_words.KANA_ONLY.fullmatch(base)) or base.endswith("っ")
                    if (feature.pos2 == "固有名詞" or feature.pos1 in LEFT_OUT_POS or noise or
                            state(lemma, base, reading, always_known) in ("known", "learning")):
                        continue  # also skips words known or being learned; cards still waiting stay on the list
                    entry = found.setdefault((lemma, reading), {"count": 0, "spellings": collections.Counter(),
                                                                "series": set(), "example": None})
                    entry["count"] += 1
                    # The reading of the spelling (コミックス), not of the dictionary entry (コミック).
                    entry["spellings"][(base, furigana._hiragana(feature.kanaBase or reading))] += 1
                    entry["series"].add(series)
                    # The shortest real sentence: OCR glitches tend to be very long.
                    if entry["example"] is None or 6 <= len(text) < len(entry["example"][0]):
                        entry["example"] = (text, series)
    rows = []
    for (lemma, reading), entry in sorted(found.items(), key=lambda item: -item[1]["count"]):
        word, word_reading = entry["spellings"].most_common(1)[0][0]
        deck = next((waiting[w] for w in [word, lemma, *(s for s, _ in entry["spellings"])] if w in waiting), "")
        rows.append({"Word": word, "Reading": word_reading, "Times in your manga": entry["count"],
                     "Series": len(entry["series"]), "Waiting in Anki": deck,
                     "Example": entry["example"][0], "Example from": entry["example"][1]})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m reader.word_list", description=__doc__.splitlines()[0])
    parser.add_argument("--manga", metavar="FOLDER", help='manga library (default: "manga" in config.json)')
    parser.add_argument("--config", metavar="FILE", help="settings file (default: config.json in the project folder)")
    args = parser.parse_args(argv)
    try:
        server.configure(config.load(args.config))
    except config.ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    args.manga = args.manga or server.ROOT
    words = known_words.anki_words()
    if words is None:
        print("Anki is not running and no saved word list exists. Start Anki and try again.", file=sys.stderr)
        return 1
    try:
        waiting = waiting_cards()
    except known_words.AnkiOffline:
        waiting = {}
        print("Anki is offline: the 'Waiting in Anki' column is empty.", file=sys.stderr)
    try:
        rows = rank_words(args.manga, words, waiting)
    except furigana.FuriganaUnavailable as error:
        print(error, file=sys.stderr)
        return 1
    try:
        with open(OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as file:  # BOM: Excel reads it as UTF-8
            writer = csv.DictWriter(file, ["Rank", *rows[0]] if rows else ["Rank"])
            writer.writeheader()
            writer.writerows({"Rank": rank, **row} for rank, row in enumerate(rows[:LIMIT], 1))
    except OSError as error:
        print(f"Could not write {OUTPUT_PATH}: {error}", file=sys.stderr)
        return 1
    print(f"Wrote the {min(len(rows), LIMIT)} most common of {len(rows)} unknown words to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
