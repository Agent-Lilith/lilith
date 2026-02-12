"""Entry point: cli | google-auth | telegram."""

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

    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python -m src.main [cli|telegram|google-auth]")
        sys.exit(1)


if __name__ == "__main__":
    main()
