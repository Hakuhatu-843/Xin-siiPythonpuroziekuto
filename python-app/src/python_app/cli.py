"""Command-line interface for the Python App starter."""

from __future__ import annotations

import argparse
import json
from typing import Sequence


def build_greeting(name: str) -> str:
    """Create a friendly greeting for the supplied name."""
    normalized_name = name.strip() or "world"
    return f"Hello, {normalized_name}!"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A small Python project starter.",
    )
    parser.add_argument(
        "--name",
        default="world",
        help="The name to greet.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the greeting as a JSON object.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command-line application."""
    args = build_parser().parse_args(argv)
    greeting = build_greeting(args.name)

    if args.as_json:
        print(json.dumps({"greeting": greeting}, ensure_ascii=False))
        return

    print(greeting)


if __name__ == "__main__":
    main()
