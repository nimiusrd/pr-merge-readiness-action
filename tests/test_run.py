"""自動入口のイベント選択と、提案設定から公開への隔離を検証する。"""

import base64
import json
import os
from unittest.mock import Mock, patch

import pytest

from pr_merge_readiness import runtime
from pr_merge_readiness.cli import main
from pr_merge_readiness.config import ACTION_REPOSITORY
from tests.test_config import ROOT, config
from tests.test_support import BASE, HEAD


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    event = tmp_path / "event.json"
    event.write_text("{}")
    api = Mock(repository="sample/project", prefix="/repos/sample/project")
    blob = {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode((ROOT / "examples/minimal.toml").read_bytes()).decode(),
    }
    env = {
        "GH_TOKEN": "read-only-token",
        "GITHUB_REPOSITORY": api.repository,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_SHA": HEAD,
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "RUNNER_TEMP": str(tmp_path),
        "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
        "PMR_SOURCE_REF": config()["action_ref"],
    }
    with patch.dict(os.environ, env, clear=True), patch.object(runtime, "GitHub", return_value=api):
        yield api, blob, event, tmp_path / "outputs"


@pytest.mark.parametrize("event_name", ["pull_request", "push"])
def test_run_validates_only_the_proposal_with_a_read_only_token(context, event_name):
    api, blob, event, output = context
    api.request.return_value = blob
    event.write_text(json.dumps({"pull_request": {"head": {"sha": BASE}}}))
    os.environ["GITHUB_EVENT_NAME"] = event_name
    with (
        patch.object(runtime, "observe") as observe,
        patch.object(runtime.publish, "GitHub") as writer,
    ):
        assert runtime.run_action() == 0
        observe.assert_not_called()
        writer.assert_not_called()
    selected = BASE if event_name == "pull_request" else HEAD
    api.request.assert_called_once_with(
        f"/repos/sample/project/contents/.github/pr-merge-readiness.toml?ref={selected}"
    )
    assert f"config-sha={selected}" in output.read_text()
    assert "operation=validate-config" in output.read_text()


@pytest.mark.parametrize("event_name", ["pull_request", "push"])
def test_invalid_proposal_never_falls_back_to_operational_config(context, event_name):
    api, _, _, _ = context
    api.request.side_effect = ValueError("invalid configuration")
    os.environ["GITHUB_EVENT_NAME"] = event_name
    with patch.object(runtime, "prepare") as prepare, pytest.raises((KeyError, ValueError)):
        runtime.run_action()
    prepare.assert_not_called()


@pytest.mark.parametrize(
    "event_name,event,expected",
    [
        ("workflow_dispatch", {"inputs": {"pr-number": "12"}}, "observe"),
        ("workflow_dispatch", {"inputs": {"update-labels": True}}, "observe"),
        ("pull_request_target", {"action": "opened"}, "mark"),
        ("pull_request_target", {"action": "closed"}, "observe"),
        ("pull_request_target", {"action": "edited", "changes": {"title": {}}}, "skip"),
        ("workflow_run", {"action": "in_progress", "workflow_run": {"name": "CI"}}, "mark"),
        ("workflow_run", {"action": "completed", "workflow_run": {"name": "CI"}}, "observe"),
    ],
)
def test_run_prepares_the_actual_event_and_report_paths(context, event_name, event, expected):
    api, blob, path, output = context
    api.request.side_effect = [{"default_branch": "trunk"}, {"object": {"sha": BASE}}, blob]
    path.write_text(json.dumps(event))
    os.environ.update(GITHUB_EVENT_NAME=event_name, PMR_OPERATION="run")
    with patch.object(runtime.publish, "GitHub") as writer:
        assert runtime.run_action() == 0
        writer.assert_not_called()
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["operation"] == expected
    assert values["config-sha"] == BASE
    assert api.request.call_count == 3
    if expected == "observe":
        assert values["artifact-name"] == "pr-merge-readiness-42-2"
        assert values["report-dir"] == str(path.parent / "pr-merge-readiness-42-2")
        assert values["manifest"] == values["report-dir"] + "/manifest.json"
    else:
        assert "artifact-name" not in values


def test_unsupported_event_skips_without_fetching_configuration(context):
    api, _, _, output = context
    os.environ["GITHUB_EVENT_NAME"] = "pull_request_review"
    assert runtime.run_action() == 0
    api.request.assert_not_called()
    assert output.read_text() == "operation=skip\n"


@pytest.mark.parametrize(
    "key", ["CONFIG_SHA", "PR_NUMBER", "EVENT_PATH", "REPORT_DIR", "ARTIFACT_NAME"]
)
def test_run_does_not_accept_overrides_for_event_derived_inputs(context, key):
    api, _, _, _ = context
    os.environ["PMR_" + key] = "override"
    with pytest.raises(ValueError, match="run derives"):
        runtime.run_action()
    api.request.assert_not_called()


@pytest.mark.parametrize("reference", ["main", "v0.3.0", "", "a" * 40])
def test_run_verifies_the_actual_source_and_config_pin(context, reference):
    api, blob, _, _ = context
    api.request.side_effect = [{"default_branch": "trunk"}, {"object": {"sha": BASE}}, blob]
    os.environ["PMR_SOURCE_REF"] = reference
    with pytest.raises(ValueError):
        runtime.run_action()


def test_run_preparation_failure_saves_diagnostics(context, monkeypatch):
    api, _, _, output = context
    api.request.side_effect = ValueError("HTTP 403")
    monkeypatch.setattr("sys.argv", ["cli.py", "action"])
    assert main() == 1
    assert "403" in (output.parent / "preparation-report/summary.md").read_text()


def test_failed_validation_does_not_create_operational_diagnostics(context, monkeypatch):
    api, _, _, output = context
    api.request.side_effect = ValueError("HTTP 403")
    os.environ["GITHUB_EVENT_NAME"] = "push"
    monkeypatch.setattr("sys.argv", ["cli.py", "action"])
    assert main() == 1
    assert not (output.parent / "preparation-report").exists()
