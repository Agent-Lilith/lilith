"""Entry point: cli | telegram | google-auth | oneshot."""

import asyncio
import sys


def main():
    mode = "cli"
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()

    if mode == "cli":
        from src.interfaces.cli import run_cli

        initial_external = "--external" in sys.argv
        asyncio.run(run_cli(initial_external=initial_external))

    elif mode == "google-auth":
        from src.services.google_auth import run_google_auth

        sys.exit(run_google_auth())

    elif mode == "telegram":
        from src.interfaces.telegram import run_telegram

        run_telegram()

    elif mode == "oneshot":
        from src.interfaces.oneshot import main as run_oneshot_main

        use_external = "--external" in sys.argv[2:]
        query_parts = [arg for arg in sys.argv[2:] if arg != "--external"]
        if query_parts:
            query = " ".join(query_parts).strip()
        else:
            query = sys.stdin.read().strip()
        sys.exit(run_oneshot_main(query=query, use_external=use_external))

    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python -m src.main [cli|telegram|google-auth|oneshot]")
        sys.exit(1)


if __name__ == "__main__":
    main()
