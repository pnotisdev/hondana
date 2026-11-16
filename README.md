# Hondana

Hondana (本棚, "bookshelf") is a local library for reading Japanese manga and light novels with
[Yomitan](https://github.com/yomidevs/yomitan) lookups, and for seeing how much
of each one you can already read, based on your Anki reviews.

- **Manga library** for volumes OCR'd with [Mokuro](https://github.com/kha-white/mokuro).
  Speech bubbles are served so that Yomitan reads each one as a single line, so
  words that wrap across OCR lines can be looked up, and the sentence Yomitan
  sends to Anki is the whole bubble.
- **EPUB reader** with vertical or horizontal text, your choice of fonts and
  page colours, optional generated furigana, notes, and position and character
  counts that match [ッツ Reader (ttsu)](https://reader.ttsu.app/).
- **Two book shelves**: your own EPUB folder, chosen in Settings, and the
  graded readers that come with Hondana.
- **Anki colours**: every word in a book can be coloured as known, learning,
  new, or not in Anki, with a per-chapter summary. With a
  [jpdb](https://jpdb.io/) API key, the words in your jpdb decks count too.
- **Known-word coverage** for each manga series and volume and for each book,
  and for books the most frequent words you don't know yet, ready to look up
  with Yomitan.
- **Difficulty from three sites**: [Jiten](https://jiten.moe/) (difficulty,
  word statistics, summaries, per-volume difficulty),
  [Natively](https://learnnatively.com/) levels, and jpdb difficulty for
  novels. A wrong match can be fixed with a link.

Everything runs on your own computer. The server only reads from Anki and jpdb;
it never changes your cards. Titles (not book text) are sent to Jiten, Natively
and jpdb to look them up; each can be turned off in Settings.

## Requirements

- Python 3.9 or newer. Serving books and manga needs only the standard library.
- For Anki colours, coverage, and generated furigana: `fugashi` and
  `unidic-lite` (`requirements-reader.txt`).
- For Anki features: Anki with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on.
- For manga OCR: Mokuro (optional, and separate from the reader).
- Yomitan, installed in your browser.

The launcher and OCR script are for Windows; on other systems, start the
server with `python3 -m reader --open`.

## Quick start

```powershell
git clone https://github.com/<you>/hondana.git
cd hondana
```

1. Double-click `Setup.cmd`. It creates a Python environment in `.venv` and
   installs the reader's packages; run `Setup.cmd ocr` instead to also install
   Mokuro for processing manga (a large download, as it includes PyTorch).
   Run it again later to update the packages.
2. Double-click `Start-Reader.cmd`. It opens `http://127.0.0.1:8421/` in your
   browser; keep its window open while reading, and close it or press Ctrl+C
   to stop. Without `.venv` it falls back to the Windows Python launcher or
   `python` on your PATH.
3. Open **Settings**, choose your manga and book folders with **Browse…**,
   enter your AnkiConnect address, and under **Note types with words** pick the
   note types and fields that hold your words. Changes apply at once.

The **Books** tab shows your book folder and, on a shelf of their own, the
graded readers that come with Hondana (turn them off in Settings).

## Configuration

The **Settings** page changes the folders, the folders to leave out, the
online lookups and their API keys, and the Anki settings (including which note
types and fields hold your words) while the reader runs. **Browse…** opens your
system's folder window on the computer running the reader; settings can only be
changed from that computer, also in LAN mode.

Settings are saved in `config.json` in the project folder, which git ignores,
so your paths and Anki details stay out of the repository. You can also edit it
by hand: `config.example.json` lists every setting with its default, and any
you leave out keep the default. Restart after editing the file, and for
`port`, `lan` and `allowHosts`, which are only read at startup.

| Setting | Default | Meaning |
| --- | --- | --- |
| `manga` | `~/Manga` | Manga folder: one subfolder per series, holding volume folders or `.cbz` files and Mokuro's `.html` files. `~` is your home folder; a relative path starts at the config file. |
| `books` | `""` | Your EPUB folder, searched with its subfolders; `""` for none. |
| `gradedReaders` | `true` | Also show the graded readers in this project's `graded-readers` folder, as a shelf of their own. |
| `port` | `8421` | Port of the library page. |
| `lan` | `false` | Let other devices on your network connect. See [Reading from another device](#reading-from-another-device). |
| `allowHosts` | `[]` | Host names to answer besides IP addresses when `lan` is on, such as a Tailscale name. |
| `skipMangaFolders` | `[]` | Folder names in the manga folder to leave out of the library and of OCR. |
| `jiten` | `true` | Look up difficulty, word statistics and summaries on jiten.moe. Set to `false` to make no requests to it. |
| `natively` | `true` | Look up Natively levels on learnnatively.com. |
| `jpdb` | `true` | Look up jpdb difficulty for books (jpdb has no manga). |
| `apiKeys.jiten` | `""` | Optional Jiten API key: Jiten then shows how much of each title you know, from the words you track on Jiten. |
| `apiKeys.jpdb` | `""` | Optional jpdb API key: the words in your jpdb decks then count as known, learning or new like Anki cards. |
| `anki.url` | `http://127.0.0.1:8765` | AnkiConnect's address. Change the port if you changed `webBindPort` in AnkiConnect's settings. |
| `anki.search` | `""` | An Anki search that limits which cards count, such as `deck:Japanese`. |
| `anki.knownInterval` | `21` | Interval in days at which a card's word counts as known. |
| `anki.wordFields` | see below | Note type name → the field that holds its word. Only these note types are read. |
| `anki.wordListFields` | `["Jlab-Lemma"]` | Fields that list every word of a sentence, separated by spaces. |
| `anki.wordCardsOnly` | `["JLPT Tango N5 Japanese"]` | Note types that mix word cards and sentence cards; the sentence cards are skipped. |

For example, a config for a library on drive D and AnkiConnect on port 9000,
counting only cards in the deck "Japanese":

```json
{
  "manga": "D:\\Manga",
  "books": "D:\\Books",
  "anki": {
    "url": "http://127.0.0.1:9000",
    "search": "deck:Japanese"
  }
}
```

Command-line options override the file for one launch; run
`py -3 -m reader --help` for all of them:

```powershell
.\Start-Reader.cmd --manga "D:\Manga" --books "D:\Books" --port 8422
.\Start-Reader.cmd --config "D:\other-config.json"
```

## Anki

The Anki features need Anki open with AnkiConnect. AnkiConnect only accepts
requests from pages it trusts; the reader asks from its own server, so no
`webCorsOriginList` change is needed (the default list, or one with
`http://localhost`, works). Only read actions are sent.

A word's status comes from its cards in the note types listed in
`anki.wordFields`:

- **Known**: a card at an interval of `anki.knownInterval` days or more. A
  suspended card still counts by how far it got.
- **Learning**: a studied card with a shorter interval.
- **New**: a card you haven't studied yet.
- **Not in Anki**: no card at all.

Words are matched by dictionary form, so 行った matches a card for 行く, and a
card in kana (わかる) matches the kanji spelling (分かる). Particles and
auxiliaries always count as known; punctuation and numbers are not counted.

The default `wordFields` cover common community decks: Kaishi 1.5k, the JLPT
Tango decks, Lapis cards (such as those Yomitan mines), and Jlab. To use your
own, open **Settings → Show note types from Anki**: it lists every note type
that has notes, with a few sample values, and suggests the field that holds the
word. Tick the ones that are word cards; leave out kanji cards (a single kanji
per card) and info notes. **Check** reads your cards and shows how many words
are known, learning and new.

The word list is read again every ten minutes. After mining new cards with
Yomitan, use **Refresh from Anki** in the reader's settings to colour them at
once. When Anki is closed, the reader uses the last word list it fetched and
says "Anki offline".

### jpdb

With a jpdb API key (from the bottom of jpdb.io's settings page) in
**Settings → jpdb**, the words in your jpdb decks, including "never forget"
and blacklisted words, count as well: a word takes the best status it has in
Anki or jpdb. Known, never-forget, blacklisted and redundant cards count as
known; learning, due, failed and suspended ones as learning; new and locked
ones as new.

## Manga

Process volumes with Mokuro first: the library lists every series and shows
which volumes are ready. `process_manga.ps1` runs Mokuro from the project's
`.venv` (or your PATH) on every volume that has no `.html` yet:

```powershell
.\Setup.cmd ocr                          # once, to install Mokuro
.\process_manga.ps1
.\process_manga.ps1 "Series name"       # every volume of one series, even processed ones
.\process_manga.ps1 -MangaRoot "D:\Manga"
```

It reads `manga` and `skipMangaFolders` from `config.json`.

Click a series for its volumes, difficulty, summary, and known-word coverage.
Use **Sort** to list the easiest first by Jiten or Natively, or by the share of
words you know. The first time a volume's coverage is shown it takes about a
second to count; after that it is cached. Point at a volume for its own Jiten
difficulty.

To list the words you'd gain most from learning, most frequent first:

```powershell
.\.venv\Scripts\python.exe -m reader.word_list
```

This writes `words_to_learn.csv`, with a sample sentence for each word and the
deck if the word is already waiting as a new card.

## Books

The project comes with 38 short graded readers in `graded-readers/`: ten each
for JLPT N5, N4 and N3, and eight more N3 stories inspired by anime and manga
(Graded Stories N3 Anime and Manga). They have a shelf of their own under your
books, and are not looked up online. Each is an original story of about 1,500 to 3,100 characters (at most
20 minutes of reading), written with Claude in the style of a well-known
Japanese author, genre or work (the `～風` in the author field), with furigana,
a glossary, grammar notes, comprehension questions and words to mine.

Click a book for its difficulty on Jiten, Natively and jpdb, how many of its
words you know, and its most frequent words you don't know yet. The
[book reader guide](docs/book-reader.md) covers series, reading settings,
furigana, Anki colours, and notes. Notes, reading progress, and preferences are
stored in your browser, not in the EPUB files.

## Difficulty lookups

Titles are matched by name: a manga by its folder name, a book by its series
or title without the volume number. Each site's answer is cached in
`lookup_cache.json` for two weeks (a title not found is tried again after a
week).

| Site | What it shows | Notes |
| --- | --- | --- |
| Jiten | Difficulty 0–5, characters, different words, how many words give 90% and 95% of the text, summary, and each volume's difficulty | With an API key, also how much of the title you know on Jiten |
| Natively | Level (such as L27, with its JLPT estimate), volumes, rating | Read from the search suggestions its own site uses; no key needed |
| jpdb | Average and peak difficulty out of 100, characters, different words | Novels only: jpdb has no manga |

If a match is wrong or missing, open the title and choose **Wrong title?** (or
**Add the link**) under that site: paste the link to the right page, or choose
**Not on …**. **Search again** removes your link. Links are kept until you
remove them; this works on the computer running the reader.

## Reading from another device

By default nothing outside this computer can connect. To read from a laptop or
phone, set `"lan": true` in `config.json` or start with `--lan`. The server then
listens on all network interfaces, on the main port (8421) and, for isolated
EPUB content, the next one (8422); change them with `port` and `--content-port`.
**There is no login**: anyone who can reach those ports can read your library,
so don't port-forward them or expose them to the internet.

1. Allow the two ports in Windows Firewall, only for your own networks (in an
   administrator PowerShell). For the same Wi-Fi or LAN:

   ```powershell
   New-NetFirewallRule -DisplayName "Hondana" -Direction Inbound -Protocol TCP -LocalPort 8421,8422 -RemoteAddress LocalSubnet -Action Allow
   ```

   For another network, use [Tailscale](https://tailscale.com/) on both devices
   and replace `LocalSubnet` with `100.64.0.0/10`.
2. On the other device, open `http://<this computer's address>:8421/`, using the
   IPv4 address from `ipconfig` or the Tailscale `100.x` address. A name such as
   `desktop.example.ts.net` is refused unless it is in `allowHosts`.
3. Install Yomitan in that device's browser. Notes, progress, and preferences
   are stored per browser and per address, so the other device starts empty.

Requests addressed to any other host name are refused, which protects the
library from DNS-rebinding attacks by other websites.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test .\tests\test_reader_frame.js .\tests\test_reader_progress.js
```

The Node tests need Node.js 18 or newer. Tests that tokenize Japanese are
skipped if `fugashi` is not installed.

## Project layout

| Path | Contents |
| --- | --- |
| `Setup.cmd` | Creates `.venv` and installs the packages (`requirements-reader.txt`, and with `ocr` also `requirements-ocr.txt`) |
| `Start-Reader.cmd` | Double-click launcher for the server |
| `process_manga.ps1` | Mokuro OCR for manga volumes |
| `config.example.json` | Every setting with its default, for editing `config.json` by hand |
| `graded-readers/` | 38 graded readers for N5 to N3, the default book library |
| `reader/` | Python server: `server.py` (HTTP and command line), `config.py` (settings), `epub_library.py` (EPUBs), `known_words.py` (Anki and jpdb words, coverage), `word_list.py` (words to learn), `lookups.py` (cached title lookups) with `jiten.py`, `natively.py` and `jpdb.py`, `titles.py` (title matching), `web.py` (requests), `furigana.py` (readings) |
| `web/manga/` | Manga library page |
| `web/books/` | Book library and reader; `lookups.js` and `lookups.css` are shared with the manga page |
| `web/settings/` | Settings page |
| `tests/` | Python and Node tests |
| `docs/` | Book reader guide |
| `userscripts/` | Anime-Planet difficulty sorter, independent of the server |

The server writes `lookup_cache.json` and `known_words_cache/` in the project
folder; both are rebuilt as needed and are not tracked. (`difficulty_cache.json`
from earlier versions is read once for its Jiten matches.)

## Credits

- [Mokuro](https://github.com/kha-white/mokuro) produces the manga pages this library serves.
- Character counting follows [ッツ Reader](https://github.com/ttu-ttu/ebook-reader)'s rule (BSD-3-Clause).
- Japanese is tokenized with [fugashi](https://github.com/polm/fugashi) and [UniDic](https://clrd.ninjal.ac.jp/unidic/) (`unidic-lite`).
- Difficulty ratings, statistics and summaries come from [Jiten](https://jiten.moe/),
  [Natively](https://learnnatively.com/) and [jpdb](https://jpdb.io/).

## License

MIT; see [LICENSE](LICENSE). This covers the code and the graded readers in
`graded-readers/`. Mokuro, fugashi, UniDic and the other tools above keep their
own licenses.
