import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
import zipfile
from urllib.parse import quote

from reader import epub_library
from reader import furigana
from reader import jiten, server


class BookLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_root = epub_library.BOOK_ROOT
        self.old_manga_root = server.ROOT
        epub_library.BOOK_ROOT = self.temp.name
        server.ROOT = self.temp.name
        os.mkdir(os.path.join(self.temp.name, "nested"))
        self.name = "nested/日本語 #1.epub"
        with zipfile.ZipFile(os.path.join(self.temp.name, *self.name.split("/")), "w") as archive:
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>')
            archive.writestr("OPS/package.opf", """<package xmlns:dc="http://purl.org/dc/elements/1.1/">
              <metadata><dc:title>日本語</dc:title><dc:creator>Author</dc:creator></metadata>
              <manifest>
                <item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
                <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
                <item id="chapter" href="text/chapter.xhtml" media-type="application/xhtml+xml"/>
                <item id="style" href="style.css" media-type="text/css"/>
              </manifest><spine><itemref idref="chapter"/></spine></package>""")
            archive.writestr("OPS/nav.xhtml", '<html><body><a href="text/chapter.xhtml#start">Chapter one</a></body></html>')
            archive.writestr("OPS/text/chapter.xhtml", '<html><head><link rel="stylesheet" href="../style.css"/></head><body id="start">本文<script>bad()</script></body></html>')
            archive.writestr("OPS/cover.jpg", b"image")
            archive.writestr("OPS/style.css", b"body { color: black }")
        self.server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        epub_library.BOOK_ROOT = self.old_root
        server.ROOT = self.old_manga_root
        self.temp.cleanup()

    def request(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        status = response.status
        connection.close()
        return status, headers, body

    def test_bundled_manga_home_and_existing_override(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Search manga", body)
        self.assertEqual(headers["Cache-Control"], "no-cache")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("HEAD", "/")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), b"")
        connection.close()
        with open(os.path.join(self.temp.name, "index.html"), "w", encoding="utf-8") as file:
            file.write("Existing custom manga page")
        self.assertEqual(self.request("/")[2], b"Existing custom manga page")

    def test_requests_for_other_hostnames_are_refused(self):
        for host, expected in (("localhost:1", 200), ("evil.example", 403), ("[bad", 403), ("192.168.1.20:1", 403)):
            self.assertEqual(self.status_for_host(host), expected, host)

    def test_lan_mode_allows_ip_addresses_and_named_hosts_only(self):
        self.server.lan = True
        self.server.allowed_hosts = ["laptop.ts.net"]
        for host, expected in (("192.168.1.20:1", 200), ("[::1]:1", 200), ("Laptop.TS.net", 200),
                               ("evil.example", 403), ("192.168.1.20.evil.example", 403), ("", 403)):
            self.assertEqual(self.status_for_host(host), expected, host)

    def status_for_host(self, host):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", "/books/", headers={"Host": host})
        status = connection.getresponse().status
        connection.close()
        return status

    def test_series_names_with_percent_signs_are_decoded_once(self):
        os.makedirs(os.path.join(self.temp.name, "100%BA", "vol1"))
        status, _, body = self.request("/api/listing?path=100%25BA")
        self.assertEqual((status, json.loads(body)), (200, [{"name": "vol1", "isDir": True}]))

    def test_mokuro_pages_get_inline_lines_and_other_html_is_unchanged(self):
        os.mkdir(os.path.join(self.temp.name, "series"))
        page = '<html><head><title>v1 | mokuro</title></head><body><div class="textBox"><p>一行</p><p>二行</p></div></body></html>'
        for name, text in (("v1.html", page), ("notes.html", "<p>plain</p>")):
            with open(os.path.join(self.temp.name, "series", name), "w", encoding="utf-8") as file:
                file.write(text)
        status, headers, body = self.request("/series/v1.html")
        self.assertEqual((status, body), (200, page.encode() + server.MOKURO_LINE_CSS))
        self.assertEqual(headers["Content-Length"], str(len(body)))
        self.assertEqual(self.request("/series/notes.html")[2], b"<p>plain</p>")
        self.assertEqual(self.request("/series/missing.html")[0], 404)

    def test_unreadable_cover_is_not_found_and_stale_override_is_detected(self):
        os.mkdir(os.path.join(self.temp.name, "series"))
        with open(os.path.join(self.temp.name, "series", "v1.cbz"), "wb") as file:
            file.write(b"not a zip")
        self.assertEqual(self.request("/api/thumbnail?series=series")[0], 404)
        with zipfile.ZipFile(os.path.join(self.temp.name, "series", "v1.cbz"), "w") as archive:
            archive.writestr("001.jpg", b"cover")
        self.assertEqual(self.request("/api/thumbnail?series=series")[2], b"cover")
        with mock.patch.object(zipfile.ZipFile, "read", side_effect=PermissionError("locked")):
            self.assertEqual(self.request("/api/thumbnail?series=series")[0], 404)
        self.assertIsNone(server.stale_manga_override())
        override = os.path.join(self.temp.name, "index.html")
        with open(override, "w", encoding="utf-8") as file:
            file.write("old copy")
        self.assertEqual(server.stale_manga_override(), override)

    def test_jiten_failure_returns_an_error(self):
        with mock.patch.object(jiten, "get_difficulty",
                               side_effect=jiten.JitenLookupError("offline")):
            self.assertEqual(self.request("/api/difficulty?title=example")[0], 502)
        with mock.patch.object(jiten, "get_difficulty", return_value=None) as lookup:
            self.assertEqual(self.request("/api/difficulty?title=example&type=novel")[0], 200)
            self.assertEqual(self.request("/api/difficulty?title=example&type=anime")[0], 400)
        lookup.assert_called_once_with("example", "novel")

    def test_furigana_endpoint_and_errors(self):
        def post(body, content_type="application/json"):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            connection.request("POST", "/api/furigana", body, {"Content-Type": content_type})
            response = connection.getresponse()
            status, data = response.status, response.read()
            connection.close()
            return status, data

        payload = json.dumps({"texts": ["学校"]}).encode("utf-8")
        with mock.patch.object(furigana, "annotate", return_value=[{"text": "学校", "reading": "がっこう"}]):
            status, data = post(payload)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["results"][0][0]["reading"], "がっこう")
        self.assertEqual(post(b"{}")[0], 400)
        self.assertEqual(post(b"{}", "text/plain")[0], 415)
        self.assertEqual(post(json.dumps({"texts": ["a" * 20001]}).encode())[0], 400)
        with mock.patch.object(furigana, "annotate", side_effect=furigana.FuriganaUnavailable("missing")):
            self.assertEqual(post(payload)[0], 503)

    def add_book(self, name, title, metadata=""):
        path = os.path.join(self.temp.name, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="package.opf"/></rootfiles></container>')
            archive.writestr("package.opf", f"""<package xmlns:dc="http://purl.org/dc/elements/1.1/">
              <metadata><dc:title>{title}</dc:title><dc:creator>Author</dc:creator>{metadata}</metadata>
              <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
              <spine><itemref idref="chapter"/></spine></package>""")
            archive.writestr("chapter.xhtml", "<html><body>Text</body></html>")

    def test_library_metadata_and_resources(self):
        status, _, body = self.request("/api/books")
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["errors"], [])
        book = result["books"][0]
        self.assertEqual((book["id"], book["title"], book["author"]), (self.name, "日本語", "Author"))
        self.assertEqual(book["sections"], [{"path": "OPS/text/chapter.xhtml", "title": "Chapter one"}])
        self.assertEqual(self.request(book["cover"])[2], b"image")
        prefix = "/epub-content/" + quote(self.name, safe="") + "/"
        status, headers, body = self.request(prefix + "OPS/text/chapter.xhtml")
        self.assertEqual(status, 200)
        self.assertIn("sandbox allow-scripts", headers["Content-Security-Policy"])
        self.assertIn("script-src 'nonce-", headers["Content-Security-Policy"])
        self.assertNotIn("'unsafe-inline'", headers["Content-Security-Policy"].split("script-src ")[1].split(";")[0])
        self.assertIn(b"book-reader-frame", body)
        self.assertNotEqual(headers["Content-Security-Policy"], self.request(prefix + "OPS/text/chapter.xhtml")[1]["Content-Security-Policy"])
        self.assertIn("本文".encode(), body)
        self.assertEqual(self.request(prefix + "OPS/style.css")[0], 200)
        self.assertEqual(self.request(prefix + "%2e%2e/package.opf")[0], 404)
        self.assertEqual(self.request("/books/")[0], 200)
        self.assertEqual(self.request("/api/listing")[0], 200)

    def test_description_is_plain_text(self):
        self.add_book("described.epub", "Described",
                      "<dc:description>&lt;p&gt;一行目&lt;br/&gt;二行目&amp;amp;&lt;/p&gt;</dc:description>")
        books = {book["id"]: book for book in json.loads(self.request("/api/books")[2])["books"]}
        self.assertEqual(books["described.epub"]["description"], "一行目\n二行目&")
        self.assertEqual(books[self.name]["description"], "")

    def test_book_character_count(self):
        prefix = "/api/book-character-count?book="
        status, _, body = self.request(prefix + quote(self.name, safe=""))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "sections": [{"path": "OPS/text/chapter.xhtml", "characters": 2}],
            "total": 2,
        })
        self.assertEqual(self.request("/api/book-character-count")[0], 400)
        self.assertEqual(self.request(prefix + quote("../missing.epub", safe=""))[0], 422)

    def test_character_counter_excludes_annotations_and_nonreading_content(self):
        counter = epub_library.CharacterCounter()
        counter.feed('''<html><head><title>ignored</title></head><body>
          <p> <ruby>学校<rt>がっこう</rt><rp>(</rp></ruby> ABC。&#x1F5FE;</p>
          <span hidden><b>hidden</b><br>ignored</span>
          <span aria-hidden="true">ignored</span><svg><text>ignored</text></svg>
          <script>ignored</script><style>ignored</style>
        </body></html>''')
        counter.close()
        # ttsu's rule: 学校 and ABC count; the full stop and the emoji do not
        self.assertEqual(counter.count, 5)
        counter = epub_library.CharacterCounter()
        counter.feed('<p>ｶﾞ々ー１ａ、。「」…！？　<img class="gaiji-line" src="g.png"/><img src="photo.jpg"/>𠮟る</p>')
        self.assertEqual(counter.count, 8)  # ｶ 々 ー １ ａ, the gaiji image, 𠮟 る; like ttsu, not the mark ﾞ

    def test_isolated_content_origin_only_serves_epub_resources(self):
        content = server.ThreadingHTTPServer(("127.0.0.1", 0), server.EpubContentHandler)
        content.isolated_epub = True
        content.epub_parent_port = self.server.server_port
        self.server.epub_content_port = content.server_port
        thread = threading.Thread(target=content.serve_forever)
        thread.start()
        try:
            data = json.loads(self.request("/api/books")[2])
            self.assertEqual(data["contentPort"], content.server_port)
            self.assertNotEqual(data["contentPort"], self.server.server_port)
            connection = http.client.HTTPConnection("127.0.0.1", content.server_port)
            path = "/epub-content/" + quote(self.name, safe="") + "/OPS/text/chapter.xhtml"
            connection.request("GET", path)
            response = connection.getresponse()
            policy = response.getheader("Content-Security-Policy")
            response.read()
            self.assertEqual(response.status, 200)
            self.assertIn("sandbox allow-scripts allow-same-origin", policy)
            self.assertIn("frame-src moz-extension: chrome-extension:", policy)
            self.assertIn(f"frame-ancestors http://127.0.0.1:{self.server.server_port}", policy)
            self.assertIn("script-src 'nonce-", policy)
            content.lan = True  # the library page may then be on the host name the request used
            connection.request("GET", path, headers={"Host": "192.168.1.20:1"})
            response = connection.getresponse()
            policy = response.getheader("Content-Security-Policy")
            response.read()
            self.assertIn(f"frame-ancestors http://127.0.0.1:{self.server.server_port} "
                          f"http://localhost:{self.server.server_port} "
                          f"http://192.168.1.20:{self.server.server_port};", policy)
            connection.request("GET", "/api/books")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 404)
            connection.close()
        finally:
            content.shutdown()
            thread.join()
            content.server_close()

    def test_invalid_epub_is_reported(self):
        with open(os.path.join(self.temp.name, "broken.epub"), "wb") as file:
            file.write(b"not an epub")
        result = json.loads(self.request("/api/books")[2])
        self.assertEqual(len(result["books"]), 1)
        self.assertEqual(result["errors"][0]["file"], "broken.epub")

    def test_series_metadata_folders_and_numbered_names(self):
        self.add_book("folder/Volume 10.epub", "Story 10",
                      '<meta name="calibre:series" content="Different metadata"/>')
        self.add_book("folder/Volume 2.epub", "Story 2")
        self.add_book("folder/vol3.epub", "Another title")
        self.add_book("folder/第４巻.epub", "Another title")
        self.add_book("Moon 1.epub", "First",
                      '<meta name="calibre:series" content="Moon"/><meta name="calibre:series_index" content="1"/>')
        self.add_book("Moon 2.epub", "Second",
                      '<meta property="belongs-to-collection" id="s">Moon</meta>'
                      '<meta refines="#s" property="collection-type">series</meta>'
                      '<meta refines="#s" property="group-position">2</meta>')
        self.add_book("River Vol 2.epub", "River Vol 2")
        self.add_book("River Vol 10.epub", "River Vol 10")
        self.add_book("Standalone 1.epub", "Standalone 1")
        books = {book["id"]: book for book in json.loads(self.request("/api/books")[2])["books"]}
        self.assertEqual(books["folder/Volume 10.epub"]["series"], "folder")
        self.assertEqual(books["folder/Volume 10.epub"]["seriesKey"], books["folder/Volume 2.epub"]["seriesKey"])
        self.assertEqual(books["folder/Volume 10.epub"]["volume"], 10)
        self.assertEqual(books["folder/Volume 2.epub"]["volume"], 2)
        self.assertEqual(books["folder/vol3.epub"]["volume"], 3)
        self.assertEqual(books["folder/第４巻.epub"]["volume"], 4)
        self.assertEqual(books["Moon 1.epub"]["seriesKey"], books["Moon 2.epub"]["seriesKey"])
        self.assertEqual((books["Moon 1.epub"]["volume"], books["Moon 2.epub"]["volume"]), (1, 2))
        self.assertEqual(books["River Vol 2.epub"]["seriesKey"], books["River Vol 10.epub"]["seriesKey"])
        self.assertEqual(books["River Vol 2.epub"]["series"], "River")
        self.assertIsNone(books["Standalone 1.epub"]["seriesKey"])

    def test_japanese_and_numbered_volume_names(self):
        self.assertEqual(epub_library.volume_name("シリーズ第１０巻"), ("シリーズ", 10))
        self.assertEqual(epub_library.volume_name("Example volume 02"), ("Example", 2))
        self.assertEqual(epub_library.volume_name("1984"), (None, None))
        self.assertEqual(epub_library.volume_name("Revol1"), (None, None))


if __name__ == "__main__":
    unittest.main()
