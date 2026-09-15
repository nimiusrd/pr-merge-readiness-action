"""設定確定と提案検証を直接 Action から使い、書込み権限を要求しない。"""

import base64
import json
import os
from unittest.mock import Mock, patch

import pytest

from pr_merge_readiness import runtime
from pr_merge_readiness.cli import main
from pr_merge_readiness.config import ACTION_REPOSITORY
from tests.test_config import config_text
from tests.test_support import ACTION_SHA, BASE, pr_event


def blob(text):
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(text.encode()).decode(),
    }


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    event = tmp_path / "event.json"
    event.write_text("{}")
    env = {
        "GITHUB_REPOSITORY": "sample-org/project",
        "GH_TOKEN": "read-only-token",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
        "PMR_SOURCE_REF": ACTION_SHA,
        "PMR_ACTION_REF": ACTION_SHA,
        "PMR_CONFIG_PATH": ".github/config.toml",
    }
    api = Mock(repository="sample-org/project", prefix="/repos/sample-org/project")
    text = config_text()
    with patch.dict(os.environ, env, clear=True), patch.object(runtime, "GitHub", return_value=api):
        yield api, text, event, tmp_path / "outputs"


@pytest.mark.parametrize(
    "event_name,event,checks,expected",
    [
        ("workflow_dispatch", {"inputs": {"pr-number": "12"}}, True, ("observe", "12", "false")),
        ("workflow_dispatch", {"inputs": {"update-labels": "true"}}, True, ("observe", "", "true")),
        ("workflow_dispatch", {"inputs": {"update-labels": True}}, False, ("observe", "", "true")),
        ("pull_request", pr_event(), True, ("observe", "", "false")),
        ("pull_request", pr_event(), False, ("observe", "", "false")),
        (
            "pull_request",
            {**pr_event("edited"), "changes": {"title": {}}},
            True,
            ("skip", "", "false"),
        ),
        (
            "workflow_run",
            {"action": "completed", "workflow_run": {"name": "CI"}},
            True,
            ("skip", "", "false"),
        ),
    ],
)
def test_prepare_selects_jobs_and_pins_default_configuration(
    context, event_name, event, checks, expected
):
    api, text, path, output = context
    if not checks:
        text = text.replace("checks = true", "checks = false")
    api.request.side_effect = [{"default_branch": "trunk"}, {"object": {"sha": BASE}}, blob(text)]
    path.write_text(json.dumps(event))
    os.environ.update(PMR_OPERATION="prepare", GITHUB_EVENT_NAME=event_name)
    with patch.object(runtime.publish, "GitHub") as writer:
        assert runtime.run_action() == 0
        writer.assert_not_called()
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert (values["operation"], values["pr-number"], values["labels"]) == expected
    assert values["config-sha"] == BASE
    assert values["checks"] == str(checks).lower()
    assert [call.args[0] for call in api.request.call_args_list] == [
        api.prefix,
        api.prefix + "/git/ref/heads/trunk",
        api.prefix + f"/contents/.github/config.toml?ref={BASE}",
    ]


@pytest.mark.parametrize(
    "inputs",
    [
        {"pr-number": "1", "update-labels": True},
        {"pr-number": "01"},
        {"pr-number": 1},
        {"pr-number": 0},
        {"pr-number": None},
        {"update-labels": 1},
        {"update-labels": 0},
        {"update-labels": None},
        {"update-labels": "yes"},
        ["unexpected"],
    ],
)
def test_prepare_rejects_conflicting_or_malformed_actual_manual_inputs(context, inputs):
    api, text, event, output = context
    api.request.side_effect = [{"default_branch": "main"}, {"object": {"sha": BASE}}, blob(text)]
    event.write_text(json.dumps({"inputs": inputs}))
    os.environ["PMR_OPERATION"] = "prepare"
    with pytest.raises(ValueError), patch.object(runtime.publish, "GitHub") as writer:
        runtime.run_action()
    writer.assert_not_called()
    assert not output.exists()


@pytest.mark.parametrize(
    "mutation", ["valid", "unknown-key", "legacy-action-ref", "permission-denied"]
)
def test_validate_config_reads_only_explicit_proposal_data(context, mutation):
    api, text, event, output = context
    if mutation == "unknown-key":
        text = "unknown = true\n" + text
    if mutation == "legacy-action-ref":
        text = f'action_ref = "{"e" * 40}"\n' + text
    api.request.return_value = blob(text)
    if mutation == "permission-denied":
        api.request.side_effect = ValueError("HTTP 403")
    os.environ.update(PMR_OPERATION="validate-config", PMR_CONFIG_SHA=BASE)
    event.unlink()  # 提案の読み取りはイベントや checkout のコードを読み込まない。
    with (
        patch.object(runtime.publish, "GitHub") as writer,
        patch.object(runtime, "observe") as observe,
    ):
        if mutation in {"valid", "legacy-action-ref"}:
            assert runtime.run_action() == 0
            assert output.read_text() == f"config-sha={BASE}\n"
        else:
            with pytest.raises(ValueError):
                runtime.run_action()
            assert not output.exists()
        writer.assert_not_called()
        observe.assert_not_called()
    api.request.assert_called_once_with(api.prefix + f"/contents/.github/config.toml?ref={BASE}")


def test_preparation_failure_saves_current_diagnostic_artifact(context, monkeypatch):
    api, _, _, _ = context
    api.request.side_effect = ValueError("HTTP 403")
    os.environ["PMR_OPERATION"] = "prepare"
    monkeypatch.setattr("sys.argv", ["cli.py", "action"])
    assert main() == 1
    from pathlib import Path

    manifest = json.loads(Path("preparation-report/manifest.json").read_text())
    assert manifest["collection_failed"] is True
    assert manifest["provenance"] is None
    assert "403" in Path("preparation-report/summary.md").read_text()
