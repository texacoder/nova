"""
NOVA - start here.

Run with:   python main.py

This file is the CLI (terminal interface). It sets everything up, then
reads your input in a loop and prints NOVA's replies. All the thinking
and memory logic lives in agent.py.
"""

import sys

from agent import Agent
from brain import create_brain
from config import ConfigError, load_config
from memory.database import MemoryStore, MemoryStoreError
from personality import load_personality
from utils.logger import get_logger, setup_logging

LINE = "=" * 40


def print_nova(name: str, text: str) -> None:
    if text:
        print(f"\n{name}:\n{text}\n")


def main() -> int:
    # 1. Configuration. Logging is not set up yet, so errors go to the screen.
    try:
        config = load_config()
    except ConfigError as error:
        print(f"Configuration problem: {error}")
        print("Check your .env file (see .env.example).")
        return 1

    # 2. Logging (technical details go to the log file only).
    try:
        setup_logging(config.log_file, config.log_level)
    except OSError as error:
        print(f"Could not create the log file at {config.log_file}: {error}")
        return 1
    log = get_logger()
    log.info("Starting %s v%s (brain=%s)", config.name, config.version, config.brain)

    # 3. Memory, personality and brain.
    try:
        memory_store = MemoryStore(config.db_path)
    except MemoryStoreError as error:
        print(error)
        return 1
    personality = load_personality(config.personality_file, config.name, config.version)
    brain = create_brain(config)
    agent = Agent(config, brain, memory_store, personality)

    # 4. Welcome banner.
    print(LINE)
    print(f"{config.name} v{config.version}")
    print("Personal AI Assistant")
    print(LINE)
    health = brain.health_check()
    if not health.ok:
        print(f"\nNote: the brain is not ready, so chatting won't work yet.\n{health.message}")
        print("Memory commands still work.")
    elif config.brain == "mock":
        print(f"\nNote: {health.message}")
    print("\nType /help for commands, /exit to quit.\n")

    # 5. Main loop.
    while not agent.should_exit:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        try:
            reply = agent.handle(user_input)
        except KeyboardInterrupt:
            print("\n(Interrupted.)")
            continue
        except Exception:  # last line of defence: never show a traceback
            log.exception("Unexpected error while handling input: %r", user_input)
            reply = f"Something unexpected went wrong. Details were written to {config.log_file}."
        print_nova(config.name, reply)

    log.info("%s stopped", config.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
