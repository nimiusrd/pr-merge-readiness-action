"""入力の矛盾・設定の固定・公開失敗を境界で検証する。"""

import pytest
import base64
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch
from pr_merge_readiness import runtime
from pr_merge_readiness.artifacts import provenance, write_json
from pr_merge_readiness.config import ACTION_REPOSITORY
from pr_merge_readiness.observe import observe
from pr_merge_readiness.publish import PublishError
from tests.test_collect import FixtureAPI
from tests.test_config import ROOT, config, config_text
from tests.test_support import BASE, HEAD, policy, pr_event


def test_routes_and_manual_input_conflicts():
    value = config()
    for action in ("opened", "reopened", "synchronize", "ready_for_review", "converted_to_draft"):
        assert runtime.route("pull_request", pr_event(action), value, "", False) == "observe"
    assert runtime.route("pull_request", pr_event("closed"), value, "", False) == "observe"
    for changed in ("title", "body"):
        assert (
            runtime.route(
                "pull_request",
                {**pr_event("edited"), "changes": {changed: {}}},
                value,
                "",
                False,
            )
            == "skip"
        )
    assert (
        runtime.route(
            "pull_request", {**pr_event("edited"), "changes": {"base": {}}}, value, "", False
        )
        == "observe"
    )
    for event in ("workflow_run", "pull_request_review", "schedule"):
        assert runtime.route(event, {}, value, "", False) == "skip"
    for event, number, labels in (
        ("push", "1", False),
        ("push", "", True),
        ("workflow_dispatch", "1", True),
        ("workflow_dispatch", "01", False),
    ):
        with pytest.raises(ValueError):
            runtime.route(event, {}, value, number, labels)
    assert runtime.route("workflow_dispatch", {}, value, "1", False) == "observe"
    for mode in ("auto", "manual"):
        value["publication"]["labels"] = mode
        assert runtime.route("workflow_dispatch", {}, value, "", True) == "observe"
    value["publication"]["labels"] = "off"
    with pytest.raises(ValueError):
        runtime.route("workflow_dispatch", {}, value, "", True)


def test_default_branch_resolved_once_and_pinned_for_later_jobs():
    text = config_text().encode()
    api = Mock(prefix="/repos/example/project")
    api.request.side_effect = [
        {"default_branch": "main"},
        {"object": {"sha": BASE}},
        {"type": "file", "encoding": "base64", "content": base64.b64encode(text).decode()},
    ]
    value, pinned = runtime.trusted_config(api, ".github/config.toml", "")
    assert pinned == BASE
    assert api.request.call_count == 3
    api.reset_mock(side_effect=True)
    api.request.return_value = {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(text).decode(),
    }
    assert runtime.trusted_config(api, ".github/config.toml", pinned) == (value, BASE)
    api.request.assert_called_once_with(
        f"/repos/example/project/contents/.github/config.toml?ref={BASE}"
    )
    api.request.side_effect = ValueError("HTTP 403")
    with pytest.raises(ValueError, match="403"):
        runtime.trusted_config(api, ".github/config.toml", BASE)


def test_source_must_match_the_pinned_action():
    env = {
        "PMR_SOURCE_REF": HEAD,
        "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
    }
    with patch.dict(os.environ, env, clear=True):
        runtime.verify_source(HEAD)
        for key, wrong in (
            ("PMR_SOURCE_REF", "main"),
            ("PMR_SOURCE_REPOSITORY", "other/action"),
        ):
            with patch.dict(os.environ, {key: wrong}), pytest.raises(ValueError):
                runtime.verify_source(HEAD)
        with pytest.raises(ValueError):
            runtime.verify_source(BASE)
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("pr_merge_readiness.runtime.subprocess.run") as git,
    ):
        git.return_value.stdout = str(ROOT.parent)
        with pytest.raises(ValueError, match="checkout SHA mismatch"):
            runtime.verify_source(HEAD)


