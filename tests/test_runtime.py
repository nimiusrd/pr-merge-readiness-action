"""入力の矛盾・設定の固定・公開失敗を境界で検証する。"""

import base64
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from pr_merge_readiness import runtime
from pr_merge_readiness.artifacts import provenance, write_json
from pr_merge_readiness.config import ACTION_REPOSITORY, WORKFLOW_PATH
from pr_merge_readiness.observe import observe
from pr_merge_readiness.publish import PublishError
from tests.test_collect import FixtureAPI
from tests.test_config import ROOT, config
from tests.test_support import BASE, HEAD, policy


class RuntimeTests(unittest.TestCase):
    def test_routes_and_manual_input_conflicts(self):
        value = config()
        for action, expected in (
            ("in_progress", "mark"),
            ("completed", "observe"),
            ("requested", "skip"),
        ):
            event = {"action": action, "workflow_run": {"name": value["ci"]["workflows"][0]}}
            self.assertEqual(runtime.route("workflow_run", event, value, "", False), expected)
            event["workflow_run"]["name"] = "Unrelated CI"
            self.assertEqual(runtime.route("workflow_run", event, value, "", False), "skip")
        for action in (
            "opened",
            "reopened",
            "synchronize",
            "ready_for_review",
            "converted_to_draft",
        ):
            self.assertEqual(
                runtime.route("pull_request_target", {"action": action}, value, "", False), "mark"
            )
        self.assertEqual(
            runtime.route("pull_request_target", {"action": "closed"}, value, "", False), "observe"
        )
        for changed in ("title", "body"):
            self.assertEqual(
                runtime.route(
                    "pull_request_target",
                    {"action": "edited", "changes": {changed: {}}},
                    value,
                    "",
                    False,
                ),
                "skip",
            )
        self.assertEqual(
            runtime.route(
                "pull_request_target",
                {"action": "edited", "changes": {"base": {}}},
                value,
                "",
                False,
            ),
            "mark",
        )
        for event in ("pull_request_review", "schedule"):
            self.assertEqual(runtime.route(event, {}, value, "", False), "skip")
        for event, number, labels in (
            ("push", "1", False),
            ("push", "", True),
            ("workflow_dispatch", "1", True),
            ("workflow_dispatch", "01", False),
        ):
            with self.assertRaises(ValueError):
                runtime.route(event, {}, value, number, labels)
        self.assertEqual(runtime.route("workflow_dispatch", {}, value, "1", False), "observe")
        self.assertEqual(runtime.route("workflow_dispatch", {}, value, "", True), "observe")
        value["publication"]["labels"] = "off"
        with self.assertRaises(ValueError):
            runtime.route("workflow_dispatch", {}, value, "", True)

    def test_default_branch_resolved_once_and_pinned_for_later_jobs(self):
        text = (ROOT / "examples/devops-tycoon.toml").read_bytes()
        api = Mock(prefix="/repos/example/project")
        api.request.side_effect = [
            {"default_branch": "main"},
            {"object": {"sha": BASE}},
            {"type": "file", "encoding": "base64", "content": base64.b64encode(text).decode()},
        ]
        value, pinned = runtime.trusted_config(api, ".github/config.toml", "", HEAD)
        self.assertEqual(pinned, BASE)
        self.assertEqual(api.request.call_count, 3)
        api.reset_mock(side_effect=True)
        api.request.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(text).decode(),
        }
        # default branch が進んでも、後続 job は contents?ref=固定SHA の一要求だけ。
        self.assertEqual(
            runtime.trusted_config(api, ".github/config.toml", pinned, HEAD), (value, BASE)
        )
        api.request.assert_called_once_with(
            f"/repos/example/project/contents/.github/config.toml?ref={BASE}"
        )
        with self.assertRaises(ValueError):
            runtime.trusted_config(api, ".github/config.toml", BASE, "c" * 40)
        api.request.side_effect = ValueError("HTTP 403")
        with self.assertRaisesRegex(ValueError, "403"):
            runtime.trusted_config(api, ".github/config.toml", BASE, HEAD)

    def test_source_and_reusable_workflow_must_match(self):
        env = {
            "PMR_SOURCE_REF": HEAD,
            "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
            "PMR_WORKFLOW_REPOSITORY": ACTION_REPOSITORY,
            "PMR_WORKFLOW_PATH": WORKFLOW_PATH,
        }
        with patch.dict(os.environ, env, clear=True):
            runtime.verify_source(HEAD, HEAD)
            for key, wrong in (
                ("PMR_SOURCE_REF", "main"),
                ("PMR_SOURCE_REPOSITORY", "other/action"),
                ("PMR_WORKFLOW_PATH", ".github/workflows/other.yml"),
                ("PMR_WORKFLOW_REPOSITORY", "other/action"),
            ):
                with patch.dict(os.environ, {key: wrong}), self.assertRaises(ValueError):
                    runtime.verify_source(HEAD, HEAD)
            with self.assertRaises(ValueError):
                runtime.verify_source(HEAD, BASE)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pr_merge_readiness.runtime.subprocess.run") as git,
        ):
            git.return_value.stdout = str(ROOT.parent)
            with self.assertRaisesRegex(ValueError, "checkout SHA mismatch"):
                runtime.verify_source(HEAD)

    def test_operation_conflicts_fail_before_api_or_source_access(self):
        for operation, extra in (
            ("unknown", {}),
            ("mark", {"PMR_PR_NUMBER": "1"}),
            ("mark", {"PMR_REPORT_DIR": "report"}),
            ("mark", {"PMR_ARTIFACT_NAME": "x"}),
            ("publish-checks", {}),
            ("publish-checks", {"PMR_CONFIG_SHA": BASE, "PMR_EVENT_PATH": "event.json"}),
            ("publish-labels", {"PMR_CONFIG_SHA": BASE, "PMR_PR_NUMBER": "1"}),
        ):
            with (
                patch.dict(os.environ, {"PMR_OPERATION": operation, **extra}, clear=True),
                patch.object(runtime, "GitHub") as api,
            ):
                with self.assertRaises(ValueError):
                    runtime.run_action()
                api.assert_not_called()

    def test_publication_validates_all_reports_before_first_write_and_continues_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = provenance("example/project", HEAD, BASE, ".github/config.toml")
            observe(
                FixtureAPI(), policy(), {}, 1, root, source, "42", "1", "pr-merge-readiness-42-1"
            )
            second = json.loads((root / "pr-1.json").read_text())
            second["observations"]["pr"]["number"] = 2
            write_json(root / "pr-2.json", second)
            manifest = json.loads((root / "manifest.json").read_text())
            manifest["reports"].append("pr-2.json")
            write_json(root / "manifest.json", manifest)
            value = config()
            value["ci"]["required_checks"] = policy()["required_checks"]
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
                with self.assertRaises(ValueError):
                    runtime.run_action()
                publish.assert_not_called()
                write_json(root / "pr-2.json", second)
                publish.side_effect = [PublishError("API 403"), "published"]
                self.assertEqual(runtime.run_action(), 1)
                self.assertEqual(publish.call_count, 2)

    def test_labels_require_explicit_all_open_manual_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = provenance("example/project", HEAD, BASE, ".github/config.toml")
            observe(
                FixtureAPI(), policy(), {}, 1, root, source, "42", "1", "pr-merge-readiness-42-1"
            )
            event = root / "event.json"
            value = config()
            value["ci"]["required_checks"] = policy()["required_checks"]
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
                    with self.assertRaises(ValueError):
                        runtime.run_action()
                    ensure.assert_not_called()

    def test_outputs_cannot_inject_additional_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output"
            with (
                patch.dict(os.environ, {"GITHUB_OUTPUT": str(path)}),
                self.assertRaises(ValueError),
            ):
                runtime.output({"safe": "value\ninjected=true"})
            self.assertEqual(path.read_text(), "")
