"""Composite Action の入力・信頼境界。"""

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


def verify_source(expected: str) -> None:
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
    if event_name == "pull_request":
        return pr_event_operation(event)
    return "skip"


def prepare(config: Config, config_sha: str, *, automatic: bool = False) -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    inputs = event.get("inputs", {})
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise EvaluationError("manual inputs must be an object")
    pr_number = inputs.get("pr-number", "")
    labels = inputs.get("update-labels", False)
    if not isinstance(pr_number, str) or not (
        type(labels) is bool or isinstance(labels, str) and labels in ("true", "false")
    ):
        raise EvaluationError("invalid manual inputs")
    update_labels = labels in ("true", True)
    operation = route(os.environ["GITHUB_EVENT_NAME"], event, config, pr_number, update_labels)
    values = {
        "config-sha": config_sha,
        "operation": operation,
        "checks": str(config["publication"]["checks"]).lower(),
        "labels": str(update_labels).lower(),
        "pr-number": pr_number,
    }
    if automatic and operation == "observe":
        name = artifact_name(os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"])
        directory = (Path(os.environ.get("RUNNER_TEMP", ".")) / name).resolve()
        values.update(
            {
                "report-dir": str(directory),
                "manifest": str(directory / "manifest.json"),
                "artifact-name": name,
            }
        )
    output(values)
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
    operation = values["operation"] or "run"
    automatic = operation == "run"
    proposal_sha = ""
    if automatic:
        if any(
            values[key]
            for key in ("config-sha", "pr-number", "event-path", "report-dir", "artifact-name")
        ):
            raise EvaluationError("run derives configuration, PR, event and report inputs")
        event_name = os.environ["GITHUB_EVENT_NAME"]
        if event_name == "pull_request":
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            operation = "prepare" if pr_event_operation(event) == "observe" else "skip"
            if operation == "prepare":
                proposal_sha = sha(event["pull_request"]["head"]["sha"])
        elif event_name == "push":
            values["config-sha"] = os.environ["GITHUB_SHA"]
            operation = "validate-config"
        elif event_name == "workflow_dispatch":
            operation = "prepare"
        else:
            operation = "skip"
    values["action-ref"] = values["action-ref"] or os.environ.get("PMR_SOURCE_REF", "")
    values["config-path"] = values["config-path"] or ".github/pr-merge-readiness.toml"
    if not (automatic and operation == "skip") and operation not in {
        "prepare",
        "validate-config",
        "observe",
        "publish-checks",
        "publish-labels",
    }:
        raise EvaluationError("unknown operation")
    publishing = operation.startswith("publish-")
    if operation in {"prepare", "validate-config"}:
        if any(values[key] for key in ("pr-number", "event-path", "report-dir", "artifact-name")):
            raise EvaluationError("configuration operations do not take PR/event/report inputs")
        if operation == "prepare" and values["config-sha"]:
            raise EvaluationError("prepare resolves the default branch once")
        if operation == "validate-config" and not values["config-sha"]:
            raise EvaluationError("validate-config requires an explicit proposal config-sha")
    if publishing and (values["pr-number"] or values["event-path"]):
        raise EvaluationError("irrelevant PR/event input")
    if publishing and not values["config-sha"]:
        raise EvaluationError("publication requires the observation config-sha")
    verify_source(values["action-ref"])
    if automatic and operation == "skip":
        output({"operation": "skip"})
        return 0
    api = GitHub(values["repository"] or os.environ["GITHUB_REPOSITORY"])
    if api.repository != os.environ["GITHUB_REPOSITORY"]:
        raise EvaluationError("repository must match the workflow context")
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        raise EvaluationError("token required")
    if proposal_sha:
        # 提案は検証だけに使い、観測・公開のpolicyはdefault branchから別に確定する。
        trusted_config(api, values["config-path"], proposal_sha, values["action-ref"])
    config, config_sha = trusted_config(
        api, values["config-path"], values["config-sha"], values["action-ref"]
    )
    if operation == "prepare":
        return prepare(config, config_sha, automatic=automatic)
    if operation == "validate-config":
        if automatic:
            output({"operation": "validate-config"})
        output({"config-sha": config_sha})
        print(json.dumps({"valid": True, "config_sha": config_sha}))
        return 0
    source = provenance(api.repository, values["action-ref"], config_sha, values["config-path"])
    policy = policy_from(config)
    event = (
        {}
        if publishing and os.environ.get("GITHUB_EVENT_NAME") != "pull_request"
        else json.loads(Path(values["event-path"] or os.environ["GITHUB_EVENT_PATH"]).read_text())
    )
    expected_head = (
        sha(event["pull_request"]["head"]["sha"])
        if os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
        else None
    )
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    run_url = f"https://github.com/{api.repository}/actions/runs/{positive(run_id)}/attempts/{positive(attempt)}"
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
            expected_head=expected_head,
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
                publish_checks.publish_report(
                    writer, number, report, run_url, name, expected_head=expected_head
                )
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
