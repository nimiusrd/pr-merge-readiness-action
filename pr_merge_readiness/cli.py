"""Actionの実行とローカル設定検証。"""

import argparse
import json
from pathlib import Path

from .config import load_config
from .runtime import report_error, run_action


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("action")
    validation = commands.add_parser("validate-config")
    validation.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "action":
            return run_action()
        load_config(args.config)
        print(json.dumps({"valid": True}))
        return 0
    except (OSError, KeyError, TypeError, ValueError) as error:
        if args.command == "action":
            report_error(error)
        else:
            print(json.dumps({"error": str(error)}))
        return 1
