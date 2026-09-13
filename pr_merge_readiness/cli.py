"""Action、workflow 準備、設定生成、再評価のコマンド。"""

import argparse
import json
import os
import subprocess
import tarfile
from pathlib import Path

from .config import load_config
from .generate import generate_workflow
from .replay import replay
from .runtime import prepare, run_action, save_failure


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("action")
    generate = commands.add_parser("generate-workflow")
    generate.add_argument("--config", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--check", action="store_true")
    preparing = commands.add_parser("prepare")
    preparing.add_argument("--config-path", required=True)
    preparing.add_argument("--action-ref", required=True)
    preparing.add_argument("--pr-number", default="")
    preparing.add_argument("--update-labels", choices=("true", "false"), default="false")
    replaying = commands.add_parser("replay")
    replaying.add_argument("--report", type=Path, required=True)
    replaying.add_argument("--source-dir", type=Path, required=True)
    replaying.add_argument("--source-repository", required=True)
    replaying.add_argument("--action-sha", required=True)
    args = parser.parse_args()
    try:
        if args.command == "action":
            return run_action()
        if args.command == "prepare":
            return prepare(
                args.config_path, args.action_ref, args.pr_number, args.update_labels == "true"
            )
        if args.command == "generate-workflow":
            return generate_workflow(load_config(args.config), args.config, args.output, args.check)
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
        if args.command == "prepare":
            save_failure(Path("preparation-report"), error)
        elif args.command == "action" and os.environ.get("PMR_OPERATION") == "observe":
            save_failure(Path(os.environ.get("PMR_REPORT_DIR") or "readiness-report"), error)
        return 1
