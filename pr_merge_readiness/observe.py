"""run 全体の履歴予算を共有して観測し、失敗時も今回の証跡を保存する。"""

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import MANIFEST_FORMAT, write_json
from .collect import ChangeHistoryCollector, CollectionError, GitHub, collect, targets
from .contracts import Manifest, Policy, Provenance
from .evaluate import assess
from .report import markdown


def observe(
    api: GitHub,
    policy: Policy,
    event: dict[str, Any],
    number: int | None,
    directory: Path,
    source: Provenance,
    run_id: str,
    attempt: str,
    name: str,
    *,
    expected_head: str | None = None,
) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    manifest: Manifest = {
        "format": MANIFEST_FORMAT,
        "schema_version": 1,
        "repository": api.repository,
        "run_id": run_id,
        "run_attempt": attempt,
        "artifact_name": name,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "reports": [],
        "collection_failed": False,
        "provenance": source,
        "selection": "single_pr" if number is not None or "pull_request" in event else "all_open",
    }
    summaries = []
    failed = False
    try:
        collector = ChangeHistoryCollector(api)
        for target in targets(api, event, number):
            facts = collect(api, target, history_collector=collector, expected_head=expected_head)
            result = assess(facts, policy)
            result["provenance"] = source
            filename = f"pr-{target}.json"
            write_json(directory / filename, result)
            manifest["reports"].append(filename)
            summaries.append(markdown(result))
            failed |= result["decision"] == "INSUFFICIENT_DATA"
            print(json.dumps({"pr": target, "decision": result["decision"], "report": filename}))
        if not manifest["reports"]:
            summaries.append("評価対象の open PR はありません。\n")
    except (CollectionError, OSError, KeyError, TypeError, ValueError) as error:
        failed = True
        manifest["collection_failed"] = True
        write_json(
            directory / "collection-error.json",
            {"decision": "INSUFFICIENT_DATA", "error": str(error)},
        )
        summaries.append("INSUFFICIENT_DATA: <code>" + html.escape(str(error)) + "</code>\n")
    write_json(directory / "manifest.json", manifest)
    (directory / "summary.md").write_text("\n".join(summaries))
    return int(failed)
