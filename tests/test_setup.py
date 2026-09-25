"""Tests for the automatic brain setup (brain/setup.py)."""

import json
import os
import platform
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from brain import setup
from brain.local import LocalBrain
from brain.setup import SetupError, SetupManager
from tests.helpers import temp_dir


class FakeOllamaState:
    def __init__(self, models=None, pull_error=None):
        self.models = list(models or [])
        self.pull_error = pull_error
        self.pulled = []


def make_handler(state: FakeOllamaState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._json({"models": [{"name": m} for m in state.models]})

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            if self.path == "/api/show":
                self._json({"capabilities": ["completion", "tools"]})
            elif self.path == "/api/pull":
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                if state.pull_error:
                    self.wfile.write(json.dumps({"error": state.pull_error}).encode() + b"\n")
                    return
                events = [{"status": "pulling manifest"},
                          {"status": "pulling abc", "total": 100, "completed": 40},
                          {"status": "pulling abc", "total": 100, "completed": 100},
                          {"status": "success"}]
                for event in events:
                    self.wfile.write(json.dumps(event).encode() + b"\n")
                model = request["model"]
                state.pulled.append(model)
                state.models.append(model if ":" in model else model + ":latest")
            else:
                self._json({"message": {"role": "assistant", "content": "hi"}})

    return Handler


class FakeServerTestCase(unittest.TestCase):
    def start_fake_ollama(self, state, port=0):
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"


class HelperTests(unittest.TestCase):
    def test_recommended_model_by_ram(self):
        self.assertEqual(setup.recommended_model(32), "qwen2.5:7b")
        self.assertEqual(setup.recommended_model(16), "qwen2.5:7b")
        self.assertEqual(setup.recommended_model(8), "qwen2.5:3b")
        self.assertEqual(setup.recommended_model(4), "qwen2.5:1.5b")
        self.assertEqual(setup.recommended_model(0), "qwen2.5:1.5b")

    def test_ram_detection(self):
        ram = setup.total_ram_gb()
        self.assertGreaterEqual(ram, 0)
        if platform.system() in ("Linux", "Windows"):
            self.assertGreater(ram, 0.5)

    def test_pick_installed_model(self):
        self.assertEqual(setup.pick_installed_model(["gemma:2b", "llama3.1:8b", "qwen2.5:3b"]), "qwen2.5:3b")
        self.assertEqual(setup.pick_installed_model(["llama3.2:latest"]), "llama3.2:latest")
        self.assertIsNone(setup.pick_installed_model(["gemma:2b"]))
        self.assertIsNone(setup.pick_installed_model([]))

    def test_save_env_value(self):
        with temp_dir() as tmp:
            env = Path(tmp) / ".env"
            setup.save_env_value(env, "OLLAMA_MODEL", "a")  # creates the file
            self.assertEqual(env.read_text(), "OLLAMA_MODEL=a\n")
            env.write_text("# settings\nNOVA_NAME=NOVA\nOLLAMA_MODEL=\n")
            setup.save_env_value(env, "OLLAMA_MODEL", "qwen2.5:7b")
            self.assertEqual(env.read_text(), "# settings\nNOVA_NAME=NOVA\nOLLAMA_MODEL=qwen2.5:7b\n")

    def test_install_outside_windows_explains(self):
        with mock.patch.object(setup, "IS_WINDOWS", False):
            with self.assertRaises(SetupError) as caught:
                setup.install_ollama()
        self.assertIn("ollama.com", str(caught.exception))


class PullTests(FakeServerTestCase):
    def test_pull_reports_progress(self):
        state = FakeOllamaState()
        host = self.start_fake_ollama(state)
        seen = []
        setup.pull_model(host, "qwen2.5:3b", lambda text, fraction: seen.append((text, fraction)))
        self.assertEqual(state.pulled, ["qwen2.5:3b"])
        self.assertIn(("pulling abc", 0.4), seen)
        self.assertEqual(seen[-1][0], "success")

    def test_pull_error(self):
        host = self.start_fake_ollama(FakeOllamaState(pull_error="disk full"))
        with self.assertRaises(SetupError) as caught:
            setup.pull_model(host, "qwen2.5:3b")
        self.assertIn("disk full", str(caught.exception))


class SetupManagerTests(FakeServerTestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.env = Path(self.tmp.name) / ".env"
        self.env.write_text("OLLAMA_MODEL=\n")

    def test_full_setup_downloads_recommended_model(self):
        state = FakeOllamaState()
        brain = LocalBrain(self.start_fake_ollama(state), "")
        manager = SetupManager(brain, self.env)
        self.assertFalse(brain.health_check().ok)
        with mock.patch.object(setup, "recommended_model", return_value="qwen2.5:3b"):
            result = manager.run_blocking()
        self.assertEqual(result["state"], "done", result)
        self.assertEqual(state.pulled, ["qwen2.5:3b"])
        self.assertEqual(brain.model, "qwen2.5:3b")
        self.assertIn("OLLAMA_MODEL=qwen2.5:3b", self.env.read_text())
        self.assertTrue(brain.health_check().ok)

    def test_background_setup_and_status(self):
        brain = LocalBrain(self.start_fake_ollama(FakeOllamaState()), "")
        manager = SetupManager(brain, self.env)
        with mock.patch.object(setup, "recommended_model", return_value="qwen2.5:1.5b"):
            self.assertTrue(manager.start())
            manager.thread.join(timeout=20)
        self.assertEqual(manager.snapshot()["state"], "done")

    def test_uses_installed_model_without_downloading(self):
        state = FakeOllamaState(models=["llama3.1:8b"])
        brain = LocalBrain(self.start_fake_ollama(state), "")
        manager = SetupManager(brain, self.env)
        self.assertTrue(manager.quick_start())
        self.assertEqual(brain.model, "llama3.1:8b")
        self.assertEqual(state.pulled, [])
        self.assertIn("OLLAMA_MODEL=llama3.1:8b", self.env.read_text())

    def test_download_failure_is_reported(self):
        brain = LocalBrain(self.start_fake_ollama(FakeOllamaState(pull_error="no internet")), "qwen2.5:3b")
        result = SetupManager(brain, self.env).run_blocking()
        self.assertEqual(result["state"], "error")
        self.assertIn("no internet", result["message"])

    def test_not_installed_and_install_fails(self):
        brain = LocalBrain("http://127.0.0.1:9", "")
        manager = SetupManager(brain, self.env)
        with mock.patch.object(setup, "find_ollama", return_value=None), \
             mock.patch.object(setup, "install_ollama", side_effect=SetupError("please install manually")):
            self.assertTrue(manager.needs_install())
            self.assertFalse(manager.quick_start())
            result = manager.run_blocking()
        self.assertEqual(result, {**result, "state": "error", "message": "please install manually"})

    @unittest.skipIf(platform.system() == "Windows", "uses a shell-script stand-in for ollama")
    def test_starts_installed_but_stopped_ollama(self):
        with socket.socket() as probe:  # find a free port for the fake server
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        fake_ollama = Path(self.tmp.name) / "ollama"
        fake_ollama.write_text(
            f"#!{sys.executable}\n"
            "import sys, json\n"
            "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def log_message(self, *a): pass\n"
            "    def do_GET(self):\n"
            "        b = json.dumps({'models': [{'name': 'qwen2.5:7b'}]}).encode()\n"
            "        self.send_response(200); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)\n"
            "    def do_POST(self):\n"
            "        self.rfile.read(int(self.headers['Content-Length']))\n"
            "        b = b'{}'\n"
            "        self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b)\n"
            "server = HTTPServer(('127.0.0.1', " + str(port) + "), H)\n"
            "server.timeout = 15\n"
            "for _ in range(6): server.handle_request()\n"
        )
        os.chmod(fake_ollama, 0o755)
        brain = LocalBrain(f"http://127.0.0.1:{port}", "")
        with mock.patch.object(setup, "find_ollama", return_value=str(fake_ollama)):
            self.assertTrue(SetupManager(brain, self.env).quick_start())
        self.assertEqual(brain.model, "qwen2.5:7b")


if __name__ == "__main__":
    unittest.main()
