"""HTTP server for the manga library and EPUB reader: request handlers and the command line."""

import argparse
import filecmp
import http.server
import io
import ipaddress
import socketserver
import os
import re
import json
import mimetypes
import threading
import zipfile
import urllib.parse
import sys
import webbrowser

from . import PROJECT_DIR, config, epub_library, furigana, jiten, known_words

ROOT = os.path.expanduser(config.DEFAULTS["manga"])  # these three are set from config.json by configure()
SKIP_MANGA_FOLDERS = set()
JITEN = True
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp"}
MANGA_LIBRARY_PATH = os.path.join(PROJECT_DIR, "web", "manga", "index.html")
SETTINGS_PAGE_PATH = os.path.join(PROJECT_DIR, "web", "settings", "index.html")
CONFIG_PATH = None  # the --config file, else config.PATH
# What the settings page may change while the server runs; port and network settings need a restart.
LIVE_SETTINGS = {"manga", "books", "skipMangaFolders", "jiten", "anki"}
_picking = threading.Lock()
# Mokuro shows each OCR line as a display:table <p>, which Yomitan's layout-aware scan (on by default)
# treats as a line break: words split across lines can't be looked up and mined sentences stop at the
# line. Inline lines with a CSS line break look the same but read as one run of text per speech bubble.
MOKURO_PAGE_MARKER = b" | mokuro</title>"
MOKURO_LINE_CSS = (b"\n<style>.textBox:hover p, .hovered p { display: inline; }\n"
                   b'.textBox p::after { content: "\\A"; white-space: pre; }</style>\n')


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def safe_join(rel):
    rel = rel.strip("/")  # already decoded by parse_qs; decoding again breaks names like "100%BA"
    target = os.path.normpath(os.path.join(ROOT, rel))
    if not (target == ROOT or target.startswith(ROOT + os.sep)):
        return None
    return target


def find_first_volume(series_dir):
    """Return (path, is_dir) for the naturally-first volume in a series dir."""
    try:
        names = [n for n in os.listdir(series_dir) if n != "_ocr"]
    except OSError:
        return None
    stems = {}
    for n in names:
        full = os.path.join(series_dir, n)
        is_dir = os.path.isdir(full)
        stem = n if is_dir else os.path.splitext(n)[0]
        pref = 0 if is_dir else 1
        if stem not in stems or pref < stems[stem][0]:
            stems[stem] = (pref, n, is_dir)
    if not stems:
        return None
    first_stem = sorted(stems.keys(), key=natural_key)[0]
    _, name, is_dir = stems[first_stem]
    return os.path.join(series_dir, name), is_dir


def find_first_image_in_dir(d):
    found = []
    for root, _, files in os.walk(d):
        for f in files:
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                found.append(os.path.join(root, f))
    if not found:
        return None
    found.sort(key=lambda p: natural_key(os.path.relpath(p, d)))
    return found[0]


def find_first_image_in_zip(zpath):
    try:
        with zipfile.ZipFile(zpath) as zf:
            names = [
                n for n in zf.namelist()
                if not n.endswith("/") and os.path.splitext(n)[1].lower() in IMAGE_EXTS
            ]
            if not names:
                return None
            names.sort(key=natural_key)
            return names[0]
    except zipfile.BadZipFile:
        return None


class LocalOnly:
    """Refuse requests addressed to another hostname.

    Without this, a web page can point its own domain at 127.0.0.1 (DNS
    rebinding) and read the library through the visitor's browser. With --lan
    the server also answers requests addressed to an IP address (a rebinding
    page cannot use one) and to names given with --allow-host.
    """

    def host_allowed(self, hostname):
        if hostname in ("127.0.0.1", "localhost") or hostname in getattr(self.server, "allowed_hosts", ()):
            return True
        if not getattr(self.server, "lan", False):
            return False
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            return False
        return True

    def parse_request(self):
        if not super().parse_request():
            return False
        try:
            hostname = urllib.parse.urlsplit("//" + self.headers.get("Host", "")).hostname
        except ValueError:
            hostname = None
        if self.host_allowed(hostname):
            return True
        self.send_error(403, "This server only answers requests for 127.0.0.1, localhost, "
                             "or (with --lan) an IP address or a name given with --allow-host")
        return False


