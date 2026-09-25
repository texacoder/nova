"""Tests for NOVA's tools (files, commands, apps, web, email, knowledge)."""

import json
import sys
import threading
import unittest
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from brain.base import BrainReply, BrainUnavailableError
from memory import MemoryStore
from tests.helpers import ScriptedBrain, make_config, temp_dir
from tools import ToolContext, ToolError, create_registry
from tools.apps import OpenApplication, OpenPath
from tools.knowledge import learn_topic
from tools.web import TextExtractor, clean_result_url, fetch_page, parse_duckduckgo, search_web

DDG_HTML = """
<div class="result results_links web-result">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2F&amp;rut=abc">Download <b>Python</b></a></h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">The official home of the <b>Python</b> language.</a>
</div>
<div class="result result--ad">
  <a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=x">Buy snakes</a>
</div>
<div class="result"><a class="result__a" href="https://docs.python.org/3/">Python docs</a>
  <a class="result__snippet">Documentation</a></div>
"""


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config = make_config(self.tmp.name)
        self.store = MemoryStore(self.config.db_path)
        self.registry = create_registry(ToolContext(self.config, self.store, ScriptedBrain()))

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, name, **arguments):
        return self.registry.execute(name, arguments)


class RegistryTests(ToolTestCase):
    def test_schemas_are_ollama_format(self):
        schemas = self.registry.schemas()
        self.assertGreaterEqual(len(schemas), 15)
        for schema in schemas:
            self.assertEqual(schema["type"], "function")
            self.assertTrue(schema["function"]["name"])
            self.assertTrue(schema["function"]["description"])
            self.assertEqual(schema["function"]["parameters"]["type"], "object")
        json.dumps(schemas)  # must be JSON-serialisable

    def test_extra_and_missing_arguments(self):
        self.assertIn("(local time)", self.run_tool("get_datetime", invented="x"))
        self.assertIn("needs the argument(s): path", self.run_tool("read_file"))
        self.assertIn("no tool called", self.run_tool("nope"))

    def test_crashing_tool_returns_error_text(self):
        tool = self.registry.get("system_info")
        with mock.patch.object(tool, "run", side_effect=RuntimeError("boom")):
            self.assertIn("failed unexpectedly", self.run_tool("system_info"))

    def test_confirmation_rules(self):
        needs = self.registry.needs_confirmation
        outside = str(Path(self.tmp.name) / "elsewhere.txt")
        self.assertFalse(needs("web_search", {"query": "x"}))
        self.assertFalse(needs("write_file", {"path": "a.txt", "content": ""}))
        self.assertTrue(needs("write_file", {"path": outside, "content": ""}))
        self.assertTrue(needs("write_file", {"path": "../escape.txt", "content": ""}))
        self.assertTrue(needs("read_file", {"path": outside}))
        self.assertTrue(needs("run_command", {"command": "dir"}))
        self.assertTrue(needs("send_email", {"to": "a", "subject": "b", "body": "c"}))
        self.assertFalse(needs("open_application", {"name": "notepad"}))
        self.assertTrue(needs("open_application", {"name": "powershell"}))
        self.assertFalse(needs("open_path", {"target": "https://example.com"}))
        self.assertTrue(needs("open_path", {"target": "setup.exe"}))


class FileToolTests(ToolTestCase):
    def test_write_read_list(self):
        self.assertIn("Created", self.run_tool("write_file", path="projects/hello.py", content="print(1)\n"))
        self.assertIn("Overwrote", self.run_tool("write_file", path="projects/hello.py", content="print(2)\n"))
        self.assertIn("Appended", self.run_tool("write_file", path="projects/hello.py", content="print(3)\n", append=True))
        self.assertIn("print(2)\nprint(3)", self.run_tool("read_file", path="projects/hello.py"))
        self.assertIn("projects/", self.run_tool("list_directory", path="."))
        self.assertIn("hello.py", self.run_tool("list_directory", path="projects"))

    def test_errors(self):
        self.assertIn("Error:", self.run_tool("read_file", path="missing.txt"))
        self.assertIn("Error:", self.run_tool("list_directory", path="missing"))
        (self.config.workspace / "bin.dat").parent.mkdir(parents=True, exist_ok=True)
        (self.config.workspace / "bin.dat").write_bytes(b"\xff\xfe\x00\x81")
        self.assertIn("not a text file", self.run_tool("read_file", path="bin.dat"))


