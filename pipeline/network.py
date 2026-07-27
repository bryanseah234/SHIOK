"""Network build entry point for run.py."""

from __future__ import annotations

import argparse

from scripts.run_network_build import run_build


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the pedestrian network.")
    parser.add_argument(
        "--area",
        default="pilot",
        choices=["pilot"],
        help="Network scope to build. Island-wide support is not implemented yet.",
    )
    parser.parse_args()

    run_build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
