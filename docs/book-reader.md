# Book library

Start the existing server as usual, then open `http://127.0.0.1:8421/books/`.
If you specify a different port, use that port instead. The Manga link returns
to the existing manga directory.

The library scans the `books` folder set in `config.json` and its subfolders for EPUB files. Click a
card to see its Jiten difficulty and summary, then open a book there to read its original text and formatting, select a section, or use the
Previous and Next buttons. Reading position is stored in this browser's local
storage; clearing browser data or switching browsers will clear or separate it.

To organize a series, put its EPUBs together in a folder named after the
series, for example `Books\My Series\My Series Vol 1.epub` and
`My Series Vol 2.epub`. The folder becomes a series card with a cover for
each volume. Books directly in the book folder can also be grouped by EPUB
series metadata (EPUB 3 or Calibre) or matching numbered titles or filenames.
Volume numbers are used for ordering; otherwise titles are sorted naturally.
Moving an existing EPUB changes its library identity and its saved progress
will no longer be associated with the new path.
Use **Settings** to switch between the publisher's layout, vertical
Japanese, and horizontal text. You can also adjust the Japanese font, text
size, line spacing, page color, and horizontal text width. Mincho and Gothic
use fonts installed on your device; no fonts are downloaded. Publisher layout
is useful for illustrated pages or books whose original styling should be
preserved. The mouse wheel scrolls horizontally in vertical-writing sections.
Use **Previous page** and **Next page** below the reading surface, or the
keyboard: Space and Page Down go forward, Shift+Space and Page Up go back, and
the left and right arrow keys follow the reading direction (in vertical text
← is the next page, and the page buttons swap sides to match). At the end of a
section these controls continue into the next section, and going back from the
start of a section opens the end of the previous one. The library shows a
**Continue reading** button for the book you opened most recently.
The character indicator below the page shows an estimated position and the
book's total characters, counted the way ttsu (reader.ttsu.app) counts them:
kana, kanji, letters and digits, without punctuation, markup, furigana or
images. The bar and percentage below the toolbar, and the library's "% read",
are that position as a share of the total; until the characters have been
counted they are estimated from the section number instead. The position is estimated from your
scroll position, not a record of what you actually read. Both indicators move
backwards when you navigate backwards.

Furigana already present in an EPUB is always preserved. To generate missing
readings locally, the reader needs its optional packages, which `Setup.cmd`
installs. If you start the reader with system Python instead, run `py -3 -m pip
install -r .\requirements-reader.txt`. Then choose **Generate missing readings** under
**Settings → Furigana**. Turn it off by choosing **Publisher's
readings only**. Generated readings are estimates and may be incorrect,
especially for names and unusual words. Existing EPUB furigana is not replaced,
and book text is not sent to an external service.

To practise reading kanji, choose **Hide · point at a word to reveal**: the
publisher's furigana stays hidden until you hover over (or tap) a word.
Furigana is never included when you select or copy text, so note excerpts
contain only the base text.

To see which words you already know, choose **Colour words by their Anki
status** under **Settings → Anki colours**. Words turn green (known: a card at
an interval of `anki.knownInterval` days or more, 21 by default), yellow (learning: studied, shorter interval),
blue (new: a card you haven't studied yet) or red (not in Anki). A hiragana word
that isn't in Anki gets a dotted underline instead of red. Particles,
auxiliaries, numbers and names without a card are left uncoloured and are not
counted. The line under the page shows the share of each colour in the current
section; point at one for the number of different words. The colours need Anki
running with AnkiConnect at the address in `anki.url` (it is only read), and the same
optional packages as generated furigana. Words are matched by dictionary form,
so 行った matches a card for 行く, a card in kana matches the kanji spelling, and
a card for a longer word or expression (自転車, 気がつく) is coloured as one
word. If Anki is closed, the last word list the server saw is used.

Use **Notes** to save notes at the current reading location. Select a passage
before adding a note to include an excerpt; notes can be edited, deleted, or
used to return to the passage. Settings, notes, and progress are stored in the
browser on this device, not in the EPUB files. Clearing browser data, changing
browsers, or moving an EPUB can make them unavailable.
Yomitan must be installed and enabled for the local server. Original EPUB
furigana is preserved. EPUB scripts and
external links are disabled. The reader serves EPUB content from a separate,
automatically selected loopback port to let browser extensions access text
without giving books access to the reader. Restart the server after updating
the reader and reload the page.

To use another folder, set `books` in `config.json` or start the server with
`--books "D:\Books"`; see the [project README](../README.md). DRM-protected EPUBs
and formats other than EPUB are not supported.
