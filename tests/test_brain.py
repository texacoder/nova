"""Tests for the Brain interface, the mock brain and the Ollama adapter."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from brain import Brain, BrainError, BrainUnavailableError, LocalBrain, MockBrain, create_brain
from tests.helpers import make_config, temp_dir


class BrainInterfaceTests(unittest.TestCase):
    def test_brain_is_abstract(self):
        with self.assertRaises(TypeError):
            Brain()

    def test_incomplete_brain_cannot_be_created(self):
        class HalfBrain(Brain):
            def generate_response(self, messages):
                return "hi"
        with self.assertRaises(TypeError):
            HalfBrain()

    def test_factory(self):
        with temp_dir() as tmp:
            self.assertIsInstance(create_brain(make_config(tmp, NOVA_BRAIN="mock")), MockBrain)
            self.assertIsInstance(create_brain(make_config(tmp, NOVA_BRAIN="ollama")), LocalBrain)


class MockBrainTests(unittest.TestCase):
    def setUp(self):
        self.brain = MockBrain()

    def test_health_check(self):
        self.assertTrue(self.brain.health_check().ok)

    def test_greeting(self):
        reply = self.brain.generate_response([{"role": "user", "content": "hello"}])
        self.assertIn("NOVA", reply)

    def test_uses_memory_from_system_prompt(self):
        messages = [
            {"role": "system", "content": "You are NOVA.\n- [1] My store is called EXORASTORE."},
            {"role": "user", "content": "What is my store called?"},
        ]
        self.assertIn("EXORASTORE", self.brain.generate_response(messages))

    def test_rejects_invalid_messages(self):
        for bad in ([], [{"role": "robot", "content": "x"}], [{"role": "user"}], ["hi"]):
            with self.subTest(bad=bad), self.assertRaises(BrainError):
                self.brain.generate_response(bad)


class FakeOllama(BaseHTTPRequestHandler):
    """A tiny stand-in for the Ollama HTTP API."""

    def _send(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"models": [{"name": "testmodel:latest"}]})

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.last_request = request
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

    def test_generate_response(self):
        brain = LocalBrain(self.host, "testmodel")
        reply = brain.generate_response([{"role": "user", "content": "ping"}])
        self.assertEqual(reply, "echo: ping")
        self.assertEqual(FakeOllama.last_request["model"], "testmodel")
        self.assertFalse(FakeOllama.last_request["stream"])

    def test_health_check_ok(self):
        self.assertTrue(LocalBrain(self.host, "testmodel").health_check().ok)

    def test_health_check_missing_model(self):
        status = LocalBrain(self.host, "othermodel").health_check()
        self.assertFalse(status.ok)
        self.assertIn("ollama pull othermodel", status.message)

    def test_no_model_configured(self):
        brain = LocalBrain(self.host, "")
        self.assertFalse(brain.health_check().ok)
        with self.assertRaises(BrainUnavailableError):
            brain.generate_response([{"role": "user", "content": "hi"}])

    def test_unreachable_server_fails_gracefully(self):
        # Port 9 on localhost is essentially never listening.
        brain = LocalBrain("http://127.0.0.1:9", "testmodel", timeout=2)
        status = brain.health_check()
        self.assertFalse(status.ok)
        self.assertIn("Cannot reach Ollama", status.message)
        with self.assertRaises(BrainUnavailableError):
            brain.generate_response([{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()
