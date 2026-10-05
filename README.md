# JARVIS

**JARVIS** is a personal AI assistant that runs on your own PC.

- **Futuristic HUD interface** in your browser: an animated core, chat, system panel, and voice input and output.
- **Acts on your PC:** writes code and opens it in Geany (or any app), opens files, folders, and websites, and runs commands.
- **Searches the internet** and reads web pages.
- **Learns from the internet:** researches a topic and saves what it learned, with sources, to a permanent knowledge base.
- **Email:** reads your inbox and sends mail (Gmail or any IMAP/SMTP provider).
- **Remembers you:** facts about you persist across restarts.
- **Improves itself:** writes new skills (Python tools) for itself, edits its own source code (tested, backed up, and rolled back if anything breaks), and learns lessons from your corrections.
- **Sets up its own brain:** installs the AI engine and downloads the right model for your PC in one click.
- **Asks before risky actions:** sending email, running commands, touching files outside its own folder.

**Cost: zero.** JARVIS's brain is a free, open AI model (Qwen 2.5) that runs **on your PC** through [Ollama](https://ollama.com), which is also free and open source. Nothing you say leaves your computer, except web searches and email you ask for. Web search uses DuckDuckGo and Wikipedia (no API keys), and email uses your own account. JARVIS uses only Python's standard library, so there is nothing to `pip install`.

> Claude Code was used to *develop* JARVIS. It is not part of JARVIS and JARVIS doesn't use it.

---

## Quick start (Windows)

### 1. Install Python

Download **Python 3.10+** from https://www.python.org/downloads/. In the installer, tick **"Add python.exe to PATH"**.

### 2. Get JARVIS

With Git:

```powershell
cd $HOME\Documents
git clone -b claude/laughing-bardeen-mjzwe3 https://github.com/texacoder/nova.git jarvis
```

(The GitHub project is still called `nova`; the last word, `jarvis`, names the folder on your PC.)

Or download the branch as a ZIP from GitHub and extract it.

### 3. Start JARVIS

Open the `jarvis` folder and double-click **`start_jarvis.bat`**, or run `py main.py`.

Your browser opens JARVIS at http://127.0.0.1:8765. Keep the black console window open; closing it stops JARVIS.

### 4. First run: let JARVIS set up its brain

On first start JARVIS shows **"Brain offline"** with a **Set up JARVIS's brain** button. Click it and JARVIS:

