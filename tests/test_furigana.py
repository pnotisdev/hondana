import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

from reader import furigana


class FuriganaTests(unittest.TestCase):
    def test_preserves_whitespace_and_skips_unknown_readings(self):
        words = [
            SimpleNamespace(surface="学校", feature=SimpleNamespace(kana="ガッコウ")),
            SimpleNamespace(surface="へ", feature=SimpleNamespace(kana="ヘ")),
            SimpleNamespace(surface="謎", feature=SimpleNamespace(kana=None)),
        ]
        with mock.patch.object(furigana, "_tagger", return_value=lambda text: words):
            self.assertEqual(furigana.annotate(" 学校 へ謎\n"), [
                {"text": " "}, {"text": "学校", "reading": "がっこう"},
                {"text": " "}, {"text": "へ"}, {"text": "謎"}, {"text": "\n"},
            ])

    def test_rejects_mismatched_tokenizer_output(self):
        word = SimpleNamespace(surface="別", feature=SimpleNamespace(kana="ベツ"))
        with mock.patch.object(furigana, "_tagger", return_value=lambda text: [word]):
            with self.assertRaises(ValueError):
                furigana.annotate("学校")

    @unittest.skipUnless(importlib.util.find_spec("fugashi") and importlib.util.find_spec("unidic_lite"),
                         "Optional furigana dependencies are not installed")
    def test_real_tokenizer(self):
        parts = furigana.annotate("学校へ行く。")
        self.assertEqual("".join(part["text"] for part in parts), "学校へ行く。")
        self.assertIn({"text": "学校", "reading": "がっこう"}, parts)


if __name__ == "__main__":
    unittest.main()
