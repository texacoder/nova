"""Tests for the Brain interface, the mock brain and the Ollama adapter."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from brain import Brain, BrainError, BrainUnavailableError, LocalBrain, create_brain
from tests.mock_brain import MockBrain
from brain.local import extract_text_tool_call
from tests.helpers import make_config, temp_dir

TOOLS = [{"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
         for name in ("web_search", "get_datetime", "open_application", "learn_topic", "list_directory")]


class BrainInterfaceTests(unittest.TestCase):
    def test_brain_is_abstract(self):
        with self.assertRaises(TypeError):
            Brain()

    def test_incomplete_brain_cannot_be_created(self):
        class HalfBrain(Brain):
            def chat(self, messages, tools=None):
                return None
        with self.assertRaises(TypeError):
            HalfBrain()

    def test_factory(self):
        with temp_dir() as tmp:
            brain = create_brain(make_config(tmp, NOVA_BRAIN="ollama", OLLAMA_MODEL="m", OLLAMA_NUM_CTX="4096"))
            self.assertIsInstance(brain, LocalBrain)
            self.assertEqual(brain.num_ctx, 4096)


class MockBrainTests(unittest.TestCase):
    def setUp(self):
        self.brain = MockBrain()

    def ask(self, text, tools=TOOLS, system="You are NOVA."):
        return self.brain.chat([{"role": "system", "content": system}, {"role": "user", "content": text}], tools)

    def test_health_check(self):
        self.assertTrue(self.brain.health_check().ok)

    def test_greeting(self):
        self.assertIn("NOVA", self.ask("hello").content)

    def test_requests_tools(self):
        cases = {
            "search the latest python version": ("web_search", {"query": "the latest python version"}),
            "what time is it?": ("get_datetime", {}),
            "open geany": ("open_application", {"name": "geany"}),
            "learn about black holes": ("learn_topic", {"topic": "black holes"}),
        }
        for text, (tool, args) in cases.items():
            with self.subTest(text=text):
                reply = self.ask(text)
                self.assertEqual(len(reply.tool_calls), 1)
                self.assertEqual((reply.tool_calls[0].name, reply.tool_calls[0].arguments), (tool, args))

    def test_no_tool_calls_without_tools(self):
        self.assertEqual(self.ask("open geany", tools=None).tool_calls, [])

    def test_reports_tool_result(self):
        reply = self.brain.chat([
            {"role": "user", "content": "what time is it?"},
            {"role": "assistant", "content": "", "tool_calls": []},
            {"role": "tool", "content": "Friday 10:00", "tool_name": "get_datetime"},
        ])
        self.assertIn("Friday 10:00", reply.content)

    def test_uses_memory_from_system_prompt(self):
        reply = self.ask("What is my store called?", system="You are NOVA.\n- [1] My store is called EXORASTORE.")
        self.assertIn("EXORASTORE", reply.content)

    def test_rejects_invalid_messages(self):
        for bad in ([], [{"role": "robot", "content": "x"}], [{"role": "user"}], ["hi"]):
            with self.subTest(bad=bad), self.assertRaises(BrainError):
                self.brain.chat(bad)


class FakeOllama(BaseHTTPRequestHandler):
    """A tiny stand-in for the Ollama HTTP API."""

    requests = []
    tools_supported = True
    reply_with = None  # message dict to return from /api/chat, or None to echo

    def _send(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"models": [{"name": "testmodel:latest"}]})

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append((self.path, request))
        if self.path == "/api/show":
            caps = ["completion", "tools"] if FakeOllama.tools_supported else ["completion"]
            self._send({"capabilities": caps})
        elif "tools" in request and not FakeOllama.tools_supported:
            self._send({"error": "registry.ollama.ai/library/testmodel does not support tools"}, 400)
        elif FakeOllama.reply_with is not None:
            self._send({"message": FakeOllama.reply_with})
        else:
            self._send({"message": {"role": "assistant", "content": f"  echo: {request['messages'][-1]['content']} "}})

    def log_message(self, *args):
        pass  # keep test output quiet


class LocalBrainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        cls.host = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeOllama.requests = []
        FakeOllama.tools_supported = True
        FakeOllama.reply_with = None

    def test_generate_response(self):
        brain = LocalBrain(self.host, "testmodel", num_ctx=4096)
        self.assertEqual(brain.generate_response([{"role": "user", "content": "ping"}]), "echo: ping")
        _, request = FakeOllama.requests[-1]
        self.assertEqual(request["model"], "testmodel")
        self.assertFalse(request["stream"])
        self.assertEqual(request["options"]["num_ctx"], 4096)
        self.assertNotIn("tools", request)

    def test_tool_calls_are_parsed(self):
        FakeOllama.reply_with = {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "weather"}}},
            {"function": {"name": "get_datetime", "arguments": "{}"}},  # some servers send a string
        ]}
        reply = LocalBrain(self.host, "testmodel").chat([{"role": "user", "content": "hi"}], TOOLS)
        self.assertEqual([(c.name, c.arguments) for c in reply.tool_calls],
                         [("web_search", {"query": "weather"}), ("get_datetime", {})])
        self.assertEqual(FakeOllama.requests[-1][1]["tools"], TOOLS)

    def test_text_tool_call_is_recognised(self):
        FakeOllama.reply_with = {"role": "assistant",
                                 "content": '```json\n{"name": "web_search", "arguments": {"query": "x"}}\n```'}
        reply = LocalBrain(self.host, "testmodel").chat([{"role": "user", "content": "hi"}], TOOLS)
        self.assertEqual(reply.tool_calls[0].name, "web_search")

    def test_model_without_tool_support_falls_back(self):
        FakeOllama.tools_supported = False
        brain = LocalBrain(self.host, "testmodel")
        reply = brain.chat([{"role": "user", "content": "ping"}], TOOLS)
        self.assertEqual(reply.content, "echo: ping")
        self.assertFalse(brain.supports_tools)

    def test_health_check_ok_and_capabilities(self):
        brain = LocalBrain(self.host, "testmodel")
        self.assertTrue(brain.health_check().ok)
        self.assertTrue(brain.supports_tools)
        FakeOllama.tools_supported = False
        status = brain.health_check()
        self.assertTrue(status.ok)
        self.assertIn("cannot use tools", status.message)

    def test_health_check_missing_model(self):
        status = LocalBrain(self.host, "othermodel").health_check()
        self.assertFalse(status.ok)
        self.assertIn("ollama pull othermodel", status.message)

    def test_no_model_configured(self):
        brain = LocalBrain(self.host, "")
        self.assertFalse(brain.health_check().ok)
        with self.assertRaises(BrainUnavailableError):
            brain.chat([{"role": "user", "content": "hi"}])

    def test_unreachable_server_fails_gracefully(self):
        # Port 9 on localhost is essentially never listening.
        brain = LocalBrain("http://127.0.0.1:9", "testmodel", timeout=2)
        status = brain.health_check()
        self.assertFalse(status.ok)
        self.assertIn("Cannot reach Ollama", status.message)
        with self.assertRaises(BrainUnavailableError):
            brain.chat([{"role": "user", "content": "hi"}])


class TextToolCallTests(unittest.TestCase):
    def test_variants(self):
        names = {"web_search"}
        self.assertEqual(extract_text_tool_call('{"name": "web_search", "parameters": {"query": "a"}}', names).arguments,
                         {"query": "a"})
        self.assertIsNotNone(extract_text_tool_call('{"function": {"name": "web_search", "arguments": {}}}', names))
        self.assertIsNone(extract_text_tool_call('{"name": "delete_everything", "arguments": {}}', names))
        self.assertIsNone(extract_text_tool_call("Just a normal answer.", names))
        self.assertIsNone(extract_text_tool_call("{not json}", names))


if __name__ == "__main__":
    unittest.main()
