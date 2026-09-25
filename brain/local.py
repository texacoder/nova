"""
LocalBrain: talks to a model running on your own computer through Ollama.

Ollama (https://ollama.com) is free and open-source. Once it is running and
a model is pulled (e.g. `ollama pull qwen2.5:7b`), set OLLAMA_MODEL in .env.

Only the Python standard library is used (urllib), so no extra packages
are needed. Everything stays on your machine.
"""

import json
import re
import urllib.error
import urllib.request

from brain.base import (
    Brain, BrainError, BrainReply, BrainStatus, BrainUnavailableError, ToolCall, validate_messages,
)
from utils.logger import get_logger

log = get_logger("brain.local")

SETUP_HINT = (
    "To connect a local brain:\n"
    "  1. Install Ollama from https://ollama.com\n"
    "  2. Pull a model that supports tools, e.g.:  ollama pull qwen2.5:7b\n"
    "  3. Set OLLAMA_MODEL=qwen2.5:7b in your .env file\n"
    "NOVA can do all of this for you: use the 'Set up brain' button or type /setup."
)


class OllamaHTTPError(BrainError):
    def __init__(self, message: str, code: int, detail: str):
        super().__init__(message)
        self.code = code
        self.detail = detail


class LocalBrain(Brain):
    """A brain backed by an Ollama server (default http://localhost:11434)."""

    def __init__(self, host: str, model: str, timeout: int = 300, num_ctx: int = 8192):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.supports_tools = True

    @property
    def name(self) -> str:
        return f"ollama ({self.model or 'no model set'})"

    # --- Brain interface -------------------------------------------------

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> BrainReply:
        validate_messages(messages)
        if not self.model:
            raise BrainUnavailableError("OLLAMA_MODEL is not set.\n" + SETUP_HINT)

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": self.num_ctx},
        }
        use_tools = bool(tools) and self.supports_tools
        if use_tools:
            payload["tools"] = tools

        try:
            data = self._request("/api/chat", payload)
        except OllamaHTTPError as error:
            if use_tools and "does not support tools" in error.detail:
                # Older/smaller models can't use tools. Keep chatting without them.
                log.warning("Model %s does not support tools; continuing without tools", self.model)
                self.supports_tools = False
                return self.chat(messages, None)
            raise

        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict):
            log.error("Unexpected Ollama response: %r", data)
            raise BrainError("The local model returned a response NOVA did not understand.")

        content = (message.get("content") or "").strip()
        tool_calls = [self._parse_tool_call(c) for c in message.get("tool_calls") or []]
        tool_calls = [c for c in tool_calls if c is not None]

        # Some small models write the tool call as JSON text instead of using
        # the proper field. Recognise that so the tool still runs.
        if not tool_calls and use_tools:
            text_call = extract_text_tool_call(content, {t["function"]["name"] for t in tools})
            if text_call:
                return BrainReply("", [text_call])

        return BrainReply(content, tool_calls)

    def health_check(self) -> BrainStatus:
        try:
            data = self._request("/api/tags", timeout=5)
        except BrainError as error:
            return BrainStatus(False, str(error))

        installed = [m.get("name", "") for m in data.get("models", [])]
        if not self.model:
            return BrainStatus(False, "Ollama is running, but OLLAMA_MODEL is not set.\n" + SETUP_HINT)
        if not self._is_installed(self.model, installed):
            available = ", ".join(installed) or "none"
            return BrainStatus(
                False,
                f"Model '{self.model}' is not installed in Ollama (installed: {available}).\n"
                f"Run:  ollama pull {self.model}",
            )

        message = f"Connected to Ollama at {self.host} using '{self.model}'."
        # Newer Ollama versions report what a model can do.
        try:
            info = self._request("/api/show", {"model": self.model}, timeout=10)
            capabilities = info.get("capabilities")
            if isinstance(capabilities, list) and "tools" not in capabilities:
                self.supports_tools = False
                message += (" Warning: this model cannot use tools, so NOVA can chat but not act."
                            " Try qwen2.5:7b or llama3.1:8b.")
        except BrainError:
            pass  # not essential
        return BrainStatus(True, message)

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _parse_tool_call(raw: dict) -> ToolCall | None:
        function = raw.get("function") if isinstance(raw, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            return None
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return ToolCall(function["name"], arguments)

    @staticmethod
    def _is_installed(model: str, installed: list[str]) -> bool:
        # "qwen2.5" and "qwen2.5:latest" refer to the same model.
        wanted = model if ":" in model else f"{model}:latest"
        return model in installed or wanted in installed

    def _request(self, path: str, payload: dict | None = None, timeout: int | None = None) -> dict:
        """GET (no payload) or POST JSON to the Ollama server and return parsed JSON."""
        url = self.host + path
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            log.error("Ollama HTTP %s at %s: %s", error.code, url, detail)
            if error.code == 404:
                raise BrainUnavailableError(
                    f"Ollama could not find model '{self.model}'. Run:  ollama pull {self.model}"
                ) from error
            raise OllamaHTTPError(
                f"Ollama returned an error (HTTP {error.code}): {detail[:200]}", error.code, detail
            ) from error
        except TimeoutError as error:
            log.warning("Ollama timed out at %s", url)
            raise BrainError(
                "The local model took too long to answer. Try a smaller model or raise OLLAMA_TIMEOUT."
            ) from error
        except (urllib.error.URLError, ConnectionError, OSError) as error:
            log.warning("Cannot reach Ollama at %s: %s", url, error)
            raise BrainUnavailableError(
                f"Cannot reach Ollama at {self.host}. Is it installed and running?\n" + SETUP_HINT
            ) from error
        except json.JSONDecodeError as error:
            log.error("Invalid JSON from Ollama at %s: %s", url, error)
            raise BrainError("The local model server sent an invalid response.") from error


def extract_text_tool_call(content: str, tool_names: set[str]) -> ToolCall | None:
    """
    Turn text like {"name": "web_search", "arguments": {"query": "x"}}
    (optionally inside ``` fences) into a ToolCall, if it names a real tool.
    """
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("function"), dict):  # {"function": {"name":..., "arguments":...}}
        data = data["function"]
    name = data.get("name")
    arguments = data.get("arguments", data.get("parameters", {}))
    if name in tool_names and isinstance(arguments, dict):
        return ToolCall(name, arguments)
    return None