1. installs **Ollama**, the free engine that runs AI models (using `winget`, or the official installer if winget isn't available),
2. picks the best model for your PC's memory:

   | Your RAM | Model it downloads | Download size |
   |---|---|---|
   | 16 GB or more | `qwen2.5:7b` | ~4.7 GB |
   | 8–15 GB | `qwen2.5:3b` | ~1.9 GB |
   | less than 8 GB | `qwen2.5:1.5b` | ~1 GB |

3. downloads it with a progress bar and saves the choice in `.env`.

This happens once. After that JARVIS starts with its brain ready. If Ollama is already installed with a suitable model, JARVIS just uses it.

In terminal mode (`py main.py --cli`) JARVIS asks `Set it up now? [Y/n]` instead. You can rerun setup any time with `/setup`.

**Prefer to do it by hand?** Install Ollama from https://ollama.com/download, run `ollama pull qwen2.5:7b`, and set `OLLAMA_MODEL=qwen2.5:7b` in `.env`.

Other ways to start JARVIS:

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
From now on, always add comments to code you write      ← JARVIS learns this as a lesson
Make yourself a skill that converts CSV files to JSON    ← JARVIS writes new code for itself
Improve your own help text so it's shorter               ← JARVIS edits its own source code
```

When JARVIS wants to do something risky, an **Authorisation required** box appears showing exactly what it will do, such as the full command or the full email. Nothing happens until you click **Approve**.

## Changing the assistant's name

Ask it: **"From now on your name is FRIDAY."** It asks for approval, then the new name is used everywhere right away: the title, the browser tab, the chat, and how it introduces itself. It's saved as `JARVIS_NAME` in `.env`, which you can also edit by hand.

**Upgrading from NOVA:** on first start, old `NOVA_…` settings, `data/nova.db` and the `NOVA_Workspace` folder are upgraded to their JARVIS names automatically. Nothing is lost, and the console window lists what was changed.

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
| `/knowledge` | List what JARVIS has learned |
| `/lessons` | List lessons JARVIS learned about how you want it to work |
| `/setup` | Install or repair JARVIS's brain automatically |
| `/skills` | List abilities JARVIS wrote for itself |
| `/rollback` | Undo JARVIS's most recent change to its own code |
| `/restart` | Restart JARVIS (activates changes to its own code; the browser tab reconnects by itself) |
| `/tools` | List JARVIS's abilities and which ones ask first |
| `/new` | Start a fresh conversation (memories are kept) |
| `/status` | Brain, memory, email, and workspace status |
| `/exit` | Quit JARVIS |

## JARVIS's abilities (tools)

| Tool | What it does | Asks you first? |
|---|---|---|
| `web_search` | Search DuckDuckGo (falls back to Wikipedia) | no |
| `fetch_webpage` | Read a web page's text | no |
| `learn_topic` | Search, read 3 pages, summarize, and save to the knowledge base | no |
| `save_knowledge`, `remember`, `recall` | Manage long-term memory and knowledge | no |
| `forget` | Delete a memory, lesson or knowledge entry, or all memories | **always** (shows what will be forgotten) |
| `learn_lesson` | Save a lesson about how to work for you (after corrections or preferences) | no |
| `create_skill` | Write a new Python tool for itself and start using it | **always** (you see the code) |
| `remove_skill`, `list_skills` | Manage its self-written skills | remove: **always** |
| `read_jarvis_source` | Read its own source code | no |
| `modify_jarvis_source` | Change its own code or personality (tested and backed up) | **always** (you see a diff) |
| `get_datetime`, `system_info` | Date/time, OS, CPU, disk space | no |
| `find_files` | Search your folders for a file by (part of its) name | no (private folders like AppData are never searched) |
| `list_directory`, `read_file` | Browse and read files | only for private places (AppData, .ssh, key files, JARVIS's `.env`) or outside your user folder |
| `write_file` | Create or edit text/code files | only outside the workspace |
| `open_application` | Open apps (Geany, Notepad, Chrome...), optionally with a file | only for apps not in the trusted list or `apps.json` |
| `open_path` | Open a file, folder, or URL with its default program | for programs/scripts, private files, and files outside your user folder |
| `delete_file` | Delete a file or folder by moving it to the **Recycle Bin** (restorable). Refuses drives, Windows/program folders, your main folders and JARVIS's own files | **always** (shows the exact path) |
| `run_command` | Run a PowerShell command (Command Prompt-style commands like `dir /s` run in cmd). Failed commands show ✗; delete commands are redirected to `delete_file` | **always** |
| `send_email` | Send an email | **always** |
| `read_emails` | Read recent inbox emails (read-only) | no |

**The workspace** is JARVIS's own folder, `~/JARVIS_Workspace` (for example `C:\Users\you\JARVIS_Workspace`). JARVIS can create and read files there freely, so code it writes for you goes there.

**Your own folders** (Documents, Desktop, Downloads, Pictures, Music, Videos) can be searched, listed, read and opened without approval, since only you use JARVIS. Example: *"check my Documents folder for a file named budget"* → *"I found Budget 2025.xlsx in your Documents folder. Want me to open it?"* Writing or deleting outside the workspace always asks.

**Fewer tools at a time:** for each message JARVIS offers the model its everyday tools plus only the groups your message is about (email, skills, self-editing, renaming, learning, system info). Small models choose the right tool far more reliably this way.

**Honesty guard:** if an action was denied or failed, JARVIS adds *"⚠ Not done: …"* to its reply, so it can never claim something happened when it didn't. If *every* action failed but the reply still claims success ("I found…", "Done"), the made-up reply is replaced with what actually went wrong.

**Memory questions** like *"show your saved memories"* or *"what do you remember about me?"* are answered straight from JARVIS's memory, without the AI model, so the answer is always accurate.

To skip approval for a tool you trust, list it in `.env`, e.g. `JARVIS_AUTO_APPROVE=open_path`. Think twice before auto-approving `run_command` or `send_email`.

### Apps (Geany, VS Code, ...)

JARVIS finds apps on your PATH and in common install folders (Geany, VS Code, Chrome, Firefox, Edge, Notepad++). If it says it can't find an app, copy `apps.example.json` to `apps.json` and add the app's full path:

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
4. Restart JARVIS. `/status` should show your address.

For Outlook or other providers, also set `SMTP_HOST`, `SMTP_PORT`, and `IMAP_HOST`. Your password stays in your local `.env`, which git ignores. JARVIS always shows you the full email before sending.

## Voice chat

Use **Microsoft Edge** (best voices) or Chrome.

| Button | What it does |
|---|---|
| **Voice chat** (sound-wave button, turns green) | Hands-free conversation: speak, JARVIS answers out loud, then listens again. Click it again (or press Esc) to stop. |
| **Speaker** | Read typed replies out loud too |
| **Microphone** | Dictate one message instead of typing |
| **The glowing core** | Click it while JARVIS is speaking to interrupt |

- JARVIS starts speaking while it's still writing, sentence by sentence. It skips code and links ("the code is shown on screen").
- In voice chat, JARVIS answers in a few short sentences, which is also faster.
- Pick the voice and speed in the **Voice** panel. Edge offers free natural-sounding voices (e.g. "Microsoft Ava Online (Natural)").
- **Privacy:** voices are produced by your browser. Speech *recognition* in Chrome and Edge is done by Google's or Microsoft's servers, so if you'd rather not send audio, type instead. Firefox has no speech recognition, so the voice buttons are hidden there.

## Speed

- **Streaming:** replies appear word by word as they're written.
- **The brain stays loaded:** JARVIS loads the model when it starts and asks Ollama to keep it in memory for `OLLAMA_KEEP_ALIVE` (default 30 minutes; `-1` = always).
- **Less repeated work:** the instructions JARVIS sends stay identical between messages, so Ollama can reuse its earlier work instead of re-reading them every time.

## How memory and learning work

JARVIS has four kinds of memory, all stored locally in `data/jarvis.db` (SQLite):

1. **Conversation:** the current chat, kept in RAM and cleared on exit or `/new`.
2. **Memories:** facts about you. They're saved when you use `/remember` or when JARVIS decides something is worth remembering.
3. **Knowledge:** what JARVIS learned from the internet with `/learn` or `learn_topic`, stored with the source URLs.
4. **Lessons:** how you want JARVIS to behave. When you correct it ("don't explain so much", "my Geany is on D:"), JARVIS saves a lesson and follows it in every future conversation.

On each message, JARVIS adds your memories, all lessons, and the most relevant knowledge to what it sends the model.

You can also change JARVIS's core personality by editing `personality/jarvis.txt`. Changes apply on the next message, with no restart needed.

**Honest note on "learning":** the AI model itself isn't retrained; that would need expensive hardware. JARVIS learns the way a person keeps notes: it researches, writes a summary, and looks the summary up later. This is free, and you can inspect it (`/knowledge`) and correct it (`/forget K<id>`).

## How JARVIS improves itself

**New skills.** When you ask for something none of JARVIS's tools can do, JARVIS can write a new tool in Python (`create_skill`). You see the full code and approve it. JARVIS then checks it in a separate process (syntax, structure, that it loads), saves it to `skills/`, and uses it immediately. If the skill has a bug, JARVIS sees the error and can write a fixed version. Skills load again every time JARVIS starts. They can't replace built-in tools. Delete a file in `skills/` to remove a skill.

**Editing its own code.** JARVIS can read its source (`read_jarvis_source`) and change it (`modify_jarvis_source`). For every change:

1. you see a diff of exactly what changes and approve it,
2. the old version is backed up to `data/backups/`,
3. JARVIS's full test suite runs, and **if any test fails, the change is rolled back automatically**,
4. `/rollback` undoes the most recent change, and `/restart` activates code changes.

Some files are **locked** so JARVIS can't weaken its own safeguards: `tests/`, `tools/base.py` (approval rules), `tools/self_modify.py`, `tools/skills.py`, and `ui/server.py` (web security). You can still edit them yourself.

**Lessons and personality.** Corrections become lessons (see *How memory and learning work*), and `personality/jarvis.txt` changes apply from the next message.

## Safety

- JARVIS only listens on `127.0.0.1`, so other computers can't reach it.
- The web page uses a secret token that changes on every start, so other websites open in your browser can't send commands to JARVIS.
- Risky actions need your click (see the tools table). The approval box shows exactly what will run, including the full code of new skills and a diff for self-edits. **Only approve code you're comfortable running on your PC**; a web page or email could try to trick the model into writing harmful code.
- The model is told to treat web pages, files, and emails as data, not instructions. A malicious page could still try to trick it, which is exactly why approvals exist. **Read approval requests before clicking Approve.**
- JARVIS never needs administrator rights. Don't run it as administrator.
- Everything JARVIS does is logged in `data/logs/jarvis.log`.

## Configuration

All settings live in `.env`; see `.env.example` for descriptions. The most useful ones:

| Setting | Default | Meaning |
|---|---|---|
| `OLLAMA_MODEL` | *(set by automatic setup)* | Model to use, e.g. `qwen2.5:7b` |
| `OLLAMA_NUM_CTX` | `8192` | Context size. Lower it (4096) if replies are slow |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long the model stays in memory after a message (`-1` = always) |
| `OLLAMA_TEMPERATURE` | `0.3` | 0 = focused and predictable, 1 = more creative. Low values make tool use more reliable |
| `OLLAMA_TIMEOUT` | `300` | Seconds to wait for the model |
| `JARVIS_WORKSPACE` | `~/JARVIS_Workspace` | JARVIS's own folder |
| `JARVIS_AUTO_APPROVE` | *(empty)* | Tools that skip approval |
| `JARVIS_WEB_PORT` | `8765` | Web interface port |
| `JARVIS_MAX_TOOL_STEPS` | `8` | Max actions chained per request |

## Troubleshooting

| Problem | Fix |
|---|---|
| "Brain offline" / "Cannot reach Ollama" | Click **Set up JARVIS's brain** or type `/setup`. JARVIS starts Ollama itself if it's installed |
| Automatic setup fails | Install Ollama from https://ollama.com/download, then click **Try again** |
| "Model ... is not installed" | Type `/setup`, or run `ollama pull <model>` using the exact name in `OLLAMA_MODEL` |
| JARVIS chats but never takes actions | Your model doesn't support tools. Use `qwen2.5:7b` or `llama3.1:8b` (`/status` warns about this) |
| Very slow replies | Use a smaller model (`qwen2.5:3b`) or set `OLLAMA_NUM_CTX=4096` |
| "Could not find an app" | Add it to `apps.json` (see above) |
| Web search fails | Check your internet connection. DuckDuckGo sometimes blocks automated searches for a while; JARVIS then falls back to Wikipedia |
| Port 8765 in use | Set `JARVIS_WEB_PORT=8770` in `.env` |
| Anything else | Check `data\logs\jarvis.log` |

## Project structure

```
jarvis/
├── main.py              # Start here: sets everything up, runs the web UI or --cli
├── start_jarvis.bat       # Double-click launcher for Windows
├── agent.py             # The agent loop: context, tool calls, approvals, commands
├── config.py            # Settings from .env
├── brain/               # The AI model layer (swappable)
│   ├── base.py          #   Brain interface: chat(messages, tools) + health_check()
│   ├── local.py         #   Ollama adapter (native tool calling)
│   └── setup.py         #   Automatic setup: install Ollama, choose + download model
├── tools/               # JARVIS's abilities
│   ├── base.py          #   Tool class, registry, approval rules
│   ├── web.py           #   web_search, fetch_webpage
│   ├── knowledge.py     #   remember, recall, learn_topic, save_knowledge
│   ├── files.py         #   list_directory, read_file, write_file
│   ├── apps.py          #   open_application, open_path
│   ├── system.py        #   get_datetime, system_info, run_command
│   ├── email_tools.py   #   send_email, read_emails
│   ├── skills.py        #   create_skill, remove_skill, list_skills (+ skill loader)
│   └── self_modify.py   #   read_jarvis_source, modify_jarvis_source, rollback
├── memory/              # SQLite memories + knowledge, conversation memory
├── skills/              # Abilities JARVIS wrote for itself (loaded at startup)
├── personality/jarvis.txt # JARVIS's personality and rules: edit freely
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

- Fully offline voice (Whisper for speech-to-text, Piper for text-to-speech) and a wake word
- Smarter memory search with local embeddings (Ollama `nomic-embed-text`)
- Reminders, scheduled tasks, and calendar (CalDAV or Google Calendar)
- Background self-learning on topics you choose
- Seeing your screen (screenshots with a local vision model) and mouse/keyboard control
- Streaming replies word by word
