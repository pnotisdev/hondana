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
- **Anki colours**: every word in a book can be coloured as known, learning,
  new, or not in Anki, with a per-chapter summary.
- **Known-word coverage** for each manga series and volume, a list of the most
  common words you don't know yet, and difficulty ratings and summaries from
  [Jiten](https://jiten.moe/).

Everything runs on your own computer. The server only reads from Anki; it never
changes your cards.

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
3. Open **Settings**, choose your manga and book folders with **Browse…**, and
   enter your AnkiConnect address. Changes apply at once.

Until you choose a book folder, the **Books** tab shows the graded readers that
come with Hondana.

## Configuration

The **Settings** page changes the folders, the folders to leave out, Jiten,
and the main Anki settings while the reader runs. **Browse…** opens your
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
| `books` | `graded-readers` | EPUB folder, searched with its subfolders. The default is the sample library of graded readers in this project. |
| `port` | `8421` | Port of the library page. |
| `lan` | `false` | Let other devices on your network connect. See [Reading from another device](#reading-from-another-device). |
| `allowHosts` | `[]` | Host names to answer besides IP addresses when `lan` is on, such as a Tailscale name. |
| `skipMangaFolders` | `[]` | Folder names in the manga folder to leave out of the library and of OCR. |
| `jiten` | `true` | Look up difficulty and summaries on jiten.moe. Set to `false` to make no requests to it. |
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
`webCorsOriginList` change is needed.

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
Tango decks, Lapis cards (such as those Yomitan mines), and Jlab. Add your own
note types by name, for example `"Core 2k/6k": "Vocabulary-Kanji"`. To find a
field name, open **Browse** in Anki, pick a card, and look at the field labels
in the editor.

When Anki is closed, the reader uses the last word list it fetched and says
"Anki offline".

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

Click a series for its volumes, Jiten difficulty, summary, and known-word
coverage. Use **Sort** to list the easiest first. The first time a volume's
coverage is shown it takes about a second to count; after that it is cached.

To list the words you'd gain most from learning, most frequent first:

```powershell
.\.venv\Scripts\python.exe -m reader.word_list
```

This writes `words_to_learn.csv`, with a sample sentence for each word and the
deck if the word is already waiting as a new card.

## Books

The project comes with 30 short graded readers in `graded-readers/`, ten each
for JLPT N5, N4 and N3, and they are the book library until you set `books`.
Each is an original story of about 1,500 to 3,100 characters, written with
Claude in the style of a well-known Japanese author or genre (the `～風` in the
author field), with furigana, a glossary, grammar notes, comprehension
questions and words to mine.

The [book reader guide](docs/book-reader.md) covers series, reading settings,
furigana, Anki colours, and notes. Notes, reading progress, and preferences are
stored in your browser, not in the EPUB files.

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
| `graded-readers/` | 30 graded readers for N5 to N3, the default book library |
| `reader/` | Python server: `server.py` (HTTP and command line), `config.py` (settings), `epub_library.py` (EPUBs), `known_words.py` (Anki words and coverage), `word_list.py` (words to learn), `jiten.py` (difficulty), `furigana.py` (readings) |
| `web/manga/` | Manga library page |
| `web/books/` | Book library and reader |
| `web/settings/` | Settings page |
| `tests/` | Python and Node tests |
| `docs/` | Book reader guide |
| `userscripts/` | Anime-Planet difficulty sorter, independent of the server |

The server writes `difficulty_cache.json` and `known_words_cache/` in the
project folder; both are rebuilt as needed and are not tracked.

## Credits

- [Mokuro](https://github.com/kha-white/mokuro) produces the manga pages this library serves.
- Character counting follows [ッツ Reader](https://github.com/ttu-ttu/ebook-reader)'s rule (BSD-3-Clause).
- Japanese is tokenized with [fugashi](https://github.com/polm/fugashi) and [UniDic](https://clrd.ninjal.ac.jp/unidic/) (`unidic-lite`).
- Difficulty ratings and summaries come from [Jiten](https://jiten.moe/).

## License

MIT; see [LICENSE](LICENSE). This covers the code and the graded readers in
`graded-readers/`. Mokuro, fugashi, UniDic and the other tools above keep their
own licenses.
