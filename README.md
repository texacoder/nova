# NOVA

**NOVA** is a personal AI assistant that runs entirely on your own computer.

Version 0.1 is the foundation. It gives you:

- a terminal chat interface
- a configurable personality
- persistent long-term memory (SQLite)
- short-term memory of the current conversation
- a "brain" interface so any local AI model can be plugged in (Ollama first)
- logging, error handling, and configuration files

NOVA uses no paid APIs or online AI services. The AI model runs on your own PC through [Ollama](https://ollama.com), which is free and open source.
Claude Code was used to *develop* NOVA. It is not part of NOVA.

---

## Project structure

```
nova/
├── main.py              # Start here: the terminal interface (CLI)
├── agent.py             # Agent engine: commands, context building, talking to the brain
├── config.py            # Loads settings from .env and environment variables
│
├── brain/               # The "brain" (AI model) layer
│   ├── base.py          # Brain interface: generate_response() + health_check()
│   ├── local.py         # LocalBrain: connects to Ollama on your PC
│   └── mock.py          # MockBrain: simple fake brain for testing without a model
│
├── memory/
│   ├── database.py      # Long-term memory in SQLite (survives restarts)
│   └── conversation.py  # Short-term memory of the current chat (RAM only)
│
├── personality/
│   ├── nova.txt         # NOVA's personality: edit this to change how NOVA behaves
│   └── __init__.py      # Loads nova.txt
│
├── tools/               # Groundwork for future abilities (empty in v0.1)
├── utils/logger.py      # Logging to data/logs/nova.log
├── tests/               # Automated tests
├── data/                # Your local database and logs (not committed to git)
├── .env.example         # Example settings: copy it to .env
└── requirements.txt     # No packages needed in v0.1
```

How the parts fit together:

```
You ──► main.py (CLI) ──► agent.py ──► brain (Ollama / mock)
                              │
                              ├── memory (SQLite + current conversation)
                              └── personality/nova.txt
```

## Requirements

- **Python 3.10 or newer** (developed and tested on 3.11).
- Nothing else. v0.1 uses only Python's standard library.
- Optional: [Ollama](https://ollama.com) for a real AI brain (see below).
- Optional: `pytest`, if you prefer it to the built-in test runner.

Check your Python version:

```bash
python --version      # on some systems: python3 --version
```

## Installation

```bash
git clone <your-repo-url> nova
cd nova
cp .env.example .env          # Windows: copy .env.example .env
```

That's it. There is nothing to `pip install`.

## Running NOVA

```bash
python main.py
```

If you haven't set up Ollama yet, NOVA starts, tells you the brain isn't ready, and explains what to do. Memory commands still work.

To try NOVA without any AI model, set this in `.env`:

```
NOVA_BRAIN=mock
```

The mock brain is **not** intelligent. It greets you and answers questions by matching words against your saved memories and earlier messages. Its replies start with `[mock]` so you always know it's the mock brain.

Example session (mock brain):

```
========================================
NOVA v0.1
Personal AI Assistant
========================================

You: /remember My store is called EXORASTORE.

NOVA:
I'll remember that. (memory #1)

You: /exit
```

…restart NOVA…

```
You: What is my store called?

NOVA:
[mock] From what I know: My store is called EXORASTORE.
```

## Commands

| Command | What it does |
|---|---|
| `/help` | Show the command list |
| `/remember <fact>` | Save a fact to long-term memory |
| `/memories` | List saved memories with their ids |
| `/forget <id>` | Delete one memory, e.g. `/forget 3` |
| `/clear_memory` | Delete all memories (asks you to type `yes`) |
| `/status` | Show brain status, memory count, and file locations |
| `/exit` | Quit (Ctrl+C or Ctrl+D also work) |

Anything that doesn't start with `/` is sent to NOVA as a chat message.

## How memory works

NOVA has two kinds of memory:

1. **Short-term (conversation) memory:** the messages from the current session, kept in RAM so NOVA can follow the conversation ("My project is called NOVA" → "What is my project called?"). Only the latest `NOVA_MAX_HISTORY` messages are kept. It is **not** saved to disk and disappears when you exit.

2. **Long-term memory:** facts you save with `/remember`. They are stored in `data/nova.db`, a SQLite database with `id`, `content`, and `created_at` columns, so they survive restarts.

On every message, NOVA builds the context it sends to the brain:

```
[system]  personality (nova.txt)
          + "Things you remember about the user:"
            - [1] My store is called EXORASTORE.
            ...
[user / assistant]  recent conversation
```

NOVA only saves memories when you explicitly ask it to. It doesn't store whole conversations. All memory access goes through the `MemoryStore` class in `memory/database.py`, so smarter memory (like semantic or vector search) can be added later without changing the rest of NOVA.

## Configuration

All settings are in `.env` (see `.env.example` for descriptions). Real environment variables override the file.

| Setting | Default | Meaning |
|---|---|---|
| `NOVA_NAME` | `NOVA` | Assistant's name |
| `NOVA_VERSION` | `0.1` | Version shown in the banner |
| `NOVA_BRAIN` | `ollama` | `ollama` or `mock` |
| `OLLAMA_HOST` | `http://localhost:11434` | Where Ollama is running |
| `OLLAMA_MODEL` | *(empty)* | The model to use, e.g. `llama3.2` |
| `OLLAMA_TIMEOUT` | `120` | Seconds to wait for a reply |
| `NOVA_DATA_DIR` | `data` | Folder for the database and logs |
| `NOVA_PERSONALITY_FILE` | `personality/nova.txt` | Personality file |
| `NOVA_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `NOVA_MAX_HISTORY` | `20` | Conversation messages kept in context |
| `NOVA_MAX_MEMORIES_IN_PROMPT` | `50` | Saved memories included in each prompt |

Relative paths are resolved from the project folder. If a setting is invalid, NOVA tells you which one and stops.

## Logging and errors

Technical details, including full error tracebacks, go to `data/logs/nova.log`. The terminal only shows short, friendly messages. If something goes wrong, check that log file.

## Connecting the local brain (Ollama)

The brain code is already written (`brain/local.py`). To use it:

1. Install Ollama: <https://ollama.com/download>
2. Pull a model (pick one that fits your PC's RAM/GPU):
   ```bash
   ollama pull llama3.2        # ~2 GB, good starting point
   ```
3. In `.env`:
   ```
   NOVA_BRAIN=ollama
   OLLAMA_MODEL=llama3.2
   ```
4. Run `python main.py`, then type `/status`. It should say `Brain status: ready`.

### Adding a different brain later

Every brain follows the interface in `brain/base.py`:

```python
class Brain(ABC):
    def generate_response(self, messages: list[dict]) -> str: ...
    def health_check(self) -> BrainStatus: ...
```

To support another local model server (for example llama.cpp or LM Studio), create a new class in `brain/` that implements those two methods, then add it to `create_brain()` in `brain/__init__.py`. Nothing else needs to change.

## Running the tests

```bash
python -m unittest discover -s tests -t . -v
# or, if you installed pytest:
pytest
```

The tests cover database creation, saving/retrieving/deleting memories, conversation context, configuration loading, and the brain interface (including a fake Ollama server). They use temporary folders, so your real memories are never touched.

## Safety in v0.1

NOVA v0.1 cannot run shell commands, read or change files, change system settings, browse the internet, send email, install software, or access credentials. Its only side effects are writing its own database and log file inside `data/`. It doesn't need administrator rights. The `tools/` package is empty on purpose. Future abilities will be added there one at a time, and NOVA can only use tools that are explicitly registered.

## Roadmap

**v0.1**
- CLI
- Personality
- Persistent memory
- Brain abstraction

**v0.2**
- Local LLM through Ollama
- Better contextual memory

**v0.3**
- Internet search
- Web browsing

**v0.4**
- Computer control
- Application launching
- File operations

**v0.5**
- Voice input/output
- Wake word

**v0.6**
- Email and calendar

**v0.7**
- Autonomous learning/research system

**v1.0**
- Full personal AI agent
