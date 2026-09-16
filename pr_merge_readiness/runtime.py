"""設定の検証・観測・ラベル更新を同じ実行内で完結させる。"""

import base64
import html
import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import publish
from .collect import GitHub
from .config import (
    ACTION_REPOSITORY,
    CONFIG_PATH,
    policy_from,
    positive,
    relative_path,
    validate_config,
)
from .contracts import Config, EvaluationError, sha
from .observe import observe
from .report import markdown


def output(values: Mapping[str, object]) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with Path(destination).open("a") as stream:
            for key, value in values.items():
                value = str(value)
                if "\n" in value or "\r" in value:
                    raise EvaluationError("invalid output value")
                stream.write(f"{key}={value}\n")


def summary(text: str) -> None:
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a") as stream:
            stream.write(text + "\n")


def report_error(error: Exception) -> None:
    print(json.dumps({"error": str(error)}))
    try:
        summary("処理失敗: <code>" + html.escape(str(error)) + "</code>")
    except OSError as summary_error:
        print(json.dumps({"summary_error": str(summary_error)}))


def verify_source() -> None:
    context_ref = os.environ.get("PMR_SOURCE_REF", "")
    if not context_ref:
        raise EvaluationError(
            "local Actions are unsupported; use the remote Action pinned to a full SHA"
        )
    sha(context_ref)
    if os.environ.get("PMR_SOURCE_REPOSITORY") != ACTION_REPOSITORY:
        raise EvaluationError("Action source repository mismatch")


def trusted_config(api: GitHub, path: str, config_sha: str = "") -> tuple[Config, str]:
    relative_path(path)
    if not config_sha:
        branch = api.request(api.prefix)["default_branch"]
        config_sha = api.request(f"{api.prefix}/git/ref/heads/{quote(branch, safe='')}")["object"][
            "sha"
        ]
    sha(config_sha)
    blob = api.request(f"{api.prefix}/contents/{quote(path, safe='/')}?ref={config_sha}")
    if blob.get("type") != "file" or blob.get("encoding") != "base64":
        raise EvaluationError("config must be a regular TOML file")
    config = validate_config(tomllib.loads(base64.b64decode(blob["content"]).decode("utf-8")))
    return config, config_sha


def pr_event_operation(event: dict[str, Any]) -> str:
    if not publish.is_publication_target(event["pull_request"]):
        return "skip"
    action = event.get("action")
    if action == "edited" and "base" not in event.get("changes", {}):
        return "skip"
    if action in {
        "opened",
        "reopened",
        "synchronize",
        "edited",
        "ready_for_review",
        "converted_to_draft",
        "closed",
    }:
        return "observe"
    return "skip"


def manual_pr_number(event: dict[str, Any]) -> int | None:
    inputs = event.get("inputs")
    if inputs is None:
        return None
    if not isinstance(inputs, dict) or inputs.keys() - {"pr-number"}:
        raise EvaluationError("manual inputs only support pr-number; labels are always updated")
    number = inputs.get("pr-number", "")
    return None if number == "" else positive(number)


def run_action() -> int:
    verify_source()
    event_name = os.environ["GITHUB_EVENT_NAME"]
    if event_name not in {"pull_request", "workflow_dispatch", "push"}:
        output({"operation": "skip"})
        return 0
    event = (
        {}
        if event_name == "push"
        else json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    )
    if event_name == "pull_request" and pr_event_operation(event) == "skip":
        output({"operation": "skip"})
        return 0
    number = manual_pr_number(event) if event_name == "workflow_dispatch" else None
    expected_head = (
        sha(event["pull_request"]["head"]["sha"]) if event_name == "pull_request" else None
    )
    api = GitHub(os.environ["GITHUB_REPOSITORY"])
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        raise EvaluationError("token required")
    path = os.environ.get("PMR_CONFIG_PATH") or CONFIG_PATH
    if event_name == "push":
        _, config_sha = trusted_config(api, path, sha(os.environ["GITHUB_SHA"]))
        output({"operation": "validate-config", "config-sha": config_sha})
        print(json.dumps({"valid": True, "config_sha": config_sha}))
        return 0
    if expected_head is not None:
        # 提案は検証だけに使う。判定にはdefault branchで一度確定した設定を使う。
        trusted_config(api, path, expected_head)
    config, config_sha = trusted_config(api, path)
    output({"operation": "observe", "config-sha": config_sha})
    summary("設定コミット: <code>" + config_sha + "</code>")
    reports = observe(api, policy_from(config), event, number, expected_head=expected_head)
    # 全対象の観測が終わってから公開する。別step・保存済みJSONからの再読込は不要。
    summary(
        "\n".join(markdown(report) for _, report in reports) or "評価対象の open PR はありません。"
    )
    writer = publish.GitHub(api.repository)
    failed = any(report["decision"] == "INSUFFICIENT_DATA" for _, report in reports)
    if reports:
        publish.ensure_labels(writer)
    for target, report in reports:
        try:
            result = publish.publish_pr(writer, target, report, expected_head=expected_head)
            print(json.dumps({"pr": target, "publication": result}, ensure_ascii=False))
        except (publish.PublishError, KeyError, TypeError, ValueError) as error:
            failed = True
            report_error(EvaluationError(f"PR #{target}: {error}"))
    if event_name == "workflow_dispatch" and number is None:
        publish.cleanup_closed(writer)
    return int(failed)
