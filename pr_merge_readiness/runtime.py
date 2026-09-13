"""Composite Action と共通 workflow の入力・信頼境界。"""

import base64
import html
import json
import os
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import publish, publish_checks
from .artifacts import MANIFEST_FORMAT, artifact_name, load_reports, provenance, write_json
from .collect import GitHub
from .config import (
    ACTION_REPOSITORY,
    WORKFLOW_PATH,
    policy_from,
    positive,
    relative_path,
    validate_config,
)
from .contracts import Config, EvaluationError, sha
from .observe import observe

ROOT = Path(__file__).resolve().parent.parent


def output(values: Mapping[str, object]) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with Path(destination).open("a") as stream:
            for key, value in values.items():
                value = str(value)
                if "\n" in value or "\r" in value:
                    raise EvaluationError("invalid output value")
                stream.write(f"{key}={value}\n")


def summary(path: Path) -> None:
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination and path.is_file():
        with Path(destination).open("a") as stream:
            stream.write(path.read_text())


def verify_source(expected: str, workflow_sha: str = "") -> None:
    sha(expected)
    context_ref = os.environ.get("PMR_SOURCE_REF", "")
    context_repository = os.environ.get("PMR_SOURCE_REPOSITORY", "")
    if context_ref:
        if context_ref != expected or context_repository != ACTION_REPOSITORY:
            raise EvaluationError("Action source reference mismatch; pin a full SHA")
    else:
        # ローカル Action の場合も、親にある利用側 checkout の SHA で代用しない。
        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True
            ).stdout.strip()

        if (
            Path(git("rev-parse", "--show-toplevel")).resolve() != ROOT
            or git("rev-parse", "HEAD") != expected
        ):
            raise EvaluationError("local Action checkout SHA mismatch")
    if workflow_sha:
        if (
            sha(workflow_sha) != expected
            or os.environ.get("PMR_WORKFLOW_REPOSITORY") != ACTION_REPOSITORY
            or os.environ.get("PMR_WORKFLOW_PATH") != WORKFLOW_PATH
        ):
            raise EvaluationError("reusable workflow identity mismatch")


def trusted_config(api: GitHub, path: str, config_sha: str, action_ref: str) -> tuple[Config, str]:
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
    if config["action_ref"] != action_ref:
        raise EvaluationError("config/action SHA mismatch")
    return config, config_sha


def route(
    event_name: str, event: dict[str, Any], config: Config, pr_number: str, update_labels: bool
) -> str:
    if pr_number:
        positive(pr_number)
    if event_name != "workflow_dispatch" and (pr_number or update_labels):
        raise EvaluationError("manual inputs require workflow_dispatch")
    if update_labels and (pr_number or config["publication"]["labels"] != "manual"):
        raise EvaluationError("labels require all open PRs and publication.labels=manual")
    if event_name == "workflow_dispatch":
        return "observe"
    if event_name == "workflow_run":
        run = event["workflow_run"]
        if run["name"] not in config["ci"]["workflows"]:
            return "skip"
        return {"in_progress": "mark", "completed": "observe"}.get(event.get("action", ""), "skip")
    if event_name == "pull_request_target":
        action = event.get("action")
        if action == "closed":
            return "observe"
        if action == "edited" and "base" not in event.get("changes", {}):
            return "skip"
        if action in {
            "opened",
            "reopened",
            "synchronize",
            "edited",
            "ready_for_review",
            "converted_to_draft",
        }:
            return "mark"
    return "skip"


def prepare(config_path: str, action_ref: str, pr_number: str, update_labels: bool) -> int:
    verify_source(action_ref, os.environ.get("PMR_WORKFLOW_SHA", ""))
    api = GitHub(os.environ["GITHUB_REPOSITORY"])
    config, config_sha = trusted_config(api, config_path, "", action_ref)
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    operation = route(os.environ["GITHUB_EVENT_NAME"], event, config, pr_number, update_labels)
    if operation == "mark" and not config["publication"]["checks"]:
        operation = "skip"
    output(
        {
            "config-sha": config_sha,
            "operation": operation,
            "checks": str(config["publication"]["checks"]).lower(),
            "labels": str(update_labels).lower(),
        }
    )
    return 0


