# NOVA

**NOVA** is a personal AI assistant, like J.A.R.V.I.S., that runs on your own PC.

- **Jarvis-style interface** in your browser: an animated core, chat, system panel, and voice input and output.
- **Acts on your PC:** writes code and opens it in Geany (or any app), opens files, folders, and websites, and runs commands.
- **Searches the internet** and reads web pages.
- **Learns from the internet:** researches a topic and saves what it learned, with sources, to a permanent knowledge base.
- **Email:** reads your inbox and sends mail (Gmail or any IMAP/SMTP provider).
- **Remembers you:** facts about you persist across restarts.
- **Improves itself:** learns lessons from your corrections and follows them from then on.
- **Sets up its own brain:** installs the AI engine and downloads the right model for your PC in one click.
- **Asks before risky actions:** sending email, running commands, touching files outside its own folder.

**Cost: zero.** NOVA's brain is a free, open AI model (Qwen 2.5) that runs **on your PC** through [Ollama](https://ollama.com), which is also free and open source. Nothing you say leaves your computer, except web searches and email you ask for. Web search uses DuckDuckGo and Wikipedia (no API keys), and email uses your own account. NOVA uses only Python's standard library, so there is nothing to `pip install`.

> Claude Code was used to *develop* NOVA. It is not part of NOVA and NOVA doesn't use it.

---

## Quick start (Windows)

### 1. Install Python

Download **Python 3.10+** from https://www.python.org/downloads/. In the installer, tick **"Add python.exe to PATH"**.

### 2. Get NOVA

With Git:

```powershell
cd $HOME\Documents
git clone -b claude/laughing-bardeen-mjzwe3 https://github.com/texacoder/nova.git
```

Or download the branch as a ZIP from GitHub and extract it.

### 3. Start NOVA

Open the `nova` folder and double-click **`start_nova.bat`**, or run `py main.py`.

Your browser opens NOVA at http://127.0.0.1:8765. Keep the black console window open; closing it stops NOVA.

### 4. First run: let NOVA set up its brain

On first start NOVA shows **"Brain offline"** with a **Set up NOVA's brain** button. Click it and NOVA:

