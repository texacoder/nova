"""
JARVIS's web interface: a small local web server (standard library only).

It serves the page in ui/static/ and a JSON API the page talks to:

    GET  /api/status     -> brain, memory and tool status
    GET  /api/memories   -> saved memories and learned knowledge
    POST /api/message    -> {"text": "..."}      send a message / command
    POST /api/confirm    -> {"approve": true}     answer a pending approval
    POST /api/message/stream, /api/confirm/stream
                         -> same, but the reply streams in as it's written
                            (one JSON object per line; the last is {"type": "done", ...})
    GET  /api/setup      -> progress of the automatic brain setup
    POST /api/setup      -> start the automatic brain setup

Security:
  - It only listens on 127.0.0.1 (your own PC), not the network.
  - Every API call needs a secret token that is created at startup and
    embedded in the page, so other websites open in your browser can't
    send commands to JARVIS.
  - The Host header is checked to block "DNS rebinding" tricks.
"""

import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from utils.logger import get_logger

log = get_logger("ui")

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
MAX_BODY_BYTES = 1_000_000


class JarvisWebServer:
    def __init__(self, agent, config):
        self.agent = agent
        self.config = config
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()  # the agent handles one request at a time
        self.httpd = ThreadingHTTPServer((config.web_host, config.web_port), self._make_handler())
        self.port = self.httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # --- request handling ---------------------------------------------------

    def _allowed_hosts(self) -> set[str]:
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if self.config.web_host not in ("127.0.0.1", "localhost", "0.0.0.0", ""):
            hosts.add(f"{self.config.web_host}:{self.port}")
        return hosts

    def _render_index(self) -> bytes:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        for key, value in {"TOKEN": self.token, "NAME": self.config.name,
                           "VERSION": self.config.version}.items():
            html = html.replace("{{" + key + "}}", value)
        return html.encode("utf-8")

    def _api(self, method: str, path: str, body: dict) -> dict:
        agent = self.agent
        if path == "/api/setup":  # outside the lock: must answer while JARVIS is busy
            if agent.setup is None:
                return {"available": False, "state": "unavailable", "message": "Automatic setup is not available."}
            if method == "POST":
                agent.setup.start()
            return {"available": True, **agent.setup.snapshot()}
        with self.lock:
            if method == "GET" and path == "/api/status":
                status = agent.status()
                return {
                    "name": self.config.name,
                    "version": self.config.version,
                    "brain_ok": status["Brain status"].startswith("ready"),
                    "status": status,
                    "pending": vars(agent.pending) if agent.pending else None,
                }
            if method == "GET" and path == "/api/memories":
                store = agent.memory_store
                return {
                    "memories": [vars(m) for m in store.list()],
                    "knowledge": [vars(k) for k in store.list_knowledge()],
                }
            if method == "POST" and path == "/api/message":
                reply = agent.handle(str(body.get("text", "")), voice=bool(body.get("voice")))
                if reply.exit:
                    threading.Thread(target=self.shutdown, daemon=True).start()
                return reply.to_dict()
            if method == "POST" and path == "/api/confirm":
                return agent.resolve_pending(bool(body.get("approve"))).to_dict()
        raise LookupError(path)

    def _stream(self, handler, path: str, body: dict) -> None:
        """Run the agent and send its live updates as they happen (newline-delimited JSON)."""
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "application/x-ndjson")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()

        def write(event: dict) -> None:
            handler.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
            handler.wfile.flush()

        try:
            with self.lock:
                if path == "/api/message/stream":
                    reply = self.agent.handle(str(body.get("text", "")), on_event=write,
                                              voice=bool(body.get("voice")))
                else:
                    reply = self.agent.resolve_pending(bool(body.get("approve")), on_event=write)
            if reply.exit:
                threading.Thread(target=self.shutdown, daemon=True).start()
            write({"type": "done", "reply": reply.to_dict()})
        except (BrokenPipeError, ConnectionResetError):
            log.info("Browser disconnected during a streamed reply")
        except Exception:
            log.exception("Streaming error on %s", path)
            try:
                write({"type": "done", "reply": {
                    "text": f"Something unexpected went wrong. Details are in {self.config.log_file}.",
                    "steps": [], "pending": None, "exit": False, "restart": False}})
            except OSError:
                pass

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # send access logs to our log file
                log.debug("%s - %s", self.address_string(), fmt % args)

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, status: int, data: dict) -> None:
                self._send(status, json.dumps(data).encode("utf-8"), "application/json")

            def _handle(self, method: str) -> None:
                if self.headers.get("Host", "") not in server._allowed_hosts():
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "Host not allowed"})
                    return
                path = self.path.split("?", 1)[0]

                if method == "GET" and path in ("/", "/index.html"):
                    self._send(HTTPStatus.OK, server._render_index(), "text/html; charset=utf-8")
                    return
                if method == "GET" and path.startswith("/static/"):
                    name = path[len("/static/"):]
                    if name in STATIC_FILES:  # fixed list: no path tricks possible
                        self._send(HTTPStatus.OK, (STATIC_DIR / name).read_bytes(), STATIC_FILES[name])
                    else:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                    return
                if not path.startswith("/api/"):
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                    return

                if not secrets.compare_digest(self.headers.get("X-Jarvis-Token", ""), server.token):
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "Missing or wrong token"})
                    return

                body = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > MAX_BODY_BYTES:
                        self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Too large"})
                        return
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except json.JSONDecodeError:
                        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
                        return
                    if not isinstance(body, dict):
                        body = {}

                if method == "POST" and path in ("/api/message/stream", "/api/confirm/stream"):
                    server._stream(self, path, body)
                    return
                try:
                    self._send_json(HTTPStatus.OK, server._api(method, path, body))
                except LookupError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown API endpoint"})
                except Exception:
                    log.exception("Web API error on %s %s", method, path)
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "text": f"Something unexpected went wrong. Details are in {server.config.log_file}.",
                        "steps": [], "pending": None, "exit": False,
                    })

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        return Handler