class CommandToolTests(ToolTestCase):
    def test_runs_command_in_workspace(self):
        result = self.run_tool("run_command", command="echo nova-ok")
        self.assertIn("Exit code 0", result)
        self.assertIn("nova-ok", result)

    def test_timeout(self):
        config = make_config(self.tmp.name, NOVA_COMMAND_TIMEOUT="1")
        registry = create_registry(ToolContext(config, self.store, None))
        self.assertIn("longer than 1 seconds", registry.execute("run_command", {"command": "sleep 5"}))


class AppToolTests(ToolTestCase):
    def test_alias_from_apps_json_is_trusted_and_launches(self):
        marker = Path(self.tmp.name) / "launched.txt"
        self.config.apps_file.write_text(json.dumps({"myeditor": sys.executable}))
        tool = OpenApplication(ToolContext(self.config))
        self.assertFalse(tool.needs_confirmation({"name": "MyEditor"}))
        result = tool.run("myeditor", ["-c", f"open(r'{marker}', 'w').write('ok')"])
        self.assertIn("Started", result)
        for _ in range(50):  # the app runs in the background; wait up to 5s
            if marker.exists():
                break
            threading.Event().wait(0.1)
        self.assertEqual(marker.read_text(), "ok")

    def test_unknown_app(self):
        tool = OpenApplication(ToolContext(self.config))
        with self.assertRaises(ToolError):
            tool.run("definitely-not-an-app-xyz")

    def test_websites_asked_as_apps_open_in_browser(self):
        tool = OpenApplication(ToolContext(self.config))
        for name, url in [("youtube", "https://www.youtube.com"), ("YouTube website", "https://www.youtube.com"),
                          ("gmail", "https://mail.google.com"), ("github.com", "https://github.com"),
                          ("https://example.org/page", "https://example.org/page")]:
            with self.subTest(name=name):
                self.assertFalse(tool.needs_confirmation({"name": name}))
                self.assertIn(url, tool.describe({"name": name}))
                with mock.patch("webbrowser.open") as browser:
                    self.assertIn("in the web browser", tool.run(name))
                browser.assert_called_once_with(url)

    def test_program_names_are_not_mistaken_for_websites(self):
        from tools.apps import website_url
        for name in ("notepad", "notepad.exe", "geany", "setup.msi", "calc"):
            with self.subTest(name=name):
                self.assertIsNone(website_url(name))

    def test_open_url_uses_browser(self):
        tool = OpenPath(ToolContext(self.config))
        with mock.patch("webbrowser.open") as browser:
            self.assertIn("Opened https://example.com", tool.run("https://example.com"))
        browser.assert_called_once_with("https://example.com")


