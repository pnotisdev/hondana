import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from unittest import mock

from reader import furigana, known_words, server


def have_tagger():
    try:
        furigana._tagger()
        return True
    except furigana.FuriganaUnavailable:
        return False


def write_mokuro(path, *blocks):
    with open(path, "w", encoding="utf-8") as file:
        json.dump({"pages": [{"blocks": [{"lines": lines} for lines in blocks]}]}, file, ensure_ascii=False)


def words(known=(), learning=(), new=()):
    saved = {"known": list(known), "learning": list(learning), "new": list(new), "at": 0}
    return known_words.word_sets(saved, offline=False)


class AnkiWordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(known_words, "CACHE_DIR", self.temp.name),
                   mock.patch.object(known_words, "_anki_state", {"words": None, "checked": 0.0})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.temp.cleanup)

    def test_clean_word_drops_markup_readings_and_notes(self):
        for value, expected in (("<b>家賃</b>[2]", "家賃"), (" 持[も]つ", "持つ"), ("（カレンダーが）掛かる", "掛かる"),
                                ("お&nbsp;茶", "お茶"), ("<br>", "")):
            self.assertEqual(known_words.clean_word(value), expected, value)

    def test_words_come_from_mapped_fields_and_known_beats_learning(self):
        def note(model, field, value):
            return {"modelName": model, "fields": {field: {"value": value}}}

        mature = [note("Kaishi 1.5k", "Word", "家"),
                  note("JLPT Tango N5 Japanese", "Expression", "はさみ[;o]"),
                  note("JLPT Tango N5 Japanese", "Expression", "この[;h] はさみ[;o] を 使[つか,つかう;h]って"),
                  note("JlabNote-JlabConverted-1+", "Jlab-Lemma", "ちょっと 遠い けど"),
                  note("Kaishi 1.5k", "Other", "犬"),
                  note("Prefecture (Geography)", "Expression", "北海道")]
        young = [note("Japanese sentences", "VocabKanji", "家[2]"),
                 note("Lapis Simplified", "Expression", "<b>荷物</b>")]
        waiting = [note("Lapis Simplified", "Expression", "街"), note("Kaishi 1.5k", "Word", "荷物")]
        queries = []

        def fake_anki(action, **params):
            if action == "findNotes":
                queries.append(params["query"])
                return [len(queries)]
            return [mature, young, waiting][params["notes"][0] - 1]

        with mock.patch.object(known_words, "anki", side_effect=fake_anki):
            result = known_words.fetch_anki_words()
        self.assertEqual(result["known"], ["けど", "ちょっと", "はさみ", "家", "遠い"])
        self.assertEqual((result["learning"], result["new"]), (["荷物"], ["街"]))  # each word in its best list
        self.assertIn("prop:ivl>=21", queries[0])
        # Suspended cards count by how far they got, but a suspended card never studied is not waiting.
        self.assertNotIn("is:suspended", queries[0] + queries[1])
        self.assertIn("is:new -is:suspended", queries[2])
        self.assertIn('-"note:JlabNote-JlabConverted-1"', queries[2])  # sentence cards are not word cards

    def test_only_read_actions_reach_anki(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(ValueError):
                known_words.anki("addNote", note={})
        urlopen.assert_not_called()

    def test_offline_anki_uses_the_saved_list_or_reports_none(self):
        offline = mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused"))
        with offline:
            self.assertIsNone(known_words.anki_words())
        saved = {"known": ["猫"], "learning": ["犬"], "at": 1700000000.0}
        known_words._anki_state.update(checked=0.0)
        with mock.patch.object(known_words, "fetch_anki_words", return_value=saved):
            self.assertFalse(known_words.anki_words()["offline"])
        known_words._anki_state.update(words=None, checked=0.0)  # as after a server restart
        with offline:
            result = known_words.anki_words()
        self.assertEqual((result["known"], result["at"], result["offline"]), ({"猫"}, 1700000000.0, True))

    def test_a_list_in_hand_is_returned_at_once_and_refreshed_behind_the_request(self):
        current = known_words.word_sets({"known": ["猫"], "at": 1.0}, offline=False)
        known_words._anki_state.update(words=current, checked=0.0)
        with mock.patch.object(known_words.threading, "Thread") as thread:
            self.assertIs(known_words.anki_words(), current)
            self.assertIs(known_words.anki_words(), current)  # checked a moment ago: no second refresh
        thread.assert_called_once_with(target=known_words.refresh_anki_words, daemon=True)
        thread.return_value.start.assert_called_once_with()


@unittest.skipUnless(have_tagger(), "fugashi and unidic-lite are not installed")
class CountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(known_words, "CACHE_DIR", os.path.join(self.temp.name, "cache"))
        patch.start()
        self.addCleanup(patch.stop)

    def count(self, text, **lists):
        types = [(*key, count) for key, count in known_words.word_types(text).items()]
        return known_words.count_known(types, words(**lists))

    def test_punctuation_and_numbers_are_skipped_and_particles_are_known(self):
        result = self.count("猫が好きです。テレビ！１２３", known=["猫", "テレビ"])
        # 猫 が 好き です テレビ: only 好き is unknown
        self.assertEqual(result, {"words": 5, "known": 4, "learning": 0, "unique": 5, "uniqueKnown": 4})

    def test_words_match_by_dictionary_form_or_kana(self):
        result = self.count("ちょっと持って。コレ、都", known=["ちょっと", "これ", "と"], learning=["持つ"])
        # ちょっと (lemma 一寸) and コレ (read これ) are known, て is a particle, and 持っ is being learned;
        # 都 is read と, but a reading alone never makes a kanji word known.
        self.assertEqual((result["words"], result["known"], result["learning"]), (5, 3, 1))

    def test_a_card_in_another_spelling_still_matches(self):
        # The cards say いい and 出来る; the text says 良い and できる.
        result = self.count("良い。できる", known=["いい"], new=["出来る"])
        self.assertEqual((result["words"], result["known"]), (2, 1))

    def test_text_is_split_into_pieces_with_each_word_and_its_status(self):
        annotate = known_words.annotator(words(known=["自転車", "いい"], learning=["乗る"], new=["気がつく"]))
        text = "自転車に乗った。良い！ 気がついた東京で12時のテレビ"
        pieces = annotate(text)
        self.assertEqual("".join(piece[0] for piece in pieces), text)
        self.assertEqual([piece for piece in pieces if len(piece) > 1], [
            ["自転車", 3, "自転車"],     # one card, though the tokenizer splits it into 自転 and 車
            ["乗っ", 2, "乗る"],
            ["良い", 3, "良い"],
            ["気がつい", 1, "気がつく"],  # an expression card covers its inflected form
            ["時", 0, "時"],
            ["テレビ", 0, "テレビ"],      # not in Anki
        ])  # particles, auxiliaries, the name 東京 and the number are not counted
        self.assertEqual(pieces[1], ["に"])

    def test_volumes_join_lines_per_bubble_and_are_cached_until_changed(self):
        path = os.path.join(self.temp.name, "v1.mokuro")
        write_mokuro(path, ["荷物を持", "ってくれ"], ["ありがとう"])
        types = known_words.volume_types(path)
        self.assertIn("持つ", {lemma for lemma, *_ in types})
        with mock.patch.object(known_words, "word_types", side_effect=AssertionError("not cached")):
            self.assertEqual(known_words.volume_types(path), types)
        write_mokuro(path, ["猫"])
        self.assertEqual([lemma for lemma, *_ in known_words.volume_types(path)], ["猫"])


@unittest.skipUnless(have_tagger(), "fugashi and unidic-lite are not installed")
class KnownWordsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        os.mkdir(os.path.join(self.temp.name, "series"))
        write_mokuro(os.path.join(self.temp.name, "series", "v2.mokuro"), ["猫が好き"])
        write_mokuro(os.path.join(self.temp.name, "series", "v10.mokuro"), ["猫"])
        for patch in (mock.patch.object(server, "ROOT", self.temp.name),
                      mock.patch.object(known_words, "CACHE_DIR", os.path.join(self.temp.name, "cache"))):
            patch.start()
            self.addCleanup(patch.stop)
        self.server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", path)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_series_and_volume_counts(self):
        with mock.patch.object(known_words, "anki_words", return_value=words(known=["猫"])):
            status, body = self.get("/api/known-words?series=series")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual([(v["name"], v["words"], v["known"]) for v in data["volumes"]], [("v2", 3, 2), ("v10", 1, 1)])
        self.assertEqual((data["series"]["words"], data["series"]["known"], data["series"]["unique"]), (4, 3, 3))
        self.assertEqual((data["ankiOffline"], data["knownWords"]), (False, 1))

    def post(self, texts):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("POST", "/api/word-status", json.dumps({"texts": texts}), {"Content-Type": "application/json"})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_word_status_of_book_text(self):
        with mock.patch.object(known_words, "anki_words", return_value=words(known=["猫"])):
            self.assertEqual(self.post(["猫が好き", ""]), (200, {
                "results": [[["猫", 3, "猫"], ["が"], ["好き", 0, "好き"]], []], "ankiOffline": False}))
        with mock.patch.object(known_words, "anki_words", return_value=None):
            self.assertEqual(self.post(["猫"]), (200, {"results": None, "ankiOffline": True}))

    def test_offline_anki_and_missing_series(self):
        with mock.patch.object(known_words, "anki_words", return_value=None):
            self.assertEqual(self.get("/api/known-words?series=series"), (200, b'{"ankiOffline": true}'))
            self.assertEqual(self.get("/api/known-words?series=missing")[0], 404)
            self.assertEqual(self.get("/api/known-words?series=..")[0], 404)


if __name__ == "__main__":
    unittest.main()
