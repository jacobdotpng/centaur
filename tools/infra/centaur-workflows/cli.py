"""Discover and verify durable workflows without confusing them with scheduled tasks."""
import argparse
import json
from pathlib import Path
import sys
import uuid
from .client import WorkflowClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List discovered definitions and enabled status, including workflows without runs")
    runs = commands.add_parser("runs", help="List durable run history, not the definition registry")
    runs.add_argument("--workflow")
    for name in ["run", "wait"]:
        sub = commands.add_parser(name)
        sub.add_argument("run_id")
        if name == "wait": sub.add_argument("--timeout", type=int, default=300)
    start = commands.add_parser("start", help="Start a real durable run; admission is not completion")
    start.add_argument("workflow")
    group = start.add_mutually_exclusive_group()
    group.add_argument("--input", default="{}", help="Input JSON (never credentials)")
    group.add_argument("--input-file", type=Path)
    start.add_argument("--idempotency-key")
    args = parser.parse_args()
    client = WorkflowClient()
    try:
        if args.command == "list": result = client.catalog()
        elif args.command == "runs": result = client.runs(args.workflow)
        elif args.command == "run": result = client.run(args.run_id)
        elif args.command == "wait":
            if not 1 <= args.timeout <= 1800: parser.error("timeout must be between 1 and 1800 seconds")
            result = client.wait(args.run_id, args.timeout)
        else:
            inputs = json.loads(args.input_file.read_text() if args.input_file else args.input)
            if not isinstance(inputs, dict): parser.error("workflow input must be a JSON object")
            result = client.start(args.workflow, inputs, args.idempotency_key or "cli-" + uuid.uuid4().hex)
        print(json.dumps(result, indent=2, default=str))
        if args.command == "wait" and result["status"] != "completed": return 1
        return 0
    except (RuntimeError, TimeoutError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


def app():
    raise SystemExit(main())


if __name__ == "__main__":
    app()
