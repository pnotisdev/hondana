"""Local Japanese readings for the EPUB reader."""

import re
import threading


_taggers = threading.local()
_KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f々〆]")


class FuriganaUnavailable(Exception):
    pass


def _tagger():
    if not hasattr(_taggers, "value"):
        try:
            import fugashi
            _taggers.value = fugashi.Tagger()
        except (ImportError, RuntimeError) as error:
            raise FuriganaUnavailable(
                "Local furigana requires fugashi and unidic-lite; see the book reader guide."
            ) from error
    return _taggers.value


def _hiragana(value):
    return "".join(chr(ord(char) - 0x60) if "\u30a1" <= char <= "\u30f6" else char for char in value)


def annotate(text):
    parts = []
    cursor = 0
    for word in _tagger()(text):
        surface = word.surface
        start = text.find(surface, cursor)
        if start < 0:
            raise ValueError("Tokenizer returned text that does not match the input")
        if start > cursor:
            parts.append({"text": text[cursor:start]})
        kana = word.feature.kana
        part = {"text": surface}
        if _KANJI.search(surface) and isinstance(kana, str) and kana and kana != "*":
            part["reading"] = _hiragana(kana)
        parts.append(part)
        cursor = start + len(surface)
    if cursor < len(text):
        parts.append({"text": text[cursor:]})
    return parts
