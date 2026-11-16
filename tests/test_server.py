import contextlib
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from reader import config, jiten, jpdb, known_words, lookups, natively, server, titles, web


class ServerConfigurationTests(unittest.TestCase):
    def setUp(self):
        # The tests must not depend on the config.json of whoever runs them.
        patch = mock.patch.object(config, "PATH", os.path.join(tempfile.gettempdir(), "no-such-reader-config.json"))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(server.configure, config.load())  # main() configures the modules; put the defaults back

    def write_config(self, folder, settings):
        path = os.path.join(folder, "config.json")
        with open(path, "w", encoding="utf-8") as file:
            json.dump(settings, file)
        return path

    def test_config_file_sets_folders_port_anki_and_more(self):
        with tempfile.TemporaryDirectory() as folder:
            os.mkdir(os.path.join(folder, "manga"))
            path = self.write_config(folder, {
                "manga": "manga", "books": "~/Bücher", "port": 9100, "lan": True, "allowHosts": ["PC.ts.net"],
                "skipMangaFolders": ["Duplicate"], "jiten": False,
                "anki": {"url": "http://127.0.0.1:9000", "search": "deck:Japanese", "knownInterval": 30,
                         "wordFields": {"My notes": "Front"}},
            })
            args = server.parse_arguments(["--config", path])
            self.assertEqual((args.port, args.content_port, args.lan, args.allow_host), (9100, 9101, True, ["pc.ts.net"]))
            self.assertEqual(args.settings["manga"], os.path.join(folder, "manga"))  # relative to the config file
            self.assertEqual(args.settings["books"], os.path.join(os.path.expanduser("~"), "Bücher"))
            self.assertEqual(server.parse_arguments(["--config", path, "--port", "9200"]).port, 9200)  # options win
            server.configure(args.settings)
            self.assertEqual((server.ROOT, server.SKIP_MANGA_FOLDERS, lookups.ENABLED["jiten"]), (args.settings["manga"], {"Duplicate"}, False))
            self.assertEqual((known_words.ANKI_URL, known_words.KNOWN_INTERVAL), ("http://127.0.0.1:9000", 30))
            self.assertEqual(known_words.anki_queries()["known"], 'deck:Japanese ("note:My notes") -is:new prop:ivl>=30')
            self.assertEqual(config.load(path)["anki"]["wordListFields"], ["Jlab-Lemma"])  # unset keys keep defaults

    def test_bad_config_is_reported_clearly(self):
        with tempfile.TemporaryDirectory() as folder:
            for settings, message in (({"mangaa": "x"}, "unknown setting 'mangaa'"),
                                      ({"port": "8421"}, "'port' must be a whole number"),
                                      ({"anki": {"url": "127.0.0.1:9000"}}, "'url' must start with http"),
                                      ({"anki": {"wordFields": {}}}, "'wordFields' must map"),
                                      ({"skipMangaFolders": "Duplicate"}, "must be a list"),
                                      ([], "must be a JSON object")):
                with self.subTest(settings=settings):
                    with self.assertRaisesRegex(config.ConfigError, message):
                        config.load(self.write_config(folder, settings))
            with open(os.path.join(folder, "config.json"), "w", encoding="utf-8") as file:
                file.write('{"port": 84')
            with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
                server.parse_arguments(["--config", os.path.join(folder, "config.json")])
            self.assertIn("Could not read", error.getvalue())

    def test_skipped_manga_folders_and_jiten_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("Series", "Duplicate"):
                os.mkdir(os.path.join(folder, name))
                os.mkdir(os.path.join(folder, name, "Duplicate"))
            server.configure({**config.load(), "manga": folder, "skipMangaFolders": ["Duplicate"], "jiten": False})
            with server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler) as httpd, \
                    contextlib.redirect_stderr(io.StringIO()):
                thread = threading.Thread(target=httpd.serve_forever)
                thread.start()
                try:
                    def get(path):
                        connection = http.client.HTTPConnection(*httpd.server_address)
                        connection.request("GET", path)
                        response = connection.getresponse()
                        result = response.status, response.read()
                        connection.close()
                        return result
                    self.assertEqual(json.loads(get("/api/listing?path=")[1]), [{"name": "Series", "isDir": True}])
                    # Only the library's own top level is filtered.
                    self.assertEqual(json.loads(get("/api/listing?path=Series")[1]), [{"name": "Duplicate", "isDir": True}])
                    with mock.patch.object(jiten, "search") as lookup:
                        status, body = get("/api/lookup?source=jiten&type=manga&title=Series")
                    self.assertEqual((status, "turned off" in json.loads(body)["error"]), (502, True))
                    lookup.assert_not_called()
                    self.assertEqual(json.loads(get("/api/features")[1])["lookups"]["jiten"], False)
                finally:
                    httpd.shutdown()
                    thread.join()

    def test_settings_page_saves_and_applies_folders_without_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("old", "new", "books"):
                os.mkdir(os.path.join(folder, name))
            os.mkdir(os.path.join(folder, "new", "Series"))
            path = self.write_config(folder, {"manga": "old", "anki": {"wordFields": {"Mine": "Word"}}})
            server.configure(config.load(path))
            with mock.patch.object(server, "CONFIG_PATH", path), \
                    server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler) as httpd, \
                    contextlib.redirect_stderr(io.StringIO()):
                thread = threading.Thread(target=httpd.serve_forever)
                thread.start()
                try:
                    def call(method, url, body=None, headers=None):
                        connection = http.client.HTTPConnection(*httpd.server_address)
                        connection.request(method, url, json.dumps(body) if body is not None else None,
                                           {"Content-Type": "application/json", **(headers or {})})
                        response = connection.getresponse()
                        result = response.status, response.read()
                        connection.close()
                        return result

                    self.assertEqual(call("GET", "/settings/")[0], 200)
                    status, body = call("GET", "/api/settings")
                    self.assertEqual((status, json.loads(body)["canChange"]), (200, True))
                    self.assertEqual(json.loads(call("GET", "/api/listing?path=")[1]), [])
                    new = os.path.join(folder, "new")
                    status, body = call("POST", "/api/settings", {"manga": new, "anki": {"url": "http://127.0.0.1:9000"}})
                    self.assertEqual(status, 200, body)
                    # The next request already reads the new folder.
                    self.assertEqual(json.loads(call("GET", "/api/listing?path=")[1]), [{"name": "Series", "isDir": True}])
                    self.assertEqual(known_words.ANKI_URL, "http://127.0.0.1:9000")
                    with open(path, encoding="utf-8") as file:
                        saved = json.load(file)
                    self.assertEqual(saved, {"manga": new, "anki": {"wordFields": {"Mine": "Word"}, "url": "http://127.0.0.1:9000"}})
                    # Nothing is saved when something is wrong.
                    status, body = call("POST", "/api/settings", {"books": os.path.join(folder, "missing")})
                    self.assertEqual((status, json.loads(body)["error"].startswith("The books folder does not exist")), (400, True))
                    self.assertEqual(call("POST", "/api/settings", {"anki": {"knownInterval": 0}})[0], 400)
                    self.assertEqual(call("POST", "/api/settings", {"port": 9999})[0], 400)  # needs a restart
                    with open(path, encoding="utf-8") as file:
                        self.assertEqual(json.load(file), saved)
                    # An API key is saved but never sent back; "" removes it. No books folder is allowed.
                    with mock.patch.object(known_words, "forget_anki_words"):
                        status, body = call("POST", "/api/settings", {"apiKeys": {"jpdb": " secret "}, "books": "",
                                                                      "gradedReaders": False})
                        self.assertEqual(status, 200, body)
                        shown = json.loads(body)["settings"]
                        self.assertEqual((shown["apiKeys"], shown["books"], shown["gradedReaders"]),
                                         ({"jiten": False, "jpdb": True}, "", False))
                        self.assertNotIn(b"secret", call("GET", "/api/settings")[1])
                        self.assertEqual((known_words.JPDB_KEY, jpdb.API_KEY, server.epub_library.SHOW_GRADED),
                                         ("secret", "secret", False))
                        self.assertEqual(call("POST", "/api/settings", {"apiKeys": {"jiten": "j"}})[0], 200)
                        self.assertEqual((jiten.API_KEY, jpdb.API_KEY), ("j", "secret"))  # the other key is kept
                        self.assertEqual(call("POST", "/api/settings", {"apiKeys": {"jpdb": "", "jiten": ""}})[0], 200)
                        self.assertEqual((jiten.API_KEY, jpdb.API_KEY), ("", ""))
                    # Another website, or another device in LAN mode, may not change settings.
                    self.assertEqual(call("POST", "/api/settings", {"jiten": False}, {"Origin": "http://evil.example"})[0], 403)
                    with mock.patch.object(server.Handler, "from_this_computer", return_value=False):
                        self.assertEqual(call("POST", "/api/settings", {"jiten": False})[0], 403)
                        self.assertEqual(call("POST", "/api/pick-folder", {})[0], 403)
                        self.assertFalse(json.loads(call("GET", "/api/settings")[1])["canChange"])
                    with mock.patch.object(server, "choose_folder", return_value=new) as choose:
                        self.assertEqual(json.loads(call("POST", "/api/pick-folder", {"start": folder})[1]), {"path": new})
                    choose.assert_called_once_with(folder)
                    with mock.patch.object(server, "choose_folder", side_effect=RuntimeError("no tkinter")):
                        self.assertEqual(call("POST", "/api/pick-folder", {}), (503, b'{"error": "no tkinter"}'))
                finally:
                    httpd.shutdown()
                    thread.join()

    def test_defaults_and_legacy_port(self):
        self.assertEqual(server.parse_arguments([]).port, 8421)
        self.assertEqual(server.parse_arguments(["9000"]).port, 9000)
        self.assertEqual(server.parse_arguments(["--port", "0"]).port, 0)
        self.assertFalse(server.parse_arguments(["--open", "--no-open"]).open)

    def test_lan_options(self):
        args = server.parse_arguments([])
        self.assertEqual((args.lan, args.content_port, args.allow_host), (False, 0, []))
        self.assertEqual(server.parse_arguments(["--lan"]).content_port, 8422)
        self.assertEqual(server.parse_arguments(["--lan", "--port", "9000"]).content_port, 9001)
        self.assertEqual(server.parse_arguments(["--lan", "--port", "0"]).content_port, 0)
        args = server.parse_arguments(["--lan", "--content-port", "9005", "--allow-host", "Laptop.TS.net"])
        self.assertEqual((args.content_port, args.allow_host), (9005, ["laptop.ts.net"]))

    def test_lan_binds_all_interfaces_on_fixed_ports(self):
        servers = [mock.MagicMock(), mock.MagicMock()]
        for s in servers:
            s.__enter__.return_value = s
        servers[0].serve_forever.side_effect = KeyboardInterrupt
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(server, "ROOT", folder), \
                mock.patch.object(server.epub_library, "BOOK_ROOT", folder), \
                mock.patch.object(server, "ThreadingHTTPServer", side_effect=servers) as factory, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(server.main(["--lan", "--port", "9000", "--allow-host", "pc.ts.net", "--manga", folder, "--books", folder]), 0)
        self.assertEqual(factory.call_args_list, [mock.call(("0.0.0.0", 9000), server.Handler),
                                                  mock.call(("0.0.0.0", 9001), server.EpubContentHandler)])
        self.assertEqual([(s.lan, s.allowed_hosts) for s in servers], [(True, ["pc.ts.net"])] * 2)
        self.assertIn("no login", output.getvalue().lower())

    def test_custom_paths_and_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            args = server.parse_arguments(["--manga", folder, "--books", folder, "--port", "9001"])
            self.assertEqual((args.manga, args.books, args.port), (os.path.abspath(folder), os.path.abspath(folder), 9001))
            for arguments in (["--port", "65536"], ["9000", "--port", "9001"],
                              ["--books", os.path.join(folder, "missing")]):
                with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        server.parse_arguments(arguments)

    def test_port_conflicts_and_restart(self):
        with server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler) as first:
            port = first.server_address[1]
            with self.assertRaises(OSError):
                server.ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
        with server.ThreadingHTTPServer(("127.0.0.1", port), server.Handler):
            pass

    def test_client_disconnects_are_not_reported(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(server, "ROOT", folder), \
                server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler) as httpd:
            thread = threading.Thread(target=httpd.serve_forever)
            thread.start()
            try:
                for error, reported in ((ConnectionAbortedError(10053, "aborted by the browser"), False),
                                        (BrokenPipeError(32, "broken pipe"), False), (ValueError("a real bug"), True)):
                    # The reply write fails as it does when the client has already closed its socket.
                    with mock.patch.object(server.Handler, "_send_json", side_effect=error), \
                            contextlib.redirect_stderr(io.StringIO()) as output, \
                            socket.create_connection(httpd.server_address) as client:
                        client.sendall(b"GET /api/listing HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                        client.recv(1)  # returns once the server has handled the request and closed the socket
                    self.assertEqual("Traceback" in output.getvalue(), reported, error)
            finally:
                httpd.shutdown()
                thread.join()

    def test_open_option_uses_bound_server_address(self):
        main_server = mock.MagicMock()
        main_server.__enter__.return_value = main_server
        main_server.server_address = ("127.0.0.1", 12345)
        main_server.serve_forever.side_effect = KeyboardInterrupt
        content_server = mock.MagicMock()
        content_server.__enter__.return_value = content_server
        content_server.server_address = ("127.0.0.1", 12346)
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(server, "ROOT", folder), \
                mock.patch.object(server.epub_library, "BOOK_ROOT", folder), \
                mock.patch.object(server, "ThreadingHTTPServer", side_effect=[main_server, content_server]), \
                mock.patch.object(server.webbrowser, "open", return_value=True) as browser, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.main(["--port", "0", "--open", "--manga", folder, "--books", folder]), 0)
        browser.assert_called_once_with("http://127.0.0.1:12345/")
        content_server.shutdown.assert_called_once()


