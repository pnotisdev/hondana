import html
import json
import math
import mimetypes
import os
import posixpath
import re
import secrets
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser

from . import PROJECT_DIR, config


BOOK_ROOT = os.path.expanduser(config.DEFAULTS["books"])  # set from config.json when the server starts
APP_DIR = os.path.join(PROJECT_DIR, "web", "books")
HTML_TYPES = {"application/xhtml+xml", "text/html"}

# What ttsu (reader.ttsu.app) counts, so positions and totals agree with it: kana, kanji, letters and
# digits, but no punctuation. From its get-character-count.ts; \p{Radical} and \p{Unified_Ideograph}
# are written out because the re module has no Unicode properties.
COUNTED_CHARACTERS = re.compile(
    "[0-9A-Za-z○◯々-〇〻ぁ-ゖゝ-ゞァ-ヺー０-９Ａ-Ｚａ-ｚｦ-ﾝ"
    r"⺀-⺙⺛-⻳⼀-⿕"  # radicals
    r"㐀-䶿一-鿿﨎﨏﨑﨓﨔﨟﨡﨣﨤﨧-﨩"  # kanji
    r"\U00020000-\U0002ee5d\U00030000-\U000323af]")  # rare kanji outside the basic plane


class CharacterCounter(HTMLParser):
    EXCLUDED = {"head", "script", "style", "rt", "rp", "svg", "math", "template"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.count = 0
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.rsplit(":", 1)[-1]
        if tag in self.VOID:
            # A character the font lacks, drawn as an image: ttsu counts it as one character.
            if tag == "img" and not self.skip_depth and "gaiji" in (dict(attrs).get("class") or ""):
                self.count += 1
            return
        if self.skip_depth:
            self.skip_depth += 1
        elif tag in self.EXCLUDED or any(
                name == "hidden" or (name == "aria-hidden" and value == "true")
                for name, value in attrs):
            self.skip_depth = 1

    def handle_startendtag(self, tag, attrs):
        if tag.rsplit(":", 1)[-1] in self.VOID:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.skip_depth and tag.rsplit(":", 1)[-1] not in self.VOID:
            self.skip_depth -= 1

    def handle_data(self, data):
        if not self.skip_depth:
            self.count += len(COUNTED_CHARACTERS.findall(data))


def count_book_characters(name):
    book = read_book(name)
    sections = []
    with zipfile.ZipFile(book_path(name)) as archive:
        for section in book["sections"]:
            text = archive.read(section["path"]).decode("utf-8-sig")
            counter = CharacterCounter()
            counter.feed(text)
            counter.close()
            sections.append({"path": section["path"], "characters": counter.count})
    return {"sections": sections, "total": sum(section["characters"] for section in sections)}


def plain_text(value):
    # dc:description often holds escaped HTML (Calibre writes <p> markup); keep only its paragraph breaks.
    value = re.sub(r"<(?:br|/p|/div)\b[^>]*>", "\n", value, flags=re.IGNORECASE)
    value = html.unescape(re.sub(r"<[^>]*>", "", value))
    return re.sub(r"[ \t]*\n\s*", "\n", value).strip()


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def archive_path(base, href):
    path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
    if path.startswith("/") or "\\" in path:
        raise ValueError("Invalid EPUB resource path")
    result = posixpath.normpath(posixpath.join(base, path))
    if result in ("", ".", "..") or result.startswith("../"):
        raise ValueError("Invalid EPUB resource path")
    return result


def book_path(name):
    root = os.path.realpath(BOOK_ROOT)
    path = os.path.realpath(os.path.join(root, name))
    if (os.path.commonpath([root, path]) != root or
            not path.lower().endswith(".epub") or not os.path.isfile(path)):
        raise ValueError("Book not found")
    return path


def resource_url(book, path):
    return "/epub-content/" + urllib.parse.quote(book, safe="") + "/" + urllib.parse.quote(path, safe="/")


def series_metadata(metadata):
    metas = [element for element in metadata if local_name(element.tag) == "meta"]
    for collection in metas:
        if collection.get("property") != "belongs-to-collection" or not collection.get("id"):
            continue
        refinements = [element for element in metas if element.get("refines") == "#" + collection.get("id")]
        if not any(element.get("property") == "collection-type" and
                   (element.text or "").strip().lower() == "series" for element in refinements):
            continue
        title = "".join(collection.itertext()).strip()
        position = next(((element.text or "").strip() for element in refinements
                         if element.get("property") == "group-position"), "")
        if title:
            return title, position
    title = next((element.get("content", "").strip() for element in metas
                  if element.get("name", "").lower() == "calibre:series"), "")
    position = next((element.get("content", "").strip() for element in metas
                     if element.get("name", "").lower() == "calibre:series_index"), "")
    return title, position


def volume_name(value):
    value = unicodedata.normalize("NFKC", value).strip()
    match = re.match(r"^(.+?)(?:[\s._-]+vol(?:ume)?\.?[\s._-]*|[\s._-]*#\s*|[\s._-]*第\s*)(\d+(?:\.\d+)?)(?:\s*巻)?$", value, re.IGNORECASE)
    if not match:
        match = re.match(r"^(.+?)[\s._-]+(\d+(?:\.\d+)?)$", value)
    if not match:
        return None, None
    title = match.group(1).rstrip(" ._-")
    return (title, float(match.group(2))) if title else (None, None)


def volume_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def standalone_volume(value):
    value = unicodedata.normalize("NFKC", value).strip()
    match = re.fullmatch(r"(?:vol(?:ume)?\.?[\s._-]*|第\s*|#\s*)?(\d+(?:\.\d+)?)(?:\s*巻)?", value, re.IGNORECASE)
    return float(match.group(1)) if match else None


def annotate_series(books):
    filename_groups = {}
    for book in books:
        series_title, series_position = book.pop("_series_metadata")
        filename = os.path.splitext(posixpath.basename(book["id"]))[0]
        title_base, title_number = volume_name(book["title"])
        file_base, file_number = volume_name(filename)
        book["volume"] = volume_number(series_position)
        if book["volume"] is None:
            book["volume"] = title_number if title_number is not None else file_number
        if book["volume"] is None and (series_title or posixpath.dirname(book["id"])):
            book["volume"] = standalone_volume(filename)
        book["series"] = None
        book["seriesKey"] = None
        if posixpath.dirname(book["id"]):
            folder = posixpath.dirname(book["id"])
            book["series"] = posixpath.basename(folder)
            book["seriesKey"] = "folder:" + folder
        elif series_title:
            book["series"] = series_title
            book["seriesKey"] = "metadata:" + unicodedata.normalize("NFKC", series_title).casefold()
        elif title_base or file_base:
            candidate = title_base or file_base
            key = unicodedata.normalize("NFKC", candidate).casefold()
            filename_groups.setdefault(key, []).append((book, candidate))
    for key, candidates in filename_groups.items():
        if len({book["volume"] for book, _ in candidates}) < 2:
            continue
        for book, title in candidates:
            book["series"] = title
            book["seriesKey"] = "filename:" + key


def read_book(name):
    with zipfile.ZipFile(book_path(name)) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next((e.get("full-path") for e in container.iter()
                         if local_name(e.tag) == "rootfile" and e.get("full-path")), None)
        if not rootfile:
            raise ValueError("EPUB has no package document")
        rootfile = archive_path("", rootfile)
        package = ET.fromstring(archive.read(rootfile))
        base = posixpath.dirname(rootfile)
        metadata = next((e for e in package if local_name(e.tag) == "metadata"), None)
        manifest = next((e for e in package if local_name(e.tag) == "manifest"), None)
        spine = next((e for e in package if local_name(e.tag) == "spine"), None)
        if metadata is None or manifest is None or spine is None:
            raise ValueError("EPUB package is missing metadata, manifest, or spine")

        def metadata_text(key):
            return next(("".join(e.itertext()).strip() for e in metadata
                         if local_name(e.tag) == key and "".join(e.itertext()).strip()), "")

        items = {}
        for item in manifest:
            if local_name(item.tag) != "item" or not item.get("id") or not item.get("href"):
                continue
            items[item.get("id")] = {
                "path": archive_path(base, item.get("href")),
                "type": item.get("media-type", ""),
                "properties": item.get("properties", "").split(),
            }

        labels = {}
        warnings = []
        nav = next((v for v in items.values() if "nav" in v["properties"]), None)
        ncx = items.get(spine.get("toc"))
        navigation = nav or ncx
        if navigation:
            try:
                nav_root = ET.fromstring(archive.read(navigation["path"]))
                nav_base = posixpath.dirname(navigation["path"])
                if nav:
                    for link in nav_root.iter():
                        if local_name(link.tag) == "a" and link.get("href"):
                            label = " ".join("".join(link.itertext()).split())
                            if label:
                                labels.setdefault(archive_path(nav_base, link.get("href")), label)
                else:
                    for point in nav_root.iter():
                        if local_name(point.tag) != "navPoint":
                            continue
                        content = next((e for e in point if local_name(e.tag) == "content"), None)
                        label = next((e for e in point if local_name(e.tag) == "navLabel"), None)
                        if content is not None and label is not None and content.get("src"):
                            labels.setdefault(archive_path(nav_base, content.get("src")),
                                              " ".join("".join(label.itertext()).split()))
            except (KeyError, ET.ParseError, ValueError) as exc:
                warnings.append(f"Could not read the table of contents: {exc}")

        sections = []
        for ref in spine:
            item = items.get(ref.get("idref"))
            if item and item["type"] in HTML_TYPES and ref.get("linear") != "no":
                sections.append({"path": item["path"], "title": labels.get(item["path"]) or
                                 f"Section {len(sections) + 1}"})
        if not sections:
            raise ValueError("EPUB contains no readable sections")
        cover = next((v for v in items.values() if "cover-image" in v["properties"]), None)
        if not cover:
            cover_id = next((e.get("content") for e in metadata
                             if local_name(e.tag) == "meta" and e.get("name") == "cover"), None)
            cover = items.get(cover_id)
        if cover and not cover["type"].startswith("image/"):
            cover = None
        return {
            "id": name, "title": metadata_text("title") or os.path.splitext(os.path.basename(name))[0],
            "author": metadata_text("creator"), "description": plain_text(metadata_text("description")), "cover": resource_url(name, cover["path"]) if cover else None,
            "sections": sections, "warnings": warnings, "_series_metadata": series_metadata(metadata),
        }


def send_bytes(handler, data, content_type, *, epub=False):
    nonce = None
    if epub and content_type.startswith("text/html"):
        nonce = secrets.token_urlsafe(24)
        with open(os.path.join(APP_DIR, "frame.js"), "rb") as file:
            bridge = file.read()
        data += b'\n<script nonce="' + nonce.encode("ascii") + b'">' + bridge + b'</script>'
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    if epub:
        scripts = f"'nonce-{nonce}'" if nonce else "'none'"
        sandbox = "allow-scripts allow-same-origin" if getattr(handler.server, "isolated_epub", False) else "allow-scripts"
        parent_port = getattr(handler.server, "epub_parent_port", None)
        # The library page is on the same host name as this request (LocalOnly already vetted it).
        host = urllib.parse.urlsplit("//" + handler.headers.get("Host", "")).hostname or "localhost"
        host = f"[{host}]" if ":" in host else host
        ancestors = (" ".join(f"http://{name}:{parent_port}"
                              for name in dict.fromkeys(("127.0.0.1", "localhost", host)))
                     if parent_port else "'self'")
        handler.send_header("Content-Security-Policy",
                            f"default-src 'none'; script-src {scripts}; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; frame-src moz-extension: chrome-extension:; frame-ancestors {ancestors}; sandbox {sandbox}")
        handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(data)


def send_json(handler, obj):
    send_bytes(handler, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")


def handle_request(handler, parsed):
    if parsed.path in ("/books", "/books/"):
        with open(os.path.join(APP_DIR, "index.html"), "rb") as f:
            send_bytes(handler, f.read(), "text/html; charset=utf-8")
        return True
    static = {"/books/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/books/style.css": ("style.css", "text/css; charset=utf-8")}
    if parsed.path in static:
        name, content_type = static[parsed.path]
        with open(os.path.join(APP_DIR, name), "rb") as f:
            send_bytes(handler, f.read(), content_type)
        return True
    if parsed.path == "/api/books":
        books, errors = [], []
        if not os.path.isdir(BOOK_ROOT):
            handler.send_error(500, "Book folder does not exist")
            return True
        for directory, _, files in os.walk(BOOK_ROOT):
            for filename in files:
                if not filename.lower().endswith(".epub"):
                    continue
                name = os.path.relpath(os.path.join(directory, filename), BOOK_ROOT).replace(os.sep, "/")
                try:
                    books.append(read_book(name))
                except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
                    errors.append({"file": name, "error": str(exc)})
        annotate_series(books)
        books.sort(key=lambda book: book["title"].casefold())
        send_json(handler, {"books": books, "errors": errors,
                            "contentPort": getattr(handler.server, "epub_content_port", None)})
        return True
    if parsed.path == "/api/book-character-count":
        names = urllib.parse.parse_qs(parsed.query).get("book", [])
        if len(names) != 1:
            handler.send_error(400, "Expected one book")
            return True
        try:
            result = count_book_characters(names[0])
        except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
            handler.log_error("Could not count book characters: %s", exc)
            handler.send_error(422, "Could not count book characters")
            return True
        send_json(handler, result)
        return True
    if parsed.path.startswith("/epub-content/"):
        parts = parsed.path[len("/epub-content/"):].split("/", 1)
        if len(parts) != 2:
            handler.send_error(404)
            return True
        try:
            name = urllib.parse.unquote(parts[0])
            path = archive_path("", parts[1])
            content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if content_type in ("application/xhtml+xml", "text/html"):
                content_type = "text/html; charset=utf-8"
            elif content_type.startswith("text/"):
                content_type += "; charset=utf-8"
            with zipfile.ZipFile(book_path(name)) as archive:
                data = archive.read(path)
            send_bytes(handler, data, content_type, epub=True)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            handler.send_error(404, "Book resource not found")
        return True
    return False
