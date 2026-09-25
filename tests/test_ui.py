"""Tests for the local web interface server."""

import http.client
import json
import re
import threading
import unittest

from agent import Agent
from tests.mock_brain import MockBrain
from memory import MemoryStore
from tests.helpers import make_config, temp_dir
from ui.server import NovaWebServer


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        config = make_config(self.tmp.name, NOVA_WEB_PORT="1")
        config.web_port = 0  # let the OS pick a free port
        agent = Agent(config, MockBrain(), MemoryStore(config.db_path), "You are NOVA.")
        self.server = NovaWebServer(agent, config)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.server.port}"}
        if token:
            headers["X-Nova-Token"] = self.server.token
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        content = response.read().decode()
        conn.close()
        return response.status, content

    def test_index_contains_token_and_security_headers(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        conn.request("GET", "/")
        response = conn.getresponse()
        html = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn(self.server.token, html)
        self.assertIn("NOVA", html)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertIsNone(re.search(r"\{\{\w+\}\}", html))  # all placeholders filled

    def test_static_files_whitelist(self):
        self.assertEqual(self.request("GET", "/static/app.js", token=False)[0], 200)
        self.assertEqual(self.request("GET", "/static/style.css", token=False)[0], 200)
        self.assertEqual(self.request("GET", "/static/../server.py", token=False)[0], 404)
        self.assertEqual(self.request("GET", "/static/index.html", token=False)[0], 404)

    def test_api_requires_token(self):
        status, _ = self.request("POST", "/api/message", {"text": "/remember hacked"}, token=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.server.agent.memory_store.count(), 0)

    def test_wrong_host_is_rejected(self):
        status, _ = self.request("GET", "/api/status", host="evil.example.com")
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/", token=False, host=f"evil.example.com:{self.server.port}")
        self.assertEqual(status, 403)

    def test_message_status_and_memories(self):
        status, content = self.request("POST", "/api/message", {"text": "/remember I like tea."})
        self.assertEqual(status, 200)
        self.assertIn("I'll remember", json.loads(content)["text"])
        data = json.loads(self.request("GET", "/api/status")[1])
        self.assertTrue(data["brain_ok"])
        self.assertEqual(data["status"]["Memories"], "1 saved")
        memories = json.loads(self.request("GET", "/api/memories")[1])["memories"]
        self.assertEqual(memories[0]["content"], "I like tea.")

    def test_tool_steps_and_approval_flow(self):
        reply = json.loads(self.request("POST", "/api/message", {"text": "what time is it?"})[1])
        self.assertEqual(reply["steps"][0]["tool"], "get_datetime")

        reply = json.loads(self.request("POST", "/api/message", {"text": "open powershell"})[1])
        self.assertEqual(reply["pending"]["tool"], "open_application")
        status = json.loads(self.request("GET", "/api/status")[1])
        self.assertIsNotNone(status["pending"])  # a reloaded page can show the approval again

        reply = json.loads(self.request("POST", "/api/confirm", {"approve": False})[1])
        self.assertIsNone(reply["pending"])
        self.assertEqual(reply["steps"][-1]["status"], "declined")

    def test_setup_endpoint(self):
        data = json.loads(self.request("GET", "/api/setup")[1])
        self.assertFalse(data["available"])  # the fake brain can't be installed
        self.assertEqual(self.request("POST", "/api/setup", {}, token=False)[0], 403)

    def test_bad_requests(self):
        self.assertEqual(self.request("GET", "/api/nothing")[0], 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        conn.request("POST", "/api/message", body="{not json", headers={
            "X-Nova-Token": self.server.token, "Host": f"127.0.0.1:{self.server.port}"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()


if __name__ == "__main__":
    unittest.main()