@pytest.mark.parametrize(
    "operation,extra",
    (
        ("unknown", {}),
        ("prepare", {"PMR_CONFIG_SHA": BASE}),
        ("prepare", {"PMR_PR_NUMBER": "1"}),
        ("prepare", {"PMR_EVENT_PATH": "event.json"}),
        ("prepare", {"PMR_REPORT_DIR": "reports"}),
        ("prepare", {"PMR_ARTIFACT_NAME": "artifact"}),
        ("validate-config", {}),
        ("validate-config", {"PMR_CONFIG_SHA": BASE, "PMR_PR_NUMBER": "1"}),
        ("validate-config", {"PMR_CONFIG_SHA": BASE, "PMR_EVENT_PATH": "event.json"}),
        ("validate-config", {"PMR_CONFIG_SHA": BASE, "PMR_REPORT_DIR": "reports"}),
        ("validate-config", {"PMR_CONFIG_SHA": BASE, "PMR_ARTIFACT_NAME": "artifact"}),
        ("mark", {"PMR_PR_NUMBER": "1"}),
        ("mark", {"PMR_REPORT_DIR": "report"}),
        ("mark", {"PMR_ARTIFACT_NAME": "x"}),
        ("publish-checks", {}),
        ("publish-checks", {"PMR_CONFIG_SHA": BASE, "PMR_EVENT_PATH": "event.json"}),
        ("publish-labels", {"PMR_CONFIG_SHA": BASE, "PMR_PR_NUMBER": "1"}),
    ),
)
def test_operation_conflicts_fail_before_api_or_source_access(operation, extra):
    with (
        patch.dict(os.environ, {"PMR_OPERATION": operation, **extra}, clear=True),
        patch.object(runtime, "GitHub") as api,
    ):
        with pytest.raises(ValueError):
            runtime.run_action()
        api.assert_not_called()


def test_publication_validates_all_reports_before_first_write_and_continues_after_failure():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = provenance("example/project", HEAD, BASE, ".github/config.toml")
        observe(FixtureAPI(), policy(), {}, 1, root, source, "42", "1", "pr-merge-readiness-42-1")
        second = json.loads((root / "pr-1.json").read_text())
        second["observations"]["pr"]["number"] = 2
        write_json(root / "pr-2.json", second)
        manifest = json.loads((root / "manifest.json").read_text())
        manifest["reports"].append("pr-2.json")
        write_json(root / "manifest.json", manifest)
        value = config()
        env = {
            "PMR_OPERATION": "publish-checks",
            "PMR_ACTION_REF": HEAD,
            "PMR_CONFIG_PATH": ".github/config.toml",
            "PMR_CONFIG_SHA": BASE,
            "PMR_REPORT_DIR": directory,
            "PMR_ARTIFACT_NAME": "pr-merge-readiness-42-1",
            "GITHUB_REPOSITORY": "example/project",
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GH_TOKEN": "test",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(runtime, "verify_source"),
            patch.object(runtime, "trusted_config", return_value=(value, BASE)),
            patch.object(runtime.publish_checks, "publish_report") as publish,
        ):
            malformed = deepcopy(second)
            malformed["policy_sha256"] = "wrong"
            write_json(root / "pr-2.json", malformed)
            with pytest.raises(ValueError):
                runtime.run_action()
            publish.assert_not_called()
            write_json(root / "pr-2.json", second)
            publish.side_effect = [PublishError("API 403"), "published"]
            assert runtime.run_action() == 1
            assert publish.call_count == 2


def test_labels_require_explicit_all_open_manual_observation():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = provenance("example/project", HEAD, BASE, ".github/config.toml")
        observe(FixtureAPI(), policy(), {}, 1, root, source, "42", "1", "pr-merge-readiness-42-1")
        event = root / "event.json"
        value = config()
        env = {
            "PMR_OPERATION": "publish-labels",
            "PMR_ACTION_REF": HEAD,
            "PMR_CONFIG_PATH": ".github/config.toml",
            "PMR_CONFIG_SHA": BASE,
            "PMR_REPORT_DIR": directory,
            "PMR_ARTIFACT_NAME": "pr-merge-readiness-42-1",
            "GITHUB_REPOSITORY": "example/project",
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_EVENT_PATH": str(event),
            "GH_TOKEN": "test",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(runtime, "verify_source"),
            patch.object(runtime, "trusted_config", return_value=(value, BASE)),
            patch.object(runtime.publish, "ensure_labels") as ensure,
        ):
            for inputs in (
                {},
                {"update-labels": "false"},
                {"update-labels": "true", "pr-number": "1"},
                {"update-labels": "true"},
            ):
                write_json(event, {"inputs": inputs})
                with pytest.raises(ValueError):
                    runtime.run_action()
                ensure.assert_not_called()


def test_outputs_cannot_inject_additional_fields():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "output"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(path)}), pytest.raises(ValueError):
            runtime.output({"safe": "value\ninjected=true"})
        assert path.read_text() == ""
