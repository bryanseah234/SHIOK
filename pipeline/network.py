"""Network build entry point for run.py."""

from __future__ import annotations

import argparse

from scripts.run_network_build import run_build


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the pedestrian network.")
    parser.add_argument(
        "--area",
        default="pilot",
        choices=["pilot", "island"],
        help="Network scope to build.",
    )
    args = parser.parse_args()

    run_build(args.area)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
