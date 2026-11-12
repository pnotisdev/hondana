import contextlib
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
from unittest import mock

from reader import config, jiten, known_words, server


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
            self.assertEqual((server.ROOT, server.SKIP_MANGA_FOLDERS, server.JITEN), (args.settings["manga"], {"Duplicate"}, False))
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
                    with mock.patch.object(jiten, "get_difficulty") as lookup:
                        self.assertEqual(get("/api/difficulty?title=Series")[0], 404)
                    lookup.assert_not_called()
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


class DifficultyCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = jiten.DIFFICULTY_CACHE_PATH
        self.old_cache = jiten._difficulty_cache
        jiten.DIFFICULTY_CACHE_PATH = os.path.join(self.temp.name, "cache.json")
        jiten._difficulty_cache = None

    def tearDown(self):
        jiten.DIFFICULTY_CACHE_PATH = self.old_path
        jiten._difficulty_cache = self.old_cache
        self.temp.cleanup()

    def test_parallel_lookups_preserve_all_entries(self):
        with mock.patch.object(jiten, "jiten_search", return_value=[]):
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(jiten.get_difficulty,
                                            [f"Test series {number}" for number in range(24)]))
        self.assertEqual(results, [None] * 24)
        with open(jiten.DIFFICULTY_CACHE_PATH, encoding="utf-8") as file:
            cache = json.load(file)
        self.assertEqual(len(cache), 24)
        self.assertEqual(os.listdir(self.temp.name), ["cache.json"])

    def test_unavailable_jiten_is_not_cached_as_a_missing_title(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(jiten.JitenLookupError):
                jiten.get_difficulty("Test series")
        self.assertFalse(os.path.exists(jiten.DIFFICULTY_CACHE_PATH))
        self.assertEqual(jiten._difficulty_cache, {})

    def test_failed_cache_write_does_not_report_a_saved_result(self):
        with mock.patch.object(jiten, "jiten_search", return_value=[]), \
                mock.patch.object(jiten, "save_difficulty_cache", side_effect=OSError("read-only")):
            with self.assertRaisesRegex(OSError, "read-only"):
                jiten.get_difficulty("Test series")
        self.assertEqual(jiten._difficulty_cache, {})

    def test_title_variants_drop_publisher_labels_and_editions(self):
        self.assertEqual(jiten.clean_title_variants("きょうの日はさようなら 完全版 (文春文庫)")[-1], "きょうの日はさようなら")
        self.assertEqual(jiten.clean_title_variants("五人一役でも君が好き【電子特典付き】")[-1], "五人一役でも君が好き")
        self.assertEqual(jiten.clean_title_variants("ごん狐"), ["ごん狐"])

    def test_novel_lookup_keeps_only_novels_and_saves_the_summary(self):
        urls = []

        def fake_get(url):
            urls.append(url)
            if "/detail" in url:
                return {"data": {"mainDeck": {"difficulty": 3, "difficultyRaw": 3.3, "description": "Kyon meets Haruhi."}}}
            return {"suggestions": [{"deckId": 1, "originalTitle": "涼宮ハルヒの憂鬱", "mediaType": 9},
                                    {"deckId": 2, "originalTitle": "涼宮ハルヒの憂鬱", "mediaType": 4}]}

        with mock.patch.object(jiten, "http_get_json", side_effect=fake_get):
            result = jiten.get_difficulty("涼宮ハルヒの憂鬱", "novel")
        self.assertEqual((result["deckId"], result["difficultyRaw"], result["description"]),
                         (2, 3.3, "Kyon meets Haruhi."))
        self.assertIn("/media-deck/2/detail", urls[-1])
        with open(jiten.DIFFICULTY_CACHE_PATH, encoding="utf-8") as file:
            self.assertEqual(list(json.load(file)), ["novel:涼宮ハルヒの憂鬱"])

    def test_entries_saved_without_a_summary_are_refreshed_once(self):
        old = {"deckId": 7, "matchedTitle": "Old", "matchScore": 1.0, "difficulty": 2, "difficultyRaw": 2.0}
        with open(jiten.DIFFICULTY_CACHE_PATH, "w", encoding="utf-8") as file:
            json.dump({"manga:old": old}, file)
        with mock.patch.object(jiten, "http_get_json", side_effect=jiten.JitenLookupError("offline")):
            self.assertEqual(jiten.get_difficulty("Old"), old)
        detail = {"data": {"mainDeck": {"difficulty": 2, "difficultyRaw": 2.4, "description": "Summary"}}}
        with mock.patch.object(jiten, "http_get_json", return_value=detail) as get:
            self.assertEqual(jiten.get_difficulty("Old"), {**old, "difficultyRaw": 2.4, "description": "Summary"})
            self.assertEqual(jiten.get_difficulty("Old")["description"], "Summary")
        get.assert_called_once()  # the saved deck's detail only: no new search, and none once complete

    def test_invalid_cache_is_reported_without_overwriting_it(self):
        with open(jiten.DIFFICULTY_CACHE_PATH, "w", encoding="utf-8") as file:
            file.write("{broken")
        with self.assertRaisesRegex(OSError, "Could not read difficulty cache"):
            jiten.get_difficulty("Test series")
        with open(jiten.DIFFICULTY_CACHE_PATH, encoding="utf-8") as file:
            self.assertEqual(file.read(), "{broken")


if __name__ == "__main__":
    unittest.main()