JPDB_LIST = """<html><body><form><input name="q"></form><p>Showing 2 entries</p>
<div><div><a href="/novel/7176/kino-no-tabi-the-beautiful-world-/stats">Statistics...</a></div>
<div><div style="opacity: 0.5">Novel</div><h5 style="max-width: 30rem;">Kino no Tabi -the Beautiful World-</h5>
<table class="cross-table"><tr><th>Length (in words)</th><td>154967</td></tr><tr><th>Unique words</th><td>10712</td></tr>
<tr><th>Average difficulty</th><td>48/100</td></tr><tr><th>Peak difficulty (90th percentile)</th><td>59/100</td></tr>
<tr><th>Characters</th><td>347673</td></tr><tr><th>Volumes</th><td>5</td></tr></table>
<a href="/novel/7176/kino-no-tabi-the-beautiful-world-" class="outline">Show details...</a></div></div>
<div><h5>Kino no Tabi Best Selection</h5><table><tr><th>Average difficulty</th><td>50/100</td></tr></table>
<a href="/novel/9000/kino-no-tabi-best-selection" class="outline">Show details...</a></div>
</body></html>"""


def natively_item(title, media_type, level, url, volumes=1, series=True):
    item = {"title": title, "mediaType": media_type, "libraryType": "books", "url": url, "numOfItems": volumes,
            "itemType": "book_series" if series else "book", "rating": {"lvl": level, "lvlDescriptor": f"{level} (Upper Intermediate, ~JLPT N2)",
                                                                          "temporary": False},
            "reviewData": {"avgRating": 3.9}}
    return {"widget": "series" if series else "book", "series" if series else "book": item}


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(lookups, "CACHE_PATH", os.path.join(self.temp.name, "lookup_cache.json")),
                   mock.patch.object(lookups, "OLD_CACHE_PATH", os.path.join(self.temp.name, "difficulty_cache.json")),
                   mock.patch.object(lookups, "_cache", None), mock.patch.object(lookups, "_busy", {}),
                   mock.patch.dict(lookups.ENABLED, {"jiten": True, "natively": True, "jpdb": True}),
                   mock.patch.object(jiten, "API_KEY", "")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.temp.cleanup)

    def saved(self):
        with open(lookups.CACHE_PATH, encoding="utf-8") as file:
            return json.load(file)

    def test_parallel_lookups_preserve_all_entries(self):
        with mock.patch.object(jiten, "search", return_value=None):
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda title: lookups.lookup("jiten", "manga", title),
                                            [f"Test series {number}" for number in range(24)]))
        self.assertEqual([result["result"] for result in results], [None] * 24)
        self.assertEqual(len(self.saved()), 24)
        self.assertEqual(os.listdir(self.temp.name), ["lookup_cache.json"])

    def test_one_title_is_looked_up_once_and_retried_after_a_week(self):
        with mock.patch.object(jiten, "search", return_value=None) as search:
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(lambda _: lookups.lookup("jiten", "novel", "Same"), range(4)))
            search.assert_called_once()
            entry = self.saved()["jiten:novel:same"]
            with mock.patch.object(lookups.time, "time", return_value=entry["at"] + lookups.RETRY_SECONDS + 1):
                lookups.lookup("jiten", "novel", "Same")
            self.assertEqual(search.call_count, 2)

    def test_unavailable_site_is_not_cached_as_a_missing_title(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            for source in ("jiten", "natively", "jpdb"):
                with self.assertRaises(lookups.Unavailable):
                    lookups.lookup(source, "novel", "Test series")
        self.assertFalse(os.path.exists(lookups.CACHE_PATH))

    def test_offline_refresh_keeps_the_saved_answer(self):
        found = {"deckId": 7, "url": "https://jiten.moe/decks/media/7/detail", "title": "Old", "difficulty": 2.0}
        lookups.store("jiten:manga:old", {"source": "jiten", "at": 0, "link": None, "result": found,
                                          "matcher": lookups.MATCHER_VERSION})
        with mock.patch.object(jiten, "get", side_effect=web.LookupFailed("offline")):
            answer = lookups.lookup("jiten", "manga", "Old")
        self.assertEqual((answer["result"], answer["stale"]), (found, "offline"))

    def test_failed_cache_write_does_not_report_a_saved_result(self):
        with mock.patch.object(jiten, "search", return_value=None), \
                mock.patch.object(lookups, "save_cache", side_effect=OSError("read-only")):
            with self.assertRaisesRegex(OSError, "read-only"):
                lookups.lookup("jiten", "manga", "Test series")
        self.assertEqual(lookups._cache, {})

    def test_invalid_cache_is_reported_without_overwriting_it(self):
        with open(lookups.CACHE_PATH, "w", encoding="utf-8") as file:
            file.write("{broken")
        with self.assertRaisesRegex(OSError, "Could not read the lookup cache"):
            lookups.lookup("jiten", "manga", "Test series")
        with open(lookups.CACHE_PATH, encoding="utf-8") as file:
            self.assertEqual(file.read(), "{broken")

    def test_matches_saved_by_the_old_difficulty_cache_are_refreshed_not_searched(self):
        with open(lookups.OLD_CACHE_PATH, "w", encoding="utf-8") as file:
            json.dump({"manga:old": {"deckId": 7, "difficulty": 2}, "novel:missing": None}, file)
        with mock.patch.object(jiten, "fetch", return_value={"deckId": 7, "url": "u", "title": "Old"}) as fetch, \
                mock.patch.object(jiten, "search") as search:
            self.assertEqual(lookups.lookup("jiten", "manga", "Old")["result"]["title"], "Old")
        fetch.assert_called_once_with(7)
        search.assert_not_called()

    def test_jiten_keeps_only_the_media_type_and_reads_volumes_and_statistics(self):
        def fake_get(path):
            if "search-suggestions" in path:
                return {"suggestions": [{"deckId": 1, "originalTitle": "涼宮ハルヒの憂鬱", "mediaType": 9},
                                        {"deckId": 2, "originalTitle": "涼宮ハルヒの憂鬱", "romajiTitle": "Suzumiya Haruhi no Yuuutsu", "mediaType": 4}]}
            if "/stats" in path:
                return {"milestones": {"90%": 3000, "95%": 6000}}
            return {"data": {"mainDeck": {"originalTitle": "涼宮ハルヒの憂鬱", "romajiTitle": "Suzumiya Haruhi no Yuuutsu", "difficultyRaw": 3.31, "description": "Kyon meets Haruhi.",
                                          "characterCount": 100000, "uniqueWordCount": 9000, "childrenDeckCount": 2, "coverage": 80},
                             "subDecks": [{"deckId": 21, "originalTitle": "Volume 1", "difficultyRaw": 3.1, "characterCount": 50000},
                                          {"deckId": 22, "originalTitle": "第2巻", "difficultyRaw": 3.5}]}}
        with mock.patch.object(jiten, "get", side_effect=fake_get):
            result = lookups.lookup("jiten", "novel", "涼宮ハルヒの憂鬱 (角川スニーカー文庫)")["result"]
        self.assertEqual((result["deckId"], result["difficulty"], result["description"], result["wordsFor"]),
                         (2, 3.31, "Kyon meets Haruhi.", {"90%": 3000, "95%": 6000}))
        self.assertEqual([(volume["number"], volume["deckId"]) for volume in result["volumes"]], [(1, 21), (2, 22)])
        self.assertIsNone(result["coverage"])  # coverage is only the viewer's own with an API key
        self.assertEqual(result["titles"], ["涼宮ハルヒの憂鬱", "Suzumiya Haruhi no Yuuutsu"])

    def test_natively_prefers_the_main_series_of_the_right_media_type(self):
        def fake(url):
            query = urllib.parse.unquote(url.split("q=", 1)[1])
            items = [natively_item("キノの旅", "manga_series", 27, "/series/manga/", 5)]
            if query == "キノの旅":
                items.append(natively_item("キノの旅", "light_novel_series", 30, "/series/main/", 24))
            items.append(natively_item("キノの旅 the Beautiful World Best Selection", "light_novel_series", 29, "/series/best/", 3))
            return items
        with mock.patch.object(web, "get_json", side_effect=fake):
            result = natively.search("キノの旅 the Beautiful World", "novel")
            manga = natively.search("キノの旅", "manga")
        self.assertEqual((result["url"], result["level"]), ("https://learnnatively.com/series/main/", 30))
        self.assertEqual(manga["url"], "https://learnnatively.com/series/manga/")

    def test_natively_link_reads_the_level_from_the_page_title(self):
        page = "<html><head><title>ある日突然、ギャルの許嫁ができた | L27</title></head></html>"
        with mock.patch.object(web, "get_text", return_value=page) as get:
            result = natively.from_link("https://learnnatively.com/series/4a5a9622b3/?tab=books", "novel")
        get.assert_called_once_with("https://learnnatively.com/series/4a5a9622b3/")
        self.assertEqual((result["title"], result["level"]), ("ある日突然、ギャルの許嫁ができた", 27))
        with self.assertRaisesRegex(ValueError, "learnnatively.com"):
            natively.from_link("https://example.com/series/1/", "novel")

    def test_jpdb_reads_its_difficulty_list_and_has_no_manga(self):
        self.assertEqual([(title, url, data.get("difficulty")) for title, url, data in jpdb.entries(JPDB_LIST)],
                         [("Kino no Tabi -the Beautiful World-", "https://jpdb.io/novel/7176/kino-no-tabi-the-beautiful-world-", 48),
                          ("Kino no Tabi Best Selection", "https://jpdb.io/novel/9000/kino-no-tabi-best-selection", 50)])
        with mock.patch.object(web, "get_text", return_value=JPDB_LIST):
            result = jpdb.search("Kino no Tabi: the Beautiful World", "novel")
            by_japanese_name = jpdb.search("キノの旅", "novel", aliases=("Kino no Tabi -the Beautiful World-",))
        self.assertEqual((result["difficulty"], result["peak"], result["characters"], result["volumes"]), (48, 59, 347673, 5))
        self.assertEqual(by_japanese_name["url"], "https://jpdb.io/novel/7176/kino-no-tabi-the-beautiful-world-")
        self.assertIsNone(jpdb.search("よつばと！", "manga"))

    def test_a_link_fixes_a_match_until_it_is_removed(self):
        with mock.patch.object(jiten, "search", return_value=None) as search, \
                mock.patch.object(jiten, "fetch", return_value={"deckId": 5, "url": "u", "title": "Right"}) as fetch:
            self.assertIsNone(lookups.lookup("jiten", "manga", "Mine")["result"])
            answer = lookups.set_link("jiten", "manga", "Mine", "https://jiten.moe/decks/media/5/detail")
            self.assertEqual((answer["result"]["title"], answer["manual"]), ("Right", True))
            fetch.assert_called_once_with(5)
            self.assertTrue(lookups.lookup("jiten", "manga", "Mine")["manual"])
            none = lookups.set_link("jiten", "manga", "Mine", "none")
            self.assertEqual((none["result"], none["manual"]), (None, True))
            with mock.patch.object(lookups.time, "time", return_value=time.time() + 365 * 86400):
                self.assertTrue(lookups.lookup("jiten", "manga", "Mine")["manual"])  # never searched again
            self.assertEqual(search.call_count, 1)
            self.assertFalse(lookups.set_link("jiten", "manga", "Mine", "")["manual"])  # "" searches again
            self.assertEqual(search.call_count, 2)
        with self.assertRaisesRegex(ValueError, "jiten.moe"):
            lookups.set_link("jiten", "manga", "Mine", "https://jpdb.io/novel/1/x")

    def test_link_endpoint_is_for_this_computer_only(self):
        with server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler) as httpd:
            thread = threading.Thread(target=httpd.serve_forever)
            thread.start()
            try:
                def post(body):
                    connection = http.client.HTTPConnection(*httpd.server_address)
                    connection.request("POST", "/api/lookup-link", json.dumps(body), {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    result = response.status, response.read()
                    connection.close()
                    return result
                request = {"source": "jiten", "type": "manga", "title": "Mine", "link": "none"}
                self.assertEqual(post(request)[0], 200)
                self.assertEqual(post({**request, "link": "https://example.com"})[0], 400)
                with mock.patch.object(server.Handler, "from_this_computer", return_value=False), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(post(request)[0], 403)
            finally:
                httpd.shutdown()
                thread.join()

    def test_title_variants_drop_publisher_labels_editions_and_hashes(self):
        self.assertEqual(titles.clean_title_variants("きょうの日はさようなら 完全版 (文春文庫)")[-1], "きょうの日はさようなら")
        self.assertEqual(titles.clean_title_variants("五人一役でも君が好き【電子特典付き】")[-1], "五人一役でも君が好き")
        self.assertIn("ある日突然、ギャルの許嫁ができた", titles.clean_title_variants("ある日突然、ギャルの許嫁ができた [d5af847c]"))
        self.assertEqual(titles.clean_title_variants("キノの旅 the Beautiful World")[-1], "キノの旅")
        self.assertEqual(titles.clean_title_variants("ごん狐"), ["ごん狐"])


if __name__ == "__main__":
    unittest.main()
