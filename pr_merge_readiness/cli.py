"""Action、ローカル設定検証、再評価のコマンド。"""

import argparse
import json
import os
import subprocess
import tarfile
from pathlib import Path

from .config import load_config
from .replay import replay
from .runtime import run_action, save_failure


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("action")
    validation = commands.add_parser("validate-config")
    validation.add_argument("--config", type=Path, required=True)
    replaying = commands.add_parser("replay")
    replaying.add_argument("--report", type=Path, required=True)
    replaying.add_argument("--source-dir", type=Path, required=True)
    replaying.add_argument("--source-repository", required=True)
    replaying.add_argument("--action-sha", required=True)
    args = parser.parse_args()
    try:
        if args.command == "action":
            return run_action()
        if args.command == "validate-config":
            load_config(args.config)
            print(json.dumps({"valid": True}))
            return 0
        report = json.loads(args.report.read_text())
        result = replay(report, args.source_dir, args.source_repository, args.action_sha)
        same = result == report
        print(
            json.dumps(
                {
                    "matches": same,
                    "decision": result["decision"],
                    "policy_sha256": result["policy_sha256"],
                }
            )
        )
        return int(not same)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
        tarfile.TarError,
    ) as error:
        print(json.dumps({"error": str(error)}))
        if args.command == "action" and os.environ.get("PMR_OPERATION") == "prepare":
            save_failure(Path("preparation-report"), error)
        elif args.command == "action" and os.environ.get("PMR_OPERATION") == "observe":
            save_failure(Path(os.environ.get("PMR_REPORT_DIR") or "readiness-report"), error)
        return 1
