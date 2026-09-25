"""
NOVA - start here.

    python main.py              Start NOVA with the web interface (opens your browser)
    python main.py --cli        Start NOVA in the terminal instead
    python main.py --no-browser Start the web interface without opening a browser

This file only sets things up and handles input/output. The thinking,
tools and memory logic live in agent.py and the packages it uses.
"""

import argparse
import shutil
import sys
import threading
import time
import webbrowser

from agent import Agent
from brain import create_brain
from config import PROJECT_ROOT, ConfigError, load_config
from memory.database import MemoryStore, MemoryStoreError
from personality import load_personality
from utils.logger import get_logger, setup_logging

LINE = "=" * 44


def build_agent():
    """Load config, logging, memory, personality and brain. Returns (config, agent) or exits."""
    env_file, example = PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.example"
    if not env_file.exists() and example.exists():
        shutil.copy(example, env_file)  # first run: create settings from the example
    try:
        config = load_config()
    except ConfigError as error:
        print(f"Configuration problem: {error}")
        print("Check your .env file (see .env.example).")
        sys.exit(1)

    try:
        setup_logging(config.log_file, config.log_level)
    except OSError as error:
        print(f"Could not create the log file at {config.log_file}: {error}")
        sys.exit(1)
    get_logger().info("Starting %s v%s (brain=%s)", config.name, config.version, config.brain)

    try:
        memory_store = MemoryStore(config.db_path)
    except MemoryStoreError as error:
        print(error)
        sys.exit(1)

    personality = load_personality(config.personality_file, config.name, config.version)
    brain = create_brain(config)
    agent = Agent(config, brain, memory_store, personality)
    if agent.setup:
        # Start an installed-but-stopped Ollama and pick an installed model, silently.
        agent.setup.quick_start()
    return config, agent


def print_banner(config, agent) -> None:
    print(LINE)
    print(f"{config.name} v{config.version}")
    print("Personal AI Assistant")
    print(LINE)
    health = agent.brain.health_check()
    if health.ok:
        print(f"\nBrain online: {health.message}")
    else:
        print("\nNOVA's brain (the local AI model) is not installed or not running yet.")


def run_setup_in_terminal(agent) -> None:
    """Run the automatic brain setup, showing progress in the terminal."""
    agent.setup.start()
    last = ""
    while True:
        status = agent.setup.snapshot()
        progress = status.get("progress")
        bar = ""
        if isinstance(progress, (int, float)):
            filled = int(progress * 30)
            bar = f" [{'#' * filled}{'.' * (30 - filled)}] {progress * 100:5.1f}%"
        line = f"{status['message'][:70]}{bar}"
        if line != last:
            print("\r" + line.ljust(110), end="", flush=True)
            last = line
        if status["state"] in ("done", "error"):
            print()
            if status["state"] == "error":
                print(f"Setup problem: {status['message']}")
            return
        time.sleep(0.5)


def offer_setup(config, agent) -> None:
    if not agent.setup or agent.brain.health_check().ok:
        return
    print(f"{config.name} can set up its brain automatically: install the free Ollama engine if needed")
    print("and download the best AI model for this PC (one-time download of a few GB).")
    try:
        answer = input("Set it up now? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    if answer in ("", "y", "yes"):
        run_setup_in_terminal(agent)
    else:
        print("OK. Type /setup any time.")


# --- terminal mode ------------------------------------------------------------

def print_steps(steps: list[dict]) -> None:
    for step in steps:
        mark = {"ok": "done", "error": "FAILED", "declined": "declined"}[step["status"]]
        print(f"  [{step['tool']}: {mark}]")


def ask_approval(pending) -> bool:
    print("\n" + "-" * 44)
    print("APPROVAL NEEDED:")
    print(pending.description)
    print("-" * 44)
    try:
        answer = input("Allow this? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def run_cli(config, agent) -> None:
    log = get_logger()
    print_banner(config, agent)
    offer_setup(config, agent)
    print("\nType /help for commands, /exit to quit.\n")

    while not agent.should_exit:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if user_input.strip().lower() == "/setup" and agent.setup:
            run_setup_in_terminal(agent)
            continue
        try:
            reply = agent.handle(user_input)
            shown = 0  # each reply repeats this turn's earlier steps; print only new ones
            while reply.pending:
                print_steps(reply.steps[shown:])
                shown = len(reply.steps)
                reply = agent.resolve_pending(ask_approval(reply.pending))
            print_steps(reply.steps[shown:])
        except KeyboardInterrupt:
            print("\n(Interrupted.)")
            continue
        except Exception:  # last line of defence: never show a traceback
            log.exception("Unexpected error while handling input: %r", user_input)
            print(f"\n{config.name}:\nSomething unexpected went wrong. Details were written to {config.log_file}.\n")
            continue
        if reply.text:
            print(f"\n{config.name}:\n{reply.text}\n")


# --- web mode -----------------------------------------------------------------

def run_web(config, agent, open_browser: bool) -> None:
    from ui.server import NovaWebServer  # imported here so --cli never needs it

    try:
        server = NovaWebServer(agent, config)
    except OSError as error:
        print(f"Could not start the web interface on port {config.web_port}: {error}")
        print("Another program may be using that port. Set NOVA_WEB_PORT in .env or use: python main.py --cli")
        sys.exit(1)

    print_banner(config, agent)
    print(f"\nWeb interface: {server.url}")
    print("Keep this window open while you use NOVA. Press Ctrl+C here to stop.\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(server.url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.httpd.server_close()
    print("NOVA stopped.")


def main() -> int:
    parser = argparse.ArgumentParser(description="NOVA - your local personal AI assistant")
    parser.add_argument("--cli", action="store_true", help="use the terminal instead of the web interface")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    args = parser.parse_args()

    config, agent = build_agent()
    if args.cli:
        run_cli(config, agent)
    else:
        run_web(config, agent, open_browser=not args.no_browser)
    get_logger().info("%s stopped", config.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