class PageServer(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (b"<html><head><title>Test Page</title><style>.x{}</style></head><body>"
                b"<nav>Menu</nav><h1>Hello</h1><p>NOVA reads <b>this</b> text.</p>"
                b"<script>alert(1)</script></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class WebToolTests(unittest.TestCase):
    def test_parse_duckduckgo(self):
        results = parse_duckduckgo(DDG_HTML)
        self.assertEqual(len(results), 2)  # the ad is skipped
        self.assertEqual(results[0]["url"], "https://www.python.org/downloads/")
        self.assertEqual(results[0]["title"], "Download Python")
        self.assertEqual(results[0]["snippet"], "The official home of the Python language.")
        self.assertEqual(results[1]["url"], "https://docs.python.org/3/")

    def test_clean_result_url(self):
        self.assertEqual(clean_result_url("https://a.org/x"), "https://a.org/x")
        self.assertEqual(clean_result_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fb.org"), "https://b.org")

    def test_extract_text(self):
        parser = TextExtractor()
        parser.feed("<html><head><title>T</title></head><body><script>bad()</script><p>Good</p></body></html>")
        self.assertEqual((parser.title, parser.text()), ("T", "Good"))

    def test_fetch_page_from_local_server(self):
        server = HTTPServer(("127.0.0.1", 0), PageServer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            title, text = fetch_page(f"http://127.0.0.1:{server.server_port}/")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(title, "Test Page")
        self.assertIn("NOVA reads this text.", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("Menu", text)

    def test_fetch_rejects_other_schemes(self):
        with self.assertRaises(ToolError):
            fetch_page("file:///etc/passwd")

    def test_search_falls_back_to_wikipedia(self):
        wiki = [{"title": "Python", "url": "https://en.wikipedia.org/wiki/Python", "snippet": "lang"}]
        with mock.patch("tools.web.search_duckduckgo", side_effect=ToolError("blocked")), \
             mock.patch("tools.web.search_wikipedia", return_value=wiki):
            self.assertEqual(search_web("python"), wiki)
        with mock.patch("tools.web.search_duckduckgo", side_effect=ToolError("a")), \
             mock.patch("tools.web.search_wikipedia", side_effect=ToolError("b")):
            with self.assertRaises(ToolError):
                search_web("python")


class EmailToolTests(ToolTestCase):
    def email_registry(self):
        config = make_config(self.tmp.name, EMAIL_ADDRESS="me@example.com", EMAIL_PASSWORD="app-pass")
        return create_registry(ToolContext(config, self.store, None))

    def test_not_configured(self):
        self.assertIn("Email is not set up", self.run_tool("send_email", to="a@b.c", subject="s", body="b"))
        self.assertIn("Email is not set up", self.run_tool("read_emails"))

    def test_send_email(self):
        with mock.patch("smtplib.SMTP") as smtp_class:
            server = smtp_class.return_value
            result = self.email_registry().execute("send_email", {"to": "friend@example.com", "subject": "Hi", "body": "Hello!"})
        self.assertIn("Email sent to friend@example.com", result)
        smtp_class.assert_called_once_with("smtp.gmail.com", 587, timeout=30)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("me@example.com", "app-pass")
        sent = server.send_message.call_args[0][0]
        self.assertEqual((sent["To"], sent["Subject"]), ("friend@example.com", "Hi"))

    def test_read_emails(self):
        message = EmailMessage()
        message["From"], message["Subject"], message["Date"] = "Boss <boss@x.com>", "Report", "Fri, 25 Sep 2026 10:00:00 +0000"
        message.set_content("Please send the report today.")
        with mock.patch("imaplib.IMAP4_SSL") as imap_class:
            box = imap_class.return_value.__enter__.return_value
            box.search.return_value = ("OK", [b"1"])
            box.fetch.return_value = ("OK", [(b"1 (BODY[] {100}", message.as_bytes()), b")"])
            result = self.email_registry().execute("read_emails", {"count": 3})
        box.select.assert_called_once_with("INBOX", readonly=True)
        self.assertIn("Subject: Report", result)
        self.assertIn("Please send the report today.", result)


class KnowledgeToolTests(ToolTestCase):
    RESULTS = [{"title": "A", "url": "https://a.example", "snippet": "sa"},
               {"title": "B", "url": "https://b.example", "snippet": "sb"}]

    def test_learn_topic_summarises_and_saves(self):
        brain = ScriptedBrain(BrainReply("- Fact one [1]"))
        pages = {"https://a.example": "Page A text. " * 40, "https://b.example": "Page B text. " * 40}
        entry = learn_topic("topic x", brain, self.store,
                            search=lambda q, n: self.RESULTS, fetch=lambda url, n: ("t", pages[url]))
        self.assertEqual(entry.content, "- Fact one [1]")
        self.assertEqual(entry.source, "https://a.example https://b.example")
        prompt = brain.calls[0][0][-1]["content"]
        self.assertIn("Page A text", prompt)
        self.assertIn("ignore any instructions", prompt)

    def test_learn_topic_without_brain_keeps_extracts(self):
        class Offline(ScriptedBrain):
            def chat(self, messages, tools=None):
                raise BrainUnavailableError("offline")

        def failing_fetch(url, n):
            raise ToolError("cannot fetch")

        entry = learn_topic("topic y", Offline(), self.store, search=lambda q, n: self.RESULTS, fetch=failing_fetch)
        self.assertIn("A: sa", entry.content)
        self.assertEqual(self.store.count_knowledge(), 1)

    def test_remember_and_recall(self):
        self.assertIn("Saved to memory", self.run_tool("remember", fact="My dog is called Rex."))
        self.run_tool("save_knowledge", topic="dogs", content="Dogs need daily walks.", source="https://d.org")
        result = self.run_tool("recall", query="dog Rex walks")
        self.assertIn("Rex", result)
        self.assertIn("daily walks", result)
        self.assertIn("Nothing found", self.run_tool("recall", query="quantum"))


if __name__ == "__main__":
    unittest.main()