1. installs **Ollama**, the free engine that runs AI models (using `winget`, or the official installer if winget isn't available),
2. picks the best model for your PC's memory:

   | Your RAM | Model it downloads | Download size |
   |---|---|---|
   | 16 GB or more | `qwen2.5:7b` | ~4.7 GB |
   | 8–15 GB | `qwen2.5:3b` | ~1.9 GB |
   | less than 8 GB | `qwen2.5:1.5b` | ~1 GB |

3. downloads it with a progress bar and saves the choice in `.env`.

This happens once. After that NOVA starts with its brain ready. If Ollama is already installed with a suitable model, NOVA just uses it.

In terminal mode (`py main.py --cli`) NOVA asks `Set it up now? [Y/n]` instead. You can rerun setup any time with `/setup`.

**Prefer to do it by hand?** Install Ollama from https://ollama.com/download, run `ollama pull qwen2.5:7b`, and set `OLLAMA_MODEL=qwen2.5:7b` in `.env`.

Other ways to start NOVA:

```powershell
py main.py --cli          # terminal only, no browser
py main.py --no-browser   # start the web server without opening a browser
```

## What you can say

```
Write a Python script that renames all .txt files in a folder, and open it in Geany
Search the web for the latest Python release
Learn about how solar panels work
What did you learn about solar panels?
Open my workspace folder
Open youtube.com
Check my latest 5 emails
Send an email to friend@example.com saying I'll be late tonight
How much free disk space do I have?
Remember that my store is called EXORASTORE
From now on, always add comments to code you write      ← NOVA learns this as a lesson
```

When NOVA wants to do something risky, an **Authorisation required** box appears showing exactly what it will do, such as the full command or the full email. Nothing happens until you click **Approve**.

## Commands

These are typed in the chat (or clicked in the Quick commands panel):

| Command | What it does |
|---|---|
| `/help` | Show help |
| `/remember <fact>` | Save a fact to long-term memory |
| `/memories` | List saved memories |
| `/forget <id>` | Delete a memory; `/forget K3` deletes knowledge #K3, `/forget L2` deletes lesson #L2 |
| `/clear_memory` | Delete all memories (asks you to type `yes`) |
| `/learn <topic>` | Research a topic online and save the result |
| `/knowledge` | List what NOVA has learned |
| `/lessons` | List lessons NOVA learned about how you want it to work |
| `/setup` | Install or repair NOVA's brain automatically |
| `/tools` | List NOVA's abilities and which ones ask first |
| `/new` | Start a fresh conversation (memories are kept) |
| `/status` | Brain, memory, email, and workspace status |
| `/exit` | Quit NOVA |

## NOVA's abilities (tools)

| Tool | What it does | Asks you first? |
|---|---|---|
| `web_search` | Search DuckDuckGo (falls back to Wikipedia) | no |
| `fetch_webpage` | Read a web page's text | no |
| `learn_topic` | Search, read 3 pages, summarize, and save to the knowledge base | no |
| `save_knowledge`, `remember`, `recall` | Manage long-term memory and knowledge | no |
| `learn_lesson` | Save a lesson about how to work for you (after corrections or preferences) | no |
| `get_datetime`, `system_info` | Date/time, OS, CPU, disk space | no |
| `list_directory`, `read_file` | Browse and read files | only outside the workspace |
| `write_file` | Create or edit text/code files | only outside the workspace |
| `open_application` | Open apps (Geany, Notepad, Chrome...), optionally with a file | only for apps not in the trusted list or `apps.json` |
| `open_path` | Open a file, folder, or URL with its default program | for programs/scripts and files outside the workspace |
| `run_command` | Run a PowerShell command | **always** |
| `send_email` | Send an email | **always** |
| `read_emails` | Read recent inbox emails (read-only) | no |

**The workspace** is NOVA's own folder, `~/NOVA_Workspace` (for example `C:\Users\you\NOVA_Workspace`). NOVA can create and read files there freely, so code it writes for you goes there.

To skip approval for a tool you trust, list it in `.env`, e.g. `NOVA_AUTO_APPROVE=open_path`. Think twice before auto-approving `run_command` or `send_email`.

### Apps (Geany, VS Code, ...)

NOVA finds apps on your PATH and in common install folders (Geany, VS Code, Chrome, Firefox, Edge, Notepad++). If it says it can't find an app, copy `apps.example.json` to `apps.json` and add the app's full path:

```json
{
  "geany": "C:/Program Files/Geany/bin/geany.exe",
  "spotify": "%APPDATA%/Spotify/Spotify.exe"
}
```

Apps listed in `apps.json` count as trusted and open without asking.

## Email setup (Gmail, free)

1. Turn on **2-Step Verification** in your Google account.
2. Create an **App password** at https://myaccount.google.com/apppasswords.
3. Put your address and the app password in `.env`. Use the app password, not your normal password.
   ```
   EMAIL_ADDRESS=you@gmail.com
   EMAIL_PASSWORD=abcd efgh ijkl mnop
   ```
4. Restart NOVA. `/status` should show your address.

For Outlook or other providers, also set `SMTP_HOST`, `SMTP_PORT`, and `IMAP_HOST`. Your password stays in your local `.env`, which git ignores. NOVA always shows you the full email before sending.

## Voice

- **Voice replies:** click the speaker button. This uses your browser's built-in text-to-speech, which runs offline.
- **Voice input:** click the microphone (Chrome or Edge). The browser's speech recognition is free, but Chrome and Edge send the audio to Google's or Microsoft's servers to convert it to text. If you don't want that, just type. Firefox doesn't support voice input, so the mic button is hidden there.

## How memory and learning work

NOVA has four kinds of memory, all stored locally in `data/nova.db` (SQLite):

1. **Conversation:** the current chat, kept in RAM and cleared on exit or `/new`.
2. **Memories:** facts about you. They're saved when you use `/remember` or when NOVA decides something is worth remembering.
3. **Knowledge:** what NOVA learned from the internet with `/learn` or `learn_topic`, stored with the source URLs.
4. **Lessons:** how you want NOVA to behave. When you correct it ("don't explain so much", "my Geany is on D:"), NOVA saves a lesson and follows it in every future conversation.

On each message, NOVA adds your memories, all lessons, and the most relevant knowledge to what it sends the model.

You can also change NOVA's core personality by editing `personality/nova.txt`. Changes apply on the next message, with no restart needed.

**Honest note on "learning":** the AI model itself isn't retrained; that would need expensive hardware. NOVA learns the way a person keeps notes: it researches, writes a summary, and looks the summary up later. This is free, and you can inspect it (`/knowledge`) and correct it (`/forget K<id>`).

## Safety

- NOVA only listens on `127.0.0.1`, so other computers can't reach it.
- The web page uses a secret token that changes on every start, so other websites open in your browser can't send commands to NOVA.
- Risky actions need your click (see the tools table). The approval box shows exactly what will run.
- The model is told to treat web pages, files, and emails as data, not instructions. A malicious page could still try to trick it, which is exactly why approvals exist. **Read approval requests before clicking Approve.**
- NOVA never needs administrator rights. Don't run it as administrator.
- Everything NOVA does is logged in `data/logs/nova.log`.

## Configuration

All settings live in `.env`; see `.env.example` for descriptions. The most useful ones:

| Setting | Default | Meaning |
|---|---|---|
| `OLLAMA_MODEL` | *(set by automatic setup)* | Model to use, e.g. `qwen2.5:7b` |
| `OLLAMA_NUM_CTX` | `8192` | Context size. Lower it (4096) if replies are slow |
| `OLLAMA_TIMEOUT` | `300` | Seconds to wait for the model |
| `NOVA_WORKSPACE` | `~/NOVA_Workspace` | NOVA's own folder |
| `NOVA_AUTO_APPROVE` | *(empty)* | Tools that skip approval |
| `NOVA_WEB_PORT` | `8765` | Web interface port |
| `NOVA_MAX_TOOL_STEPS` | `8` | Max actions chained per request |

## Troubleshooting

| Problem | Fix |
|---|---|
| "Brain offline" / "Cannot reach Ollama" | Click **Set up NOVA's brain** or type `/setup`. NOVA starts Ollama itself if it's installed |
| Automatic setup fails | Install Ollama from https://ollama.com/download, then click **Try again** |
| "Model ... is not installed" | Type `/setup`, or run `ollama pull <model>` using the exact name in `OLLAMA_MODEL` |
| NOVA chats but never takes actions | Your model doesn't support tools. Use `qwen2.5:7b` or `llama3.1:8b` (`/status` warns about this) |
| Very slow replies | Use a smaller model (`qwen2.5:3b`) or set `OLLAMA_NUM_CTX=4096` |
| "Could not find an app" | Add it to `apps.json` (see above) |
| Web search fails | Check your internet connection. DuckDuckGo sometimes blocks automated searches for a while; NOVA then falls back to Wikipedia |
| Port 8765 in use | Set `NOVA_WEB_PORT=8770` in `.env` |
| Anything else | Check `data\logs\nova.log` |

## Project structure

```
nova/
├── main.py              # Start here: sets everything up, runs the web UI or --cli
├── start_nova.bat       # Double-click launcher for Windows
├── agent.py             # The agent loop: context, tool calls, approvals, commands
├── config.py            # Settings from .env
├── brain/               # The AI model layer (swappable)
│   ├── base.py          #   Brain interface: chat(messages, tools) + health_check()
│   ├── local.py         #   Ollama adapter (native tool calling)
│   └── setup.py         #   Automatic setup: install Ollama, choose + download model
├── tools/               # NOVA's abilities
│   ├── base.py          #   Tool class, registry, approval rules
│   ├── web.py           #   web_search, fetch_webpage
│   ├── knowledge.py     #   remember, recall, learn_topic, save_knowledge
│   ├── files.py         #   list_directory, read_file, write_file
│   ├── apps.py          #   open_application, open_path
│   ├── system.py        #   get_datetime, system_info, run_command
│   └── email_tools.py   #   send_email, read_emails
├── memory/              # SQLite memories + knowledge, conversation memory
├── personality/nova.txt # NOVA's personality and rules: edit freely
├── ui/                  # Web interface (server.py + static/ HTML, CSS, JS)
├── utils/               # Logging and text helpers
├── tests/               # Automated tests
└── data/                # Your database and logs (not in git)
```

How a request flows:

```
You ─► Web UI / CLI ─► Agent ─► Brain (Ollama)
                         ▲  │  "call web_search(...)"
                         │  ▼
                    tool results ◄── Tools (web, files, apps, email, memory)
                         │
                         └─ asks YOU first for risky tools
```

**Adding a new ability:** create a `Tool` subclass in `tools/` with a `name`, `description`, `parameters`, and `run()` method, then add it to `ALL_TOOLS` in `tools/__init__.py`. The model can use it immediately.

**Using a different model server** (llama.cpp, LM Studio...): write a class in `brain/` that implements `chat()` and `health_check()`, and add it to `create_brain()`.

## Running the tests

```powershell
py -m unittest discover -s tests -t . -v
```

The tests don't need Ollama or internet access; they use fake servers, a scripted stand-in brain (`tests/mock_brain.py`, used only by tests), and temporary folders.

## Future ideas

v1.0 covers the core. Natural next steps, all still free:

- Self-coding: NOVA writes, tests, and installs new skills (Python tools) for itself, and can propose changes to its own code with automatic tests and rollback

- Fully offline voice (Whisper for speech-to-text, Piper for text-to-speech) and a wake word
- Smarter memory search with local embeddings (Ollama `nomic-embed-text`)
- Reminders, scheduled tasks, and calendar (CalDAV or Google Calendar)
- Background self-learning on topics you choose
- Seeing your screen (screenshots with a local vision model) and mouse/keyboard control
- Streaming replies word by word
