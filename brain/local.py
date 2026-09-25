"""
LocalBrain: talks to a model running on your own computer through Ollama.

Ollama (https://ollama.com) is free and open-source. Once it is running and
a model is pulled (e.g. `ollama pull llama3.2`), set OLLAMA_MODEL in .env.

Only the Python standard library is used (urllib), so no extra packages
are needed. Everything stays on your machine.
"""

import json
import urllib.error
import urllib.request

from brain.base import Brain, BrainError, BrainStatus, BrainUnavailableError, validate_messages
from utils.logger import get_logger

log = get_logger("brain.local")

SETUP_HINT = (
    "To connect a local brain:\n"
    "  1. Install Ollama from https://ollama.com\n"
    "  2. Pull a model, e.g.:  ollama pull llama3.2\n"
    "  3. Set OLLAMA_MODEL=llama3.2 in your .env file\n"
    "Or set NOVA_BRAIN=mock in .env to test NOVA without a model."
)


class LocalBrain(Brain):
    """A brain backed by an Ollama server (default http://localhost:11434)."""

    def __init__(self, host: str, model: str, timeout: int = 120):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama ({self.model or 'no model set'})"

    # --- Brain interface -------------------------------------------------

    def generate_response(self, messages: list[dict]) -> str:
        validate_messages(messages)
        if not self.model:
            raise BrainUnavailableError("OLLAMA_MODEL is not set.\n" + SETUP_HINT)

        payload = {"model": self.model, "messages": messages, "stream": False}
        data = self._request("/api/chat", payload)

        try:
            reply = data["message"]["content"]
        except (KeyError, TypeError):
            log.error("Unexpected Ollama response: %r", data)
            raise BrainError("The local model returned a response NOVA did not understand.")
        return reply.strip()

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
        return BrainStatus(True, f"Connected to Ollama at {self.host} using '{self.model}'.")

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _is_installed(model: str, installed: list[str]) -> bool:
        # "llama3.2" and "llama3.2:latest" refer to the same model.
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
            raise BrainError(f"Ollama returned an error (HTTP {error.code}).") from error
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
            log.warning("Cannot reach Ollama at %s: %s", url, error)
            raise BrainUnavailableError(
                f"Cannot reach Ollama at {self.host}. Is it installed and running?\n" + SETUP_HINT
            ) from error
        except json.JSONDecodeError as error:
            log.error("Invalid JSON from Ollama at %s: %s", url, error)
            raise BrainError("The local model server sent an invalid response.") from error
