"""Inspect production via the Centaur diagnostics service."""
import argparse
import json
import sys

from .client import DiagnosticsClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("deployment")
    commands.add_parser("health")
    pipelines = commands.add_parser("pipelines")
    pipelines.add_argument("repo", choices=("source", "config", "product"))
    pipelines.add_argument("--run-id", type=int)
    release = commands.add_parser("release")
    release.add_argument("source")
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--extended", action="store_true")
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        print(json.dumps(getattr(DiagnosticsClient(), command)(**args), indent=2))
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def app():
    raise SystemExit(main())