def run_action() -> int:
    values = {
        name: os.environ.get("PMR_" + name.upper().replace("-", "_"), "")
        for name in (
            "operation",
            "action-ref",
            "config-path",
            "config-sha",
            "repository",
            "pr-number",
            "event-path",
            "report-dir",
            "artifact-name",
        )
    }
    operation = values["operation"]
    if operation not in {"observe", "mark", "publish-checks", "publish-labels"}:
        raise EvaluationError("unknown operation")
    publishing = operation.startswith("publish-")
    if (publishing and (values["pr-number"] or values["event-path"])) or (
        operation == "mark" and values["pr-number"]
    ):
        raise EvaluationError("irrelevant PR/event input")
    if publishing and not values["config-sha"]:
        raise EvaluationError("publication requires the observation config-sha")
    if operation == "mark" and (values["report-dir"] or values["artifact-name"]):
        raise EvaluationError("mark does not use reports or artifacts")
    workflow_sha = os.environ.get("PMR_WORKFLOW_SHA", "")
    verify_source(values["action-ref"], workflow_sha)
    api = GitHub(values["repository"] or os.environ["GITHUB_REPOSITORY"])
    if api.repository != os.environ["GITHUB_REPOSITORY"]:
        raise EvaluationError("repository must match the workflow context")
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        raise EvaluationError("token required")
    config, config_sha = trusted_config(
        api, values["config-path"], values["config-sha"], values["action-ref"]
    )
    source = provenance(
        api.repository, values["action-ref"], config_sha, values["config-path"], workflow_sha
    )
    policy = policy_from(config)
    event = (
        {}
        if publishing
        else json.loads(Path(values["event-path"] or os.environ["GITHUB_EVENT_PATH"]).read_text())
    )
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    run_url = f"https://github.com/{api.repository}/actions/runs/{positive(run_id)}/attempts/{positive(attempt)}"
    if operation == "mark":
        if not config["publication"]["checks"]:
            raise EvaluationError("Check publication is disabled")
        if route(os.environ["GITHUB_EVENT_NAME"], event, config, "", False) != "mark":
            raise EvaluationError("event does not request a marker")
        results = publish_checks.mark_event(publish.GitHub(api.repository), event, run_url)
        print(json.dumps(results, ensure_ascii=False))
        output({"config-sha": config_sha})
        return 0
    name = artifact_name(run_id, attempt)
    if values["artifact-name"] != name or not values["report-dir"]:
        raise EvaluationError("this run/attempt artifact-name and report-dir are required")
    directory = Path(values["report-dir"]).resolve()
    output(
        {
            "config-sha": config_sha,
            "report-dir": directory,
            "manifest": directory / "manifest.json",
            "artifact-name": name,
        }
    )
    if operation == "observe":
        if (
            route(os.environ["GITHUB_EVENT_NAME"], event, config, values["pr-number"], False)
            != "observe"
        ):
            raise EvaluationError("event does not request observation")
        code = observe(
            api,
            policy,
            event,
            positive(values["pr-number"]) if values["pr-number"] else None,
            directory,
            source,
            run_id,
            attempt,
            name,
        )
        summary(directory / "summary.md")
        return code
    if operation == "publish-checks" and not config["publication"]["checks"]:
        raise EvaluationError("Check publication is disabled")
    if operation == "publish-labels" and (
        config["publication"]["labels"] != "manual"
        or os.environ["GITHUB_EVENT_NAME"] != "workflow_dispatch"
    ):
        raise EvaluationError("labels require an explicit manual invocation")
    reports = load_reports(directory, api.repository, run_id, attempt, name, source, policy)
    if operation == "publish-labels":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        inputs = event.get("inputs", {})
        manifest = json.loads((directory / "manifest.json").read_text())
        if (
            inputs.get("pr-number")
            or inputs.get("update-labels") not in ("true", True)
            or manifest["selection"] != "all_open"
        ):
            raise EvaluationError(
                "labels require explicit update-labels and an all-open observation"
            )
    writer = publish.GitHub(api.repository)
    failed = False
    if operation == "publish-labels":
        publish.ensure_labels(writer)
    for number, report in reports:
        try:
            result = (
                publish_checks.publish_report(writer, number, report, run_url, name)
                if operation == "publish-checks"
                else publish.publish_pr(writer, number, report)
            )
            print(json.dumps({"pr": number, "publication": result}, ensure_ascii=False))
        except (publish.PublishError, KeyError, TypeError, ValueError) as error:
            failed = True
            print(json.dumps({"pr": number, "error": str(error)}))
    if operation == "publish-labels":
        publish.cleanup_closed(writer)
    return int(failed)


def save_failure(directory: Path, error: Exception) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    write_json(
        directory / "collection-error.json", {"decision": "INSUFFICIENT_DATA", "error": str(error)}
    )
    # 準備失敗時は信頼済み provenance を捏造しない。publisher は必ず拒否する。
    write_json(
        directory / "manifest.json",
        {
            "format": MANIFEST_FORMAT,
            "schema_version": 1,
            "collection_failed": True,
            "reports": [],
            "provenance": None,
        },
    )
    path = directory / "summary.md"
    path.write_text("INSUFFICIENT_DATA: <code>" + html.escape(str(error)) + "</code>\n")
    summary(path)
