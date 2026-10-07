#!/usr/bin/env python3
"""
Backward-compatible CLI entry point for the STM32 firmware agent - now with
the agent's two modes exposed directly:

  Mode 1 - documentation chat:
      python stm32_agent.py chat
      (interactive REPL: ask questions, answered from the RAG store)

  Mode 2 - firmware generation & flashing:
      python stm32_agent.py "Turn on the red diode continuously"
      python stm32_agent.py "blink via DMA" --yes        # skip flash confirm

Also re-exports the old top-level names so existing imports keep working.
"""

import sys

# ---------------------------------------------------------------- re-exports
# Old name -> new home (kept importable for backwards compatibility).
from agent.llm import create_client as init_ollama_client  # noqa: F401
from agent.orchestrator import (  # noqa: F401
    AgentOrchestrator,
    orchestration_pipeline,
)
from agent.validation import validate_c_source  # noqa: F401
from agent.builder import (  # noqa: F401
    archive_and_remove_source,
    write_source_files as write_code_to_platformio,
)
from rag.retriever import Retriever as Stm32RagStore  # noqa: F401


def run_chat() -> int:
    """Interactive documentation chat (mode 1)."""
    from agent.chat import ChatEngine

    try:
        engine = ChatEngine()
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    print("STM32 documentation chat (mode 1) - ask anything, 'quit' to exit.")
    history: list[dict] = []
    while True:
        try:
            question = input("\nyou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question or question.lower() in {"quit", "exit"}:
            break
        try:
            result = engine.answer(question, history=history)
        except Exception as e:
            sys.stderr.write(f"error: {e}\n")
            continue
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": result.answer})
        print(f"\nassistant: {result.answer}")
        if result.sources:
            print(f"\n[sources: {', '.join(result.sources)}]")
    return 0


def main(argv: list[str]) -> int:
    args = argv[:]
    auto_confirm_flag = "--yes" in args or "-y" in args
    args = [a for a in args if a not in ("--yes", "-y")]

    port = None
    if "--port" in args:
        i = args.index("--port")
        if i + 1 >= len(args):
            sys.stderr.write("--port needs a value, e.g. --port COM3\n")
            return 1
        port = args[i + 1]
        args = args[i + 2:]

    # --check: compile-verify the generated firmware (no flashing, no board
    # needed). The default flow still asks before touching hardware.
    check_only = "--check" in args
    args = [a for a in args if a != "--check"]

    if args == ["chat"]:
        return run_chat()

    if not args:
        sys.stderr.write(
            "Two modes are available:\n"
            "\n"
            "Mode 1 - documentation chat (no code generation):\n"
            "  python stm32_agent.py chat\n"
            "\n"
            "Mode 2 - generate firmware (and optionally flash it):\n"
            '  python stm32_agent.py "<natural-language command>" [--yes] [--check]\n'
            '  Example: python stm32_agent.py "Turn on the red diode continuously"\n'
            "\n"
            "Options:\n"
            "  --check         compile-verify the generated code, never flash\n"
            "  --port COM3     explicit upload port (default: PlatformIO auto-detect)\n"
            "  --yes           skip the interactive flash confirmation\n"
        )
        return 1

    user_command = " ".join(args)
    try:
        if check_only:
            from agent.orchestrator import AgentOrchestrator

            result = AgentOrchestrator().generate_verified(user_command)
            print("\nCompile check:", result.build_status)
            if result.build_status == "error":
                print(result.build_logs[-2000:])
            else:
                print("Firmware builds cleanly - no flash attempted.")
            return 0 if result.build_status == "success" else 1

        orchestration_pipeline(
            user_command, auto_confirm=auto_confirm_flag, port=port
        )
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
