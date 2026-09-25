"""
NOVA - start here.

    python main.py              Start NOVA with the web interface (opens your browser)
    python main.py --cli        Start NOVA in the terminal instead
    python main.py --no-browser Start the web interface without opening a browser

This file only sets things up and handles input/output. The thinking,
tools and memory logic live in agent.py and the packages it uses.
"""

import argparse
import sys
import threading
import webbrowser

from agent import Agent
from brain import create_brain
from config import ConfigError, load_config
from memory.database import MemoryStore, MemoryStoreError
from personality import load_personality
from utils.logger import get_logger, setup_logging

LINE = "=" * 44


def build_agent():
    """Load config, logging, memory, personality and brain. Returns (config, agent) or exits."""
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
    return config, Agent(config, brain, memory_store, personality)


def print_banner(config, agent) -> None:
    print(LINE)
    print(f"{config.name} v{config.version}")
    print("Personal AI Assistant")
    print(LINE)
    health = agent.brain.health_check()
    if not health.ok:
        print(f"\nNote: the brain is not ready, so chatting won't work yet.\n{health.message}")
        print("Commands like /remember and /learn still work.")
    else:
        print(f"\n{health.message}")


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
    print("\nType /help for commands, /exit to quit.\n")

    while not agent.should_exit:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

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