class Handler(LocalOnly, http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def send_head(self):
        path = urllib.parse.urlsplit(self.path).path
        page = None
        if path in ("/", "/index.html") and not os.path.isfile(os.path.join(ROOT, "index.html")):
            page = MANGA_LIBRARY_PATH
        elif path in ("/settings", "/settings/"):
            page = SETTINGS_PAGE_PATH
        if page:
            file = None
            try:
                file = open(page, "rb")
                size = os.fstat(file.fileno()).st_size
            except OSError as error:
                if file is not None:
                    file.close()
                self.log_error("Cannot open %s: %s", page, error)
                self.send_error(500, "Library page unavailable")
                return None
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return file
        if path.lower().endswith(".html"):
            page = self.mokuro_page()
            if page is not None:
                return page
        return super().send_head()

    def mokuro_page(self):
        """Send a Mokuro volume page with MOKURO_LINE_CSS appended; None means serve the file unchanged."""
        try:
            with open(self.translate_path(self.path), "rb") as file:
                data = file.read()
        except OSError:
            return None  # the normal handler reports the missing or unreadable file
        if data.find(MOKURO_PAGE_MARKER, 0, 4096) < 0:
            return None
        data += MOKURO_LINE_CSS  # after Mokuro's own styles, so these rules win
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        return io.BytesIO(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if epub_library.handle_request(self, parsed):
            return
        if parsed.path == "/api/listing":
            self.handle_listing(parsed.query)
            return
        if parsed.path == "/api/thumbnail":
            self.handle_thumbnail(parsed.query)
            return
        if parsed.path == "/api/difficulty":
            self.handle_difficulty(parsed.query)
            return
        if parsed.path == "/api/known-words":
            self.handle_known_words(parsed.query)
            return
        if parsed.path == "/api/settings":
            self.send_settings()
            return
        super().do_GET()

    def from_this_computer(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def send_settings(self, settings=None):
        settings = settings or config.load(CONFIG_PATH)
        self._send_json({"settings": {key: value for key, value in settings.items() if key in LIVE_SETTINGS},
                         "active": {"manga": ROOT, "books": epub_library.BOOK_ROOT},
                         "configFile": os.path.abspath(CONFIG_PATH or config.PATH),
                         "canChange": self.from_this_computer()})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path not in ("/api/furigana", "/api/word-status", "/api/settings", "/api/pick-folder"):
            self.send_error(404)
            return
        if path in ("/api/settings", "/api/pick-folder"):
            # Only this computer may change where the server reads files from, also with --lan;
            # the Origin check refuses other websites (the JSON content type already forces a preflight).
            origin = self.headers.get("Origin")
            if not self.from_this_computer() or (origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host")):
                self.send_error(403, "Settings can only be changed on the computer running the reader")
                return
        if self.headers.get_content_type() != "application/json":
            self.send_error(415, "Expected JSON")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if length < 0 or length > 1048576:
            self.send_error(413, "Request is too large")
            return
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400, "Invalid JSON")
            return
        if path == "/api/settings":
            self.change_settings(request)
            return
        if path == "/api/pick-folder":
            self.pick_folder(request)
            return
        texts = request.get("texts") if isinstance(request, dict) else None
        if (not isinstance(texts, list) or len(texts) > 200 or
                any(not isinstance(text, str) or len(text) > 20000 for text in texts) or
                sum(len(text) for text in texts) > 60000):
            self.send_error(400, "Invalid furigana text batch")
            return
        extra = {}
        try:
            if path == "/api/furigana":
                annotate = furigana.annotate
            else:
                words = known_words.anki_words()
                if words is None:
                    self._send_json({"results": None, "ankiOffline": True})
                    return
                annotate = known_words.annotator(words)
                extra = {"ankiOffline": words["offline"]}
            result = [annotate(text) for text in texts]
        except furigana.FuriganaUnavailable as error:
            self.log_error("Furigana unavailable: %s", error)
            self.send_error(503, str(error))
            return
        except (RuntimeError, ValueError) as error:
            self.log_error("Furigana generation failed: %s", error)
            self.send_error(500, "Furigana generation failed")
            return
        self._send_json({"results": result, **extra})

    def change_settings(self, changes):
        if not isinstance(changes, dict) or not set(changes) <= LIVE_SETTINGS:
            self.send_error(400, "Only folder, Jiten and Anki settings can be changed here")
            return
        for key in ("manga", "books"):
            folder = changes.get(key)
            if isinstance(folder, str) and not os.path.isdir(os.path.expanduser(folder)):
                self._send_json({"error": f"The {key} folder does not exist: {folder}"}, 400)
                return
        try:
            settings = config.save(changes, CONFIG_PATH)
        except config.ConfigError as error:
            self._send_json({"error": str(error)}, 400)
            return
        configure(settings)
        known_words.forget_anki_words()  # another AnkiConnect address or search means another word list
        self.send_settings(settings)

    def pick_folder(self, request):
        start = request.get("start") if isinstance(request, dict) else None
        if not _picking.acquire(blocking=False):
            self._send_json({"error": "A folder window is already open; it may be behind the browser."}, 409)
            return
        try:
            folder = choose_folder(start if isinstance(start, str) and os.path.isdir(start) else None)
        except RuntimeError as error:
            self._send_json({"error": str(error)}, 503)
            return
        finally:
            _picking.release()
        self._send_json({"path": folder})

    def handle_listing(self, query):
        qs = urllib.parse.parse_qs(query)
        target = safe_join(qs.get("path", [""])[0])
        if target is None:
            self.send_error(403)
            return
        if not os.path.isdir(target):
            self.send_error(404)
            return
        try:
            entries = [
                {"name": name, "isDir": os.path.isdir(os.path.join(target, name))}
                for name in os.listdir(target) if target != ROOT or name not in SKIP_MANGA_FOLDERS
            ]
        except OSError:
            self.send_error(500)
            return
        self._send_json(entries)

    def handle_thumbnail(self, query):
        qs = urllib.parse.parse_qs(query)
        series_dir = safe_join(qs.get("series", [""])[0])
        if series_dir is None or not os.path.isdir(series_dir):
            self.send_error(404)
            return

        vol = find_first_volume(series_dir)
        if vol is None:
            self.send_error(404)
            return
        vol_path, is_dir = vol

        try:
            if is_dir:
                name = find_first_image_in_dir(vol_path)
                if name is not None:
                    with open(name, "rb") as file:
                        data = file.read()
            else:
                name = find_first_image_in_zip(vol_path)
                if name is not None:
                    with zipfile.ZipFile(vol_path) as zf:
                        data = zf.read(name)
        except (OSError, zipfile.BadZipFile, KeyError) as error:
            # Volumes can be locked or half-written while Mokuro is processing them.
            self.log_error("Cannot read a cover from %s: %s", vol_path, error)
            name = None
        if name is None:
            self.send_error(404)
            return
        self._send_file_bytes(data, name)

    def handle_difficulty(self, query):
        qs = urllib.parse.parse_qs(query)
        title = qs.get("title", [""])[0]
        kind = qs.get("type", ["manga"])[0]
        if not title or kind not in jiten.JITEN_MEDIA_TYPES:
            self.send_error(400)
            return
        if not JITEN:
            self.send_error(404, "Jiten lookups are turned off in config.json")
            return
        try:
            result = jiten.get_difficulty(title, kind)
        except jiten.JitenLookupError as error:
            self.log_error("Jiten lookup failed: %s", error)
            self.send_error(502, "Jiten lookup failed")
            return
        except OSError as error:
            self.log_error("Difficulty cache failed: %s", error)
            self.send_error(500, "Difficulty cache unavailable")
            return
        self._send_json(result)

    def handle_known_words(self, query):
        name = urllib.parse.parse_qs(query).get("series", [""])[0]
        series_dir = safe_join(name) if name else None
        if series_dir is None or not os.path.isdir(series_dir):
            self.send_error(404)
            return
        words = known_words.anki_words()
        if words is None:
            self._send_json({"ankiOffline": True})
            return
        try:
            files = sorted((f for f in os.listdir(series_dir) if f.lower().endswith(".mokuro")), key=natural_key)
        except OSError as error:
            self.log_error("Cannot list %s: %s", series_dir, error)
            self.send_error(500)
            return
        volumes, series_types = [], []
        for file in files:
            try:
                types = known_words.volume_types(os.path.join(series_dir, file))
            except furigana.FuriganaUnavailable as error:
                self.send_error(503, str(error))
                return
            except (OSError, ValueError) as error:  # half-written by Mokuro, or not a Mokuro file
                self.log_error("Cannot count the words in %s: %s", file, error)
                continue
            volumes.append({"name": file[:-len(".mokuro")], **known_words.count_known(types, words)})
            series_types += types
        self._send_json({"ankiOffline": words["offline"], "wordsAt": words["at"], "knownWords": words["knownCount"],
                         "knownInterval": known_words.KNOWN_INTERVAL,
                         "series": known_words.count_known(series_types, words), "volumes": volumes})

    def _send_file_bytes(self, data, name_for_type):
        ctype = mimetypes.guess_type(name_for_type)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

    def log_error(self, format, *args):
        print(f"Server: {format % args}", file=sys.stderr)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def handle_error(self, request, client_address):
        # The browser hung up first (page reloaded or closed); anything slow was already cached.
        if not isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


class EpubContentHandler(LocalOnly, http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/epub-content/") and epub_library.handle_request(self, parsed):
            return
        self.send_error(404)

    def log_message(self, format, *args):
        pass


def stale_manga_override():
    """Path of a manga-folder index.html that hides a different bundled library page, else None."""
    override = os.path.join(ROOT, "index.html")
    try:
        if os.path.isfile(override) and not filecmp.cmp(override, MANGA_LIBRARY_PATH, shallow=False):
            return override
    except OSError:
        pass
    return None


def port_number(value):
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be a number") from error
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def choose_folder(start=None):
    """Show the system's folder window on this computer; the chosen folder, or None if cancelled."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError as error:
        raise RuntimeError("This Python has no tkinter, so folders can't be picked here; type the path instead.") from error
    try:
        root = tkinter.Tk()
    except tkinter.TclError as error:
        raise RuntimeError(f"Could not open a folder window: {error}. Type the path instead.") from error
    try:
        root.withdraw()
        root.attributes("-topmost", True)  # in front of the browser
        folder = filedialog.askdirectory(parent=root, initialdir=start, mustexist=True, title="Choose a folder")
    finally:
        root.destroy()
    return os.path.normpath(folder) if folder else None


def configure(settings):
    """Use settings from config.load(): folders, manga folders to leave out, Jiten and Anki."""
    global ROOT, SKIP_MANGA_FOLDERS, JITEN
    ROOT, epub_library.BOOK_ROOT = settings["manga"], settings["books"]
    SKIP_MANGA_FOLDERS, JITEN = set(settings["skipMangaFolders"]), settings["jiten"]
    known_words.configure(settings["anki"])


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(prog="python -m reader", description="Run Hondana, the local manga and EPUB library.")
    parser.add_argument("legacy_port", nargs="?", type=port_number, help="port number (legacy form)")
    parser.add_argument("--port", type=port_number, help='port number (default: "port" in config.json, else 8421)')
    parser.add_argument("--manga", metavar="FOLDER", help='manga library folder (default: "manga" in config.json)')
    parser.add_argument("--books", metavar="FOLDER", help='EPUB library folder (default: "books" in config.json)')
    parser.add_argument("--config", metavar="FILE", help="settings file (default: config.json in the project folder)")
    parser.add_argument("--open", action=argparse.BooleanOptionalAction, default=False,
                        help="open the library in the default browser")
    parser.add_argument("--lan", action="store_true",
                        help="listen on all network interfaces so other devices can connect (there is no login)")
    parser.add_argument("--content-port", type=port_number,
                        help="port for isolated EPUB content (default: random, or the main port + 1 with --lan)")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME", type=str.lower,
                        help="also answer requests addressed to this hostname; repeat for several")
    args = parser.parse_args(argv)
    if args.port is not None and args.legacy_port is not None:
        parser.error("use either --port or the positional port, not both")
    try:
        args.settings = config.load(args.config)
    except config.ConfigError as error:
        parser.error(str(error))
    port = args.port if args.port is not None else args.legacy_port
    args.port = port if port is not None else args.settings["port"]
    args.lan = args.lan or args.settings["lan"]
    args.allow_host = [name.lower() for name in args.settings["allowHosts"]] + args.allow_host
    if args.content_port is None:
        args.content_port = args.port + 1 if args.lan and 0 < args.port < 65535 else 0
    for name in ("manga", "books"):
        value = getattr(args, name)
        if value is not None:
            value = os.path.abspath(os.path.expanduser(value))
            if not os.path.isdir(value):
                parser.error(f"{name} folder does not exist: {value}")
            setattr(args, name, value)
    return args


def main(argv=None):
    global ROOT
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is required.", file=sys.stderr)
        return 1
    global CONFIG_PATH
    args = parse_arguments(argv)
    CONFIG_PATH = args.config
    configure(args.settings)
    if args.manga is not None:
        ROOT = args.manga
    if args.books is not None:
        epub_library.BOOK_ROOT = args.books
    for name, folder in (("Manga", ROOT), ("Books", epub_library.BOOK_ROOT)):
        if not os.path.isdir(folder):
            print(f"Warning: {name} folder does not exist: {folder}", file=sys.stderr)
            print(f'Set "{name.lower()}" in config.json or use --{name.lower()} to select an existing folder.',
                  file=sys.stderr)
    override = stale_manga_override()
    if override:
        print(f"Note: {override} is shown instead of the bundled manga library page and differs from it.\n"
              "Delete it to get the bundled page and its updates.", file=sys.stderr)
    bind = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        server = ThreadingHTTPServer((bind, args.port), Handler)
    except OSError as error:
        print(f"Could not start the server on port {args.port}: {error}", file=sys.stderr)
        print("If the port is in use, stop the other server or choose --port 8422.", file=sys.stderr)
        return 1
    try:
        content_server = ThreadingHTTPServer((bind, args.content_port), EpubContentHandler)
    except OSError as error:
        server.server_close()
        print(f"Could not start the isolated EPUB content server: {error}", file=sys.stderr)
        return 1
    for s in (server, content_server):
        s.lan, s.allowed_hosts = args.lan, args.allow_host
    content_server.isolated_epub = True
    content_server.epub_parent_port = server.server_address[1]
    server.epub_content_port = content_server.server_address[1]
    with server, content_server:
        content_thread = threading.Thread(target=content_server.serve_forever, daemon=True)
        content_thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        print(f"Manga: {ROOT}\nBooks: {epub_library.BOOK_ROOT}\nLibrary: {url}", flush=True)
        if args.lan:
            print(f"LAN mode: other devices can use http://<this computer's address>:{server.server_address[1]}/ "
                  f"(allow TCP ports {server.server_address[1]} and {content_server.server_address[1]} "
                  "in the firewall).\nThere is no login: anyone who can reach those ports can read your library.",
                  flush=True)
        print("Press Ctrl+C to stop the server.", flush=True)
        if args.open:
            try:
                if not webbrowser.open(url):
                    print(f"Could not open a browser automatically. Open {url} manually.", file=sys.stderr)
            except (webbrowser.Error, OSError) as error:
                print(f"Could not open a browser: {error}. Open {url} manually.", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
        finally:
            content_server.shutdown()
            content_thread.join()
    return 0
