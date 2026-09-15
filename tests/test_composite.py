"""実際の action.yml の条件・環境変数を使い、API と artifact 転送だけを代替する。"""

import base64
import json
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from pr_merge_readiness import runtime
from pr_merge_readiness.cli import main
from pr_merge_readiness.collect import CollectionError
from pr_merge_readiness.config import ACTION_REPOSITORY
from tests.test_collect import FixtureAPI as Reader
from tests.test_config import ROOT, config, config_text
from tests.test_publish import FixtureAPI as LabelWriter
from pr_merge_readiness.publish import DECISION_LABELS
from tests.test_publish_checks import FixtureAPI as CheckWriter
from tests.test_support import BASE, HEAD, pr_event


class Composite:
    """この Action が使用する式だけを評価する、テスト専用の step 実行器。"""

    def __init__(self, directory, inputs=None, fail_upload=False, cancel_before=None, tamper=False):
        self.action = yaml.load((ROOT / "action.yml").read_text(), Loader=yaml.BaseLoader)
        self.directory = directory
        self.context = {
            "github.action_path": str(ROOT),
            "github.action_ref": config()["action_ref"],
            "github.action_repository": ACTION_REPOSITORY,
            "github.repository": "example/project",
            "github.token": "test-token",
            "github.event_name": os.environ["GITHUB_EVENT_NAME"],
            "github.run_id": "42",
            "github.run_attempt": "2",
        }
        for name, value in self.action["inputs"].items():
            self.context["inputs." + name] = self.render(value.get("default", ""))
        self.context.update({"inputs." + key: value for key, value in (inputs or {}).items()})
        self.fail_upload = fail_upload
        self.cancel_before = cancel_before
        self.tamper = tamper
        self.cancelled = False
        self.failed = False
        self.executed = []
        self.artifacts = {}

    def evaluate(self, expression):
        expression = expression.removeprefix("${{").removesuffix("}}").strip()
        expression = re.sub(
            r"(?:github|inputs|steps)\.[\w.-]+",
            lambda match: repr(self.context.get(match[0], "")),
            expression,
        )
        expression = expression.replace("&&", " and ").replace("||", " or ")
        expression = re.sub(r"!(?!=)", "not ", expression)
        # 対象はリポジトリ内の固定 metadata。イベントや API の文字列は repr 経由のみ。
        return eval(
            expression.strip(),
            {"__builtins__": {}},
            {
                "always": lambda: True,
                "cancelled": lambda: self.cancelled,
                "failure": lambda: self.failed,
                "success": lambda: not self.failed and not self.cancelled,
            },
        )

    def render(self, value):
        return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(self.evaluate(match[1])), value)

    def run(self):
        for step in self.action["runs"]["steps"]:
            if "id" not in step:
                continue  # uv/Python の導入は既存の隔離起動テストで検証する。
            name = step["id"]
            self.cancelled |= name == self.cancel_before
            condition = step.get("if", "success()")
            allowed = self.evaluate(condition)
            if not re.search(r"\b(success|failure|cancelled|always)\(", condition):
                allowed = allowed and not self.failed and not self.cancelled
            outcome = "skipped"
            if allowed:
                self.executed.append(name)
                if "uses" in step:
                    values = {key: self.render(value) for key, value in step["with"].items()}
                    directory = Path(values["path"])
                    assert (directory / "manifest.json").is_file()
                    assert (directory / "summary.md").is_file()
                    if self.fail_upload:
                        outcome = "failure"
                    else:
                        self.artifacts[values["name"]] = {
                            path.name: path.read_text() for path in directory.iterdir()
                        }
                        outcome = "success"
                        if self.tamper:
                            manifest = json.loads((directory / "manifest.json").read_text())
                            manifest["run_attempt"] = "1"
                            (directory / "manifest.json").write_text(json.dumps(manifest))
                else:
                    output = self.directory / (name + ".outputs")
                    environment = {key: self.render(value) for key, value in step["env"].items()}
                    with patch.dict(os.environ, {**environment, "GITHUB_OUTPUT": str(output)}):
                        code = main()
                    outcome = "success" if code == 0 else "failure"
                    if output.exists():
                        for line in output.read_text().splitlines():
                            key, value = line.split("=", 1)
                            self.context[f"steps.{name}.outputs.{key}"] = value
                self.failed |= outcome == "failure"
            self.context[f"steps.{name}.outcome"] = outcome
        return int(self.failed or self.cancelled)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["cli.py", "action"])
    event = tmp_path / "event.json"
    event.write_text('{"inputs":{"update-labels":"true"}}')
    env = {
        "GITHUB_REPOSITORY": "example/project",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_SHA": HEAD,
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "RUNNER_TEMP": str(tmp_path),
    }
    reader, checks, labels = Reader(), CheckWriter(), LabelWriter()
    checks.prs[1]["base"]["ref"] = labels.pulls[1]["base"]["ref"] = reader.state["baseRefName"]
    checks.prs[1]["updated_at"] = reader.state["updatedAt"]
    settings = {"text": config_text(), "proposal": config_text(), "requests": []}
    request, pages = reader.request, reader.pages

    def read(path, body=None):
        if "/commits?" in path:
            return request(path, body)
        settings["requests"].append(path)
        if path == reader.prefix:
            return {"default_branch": "trunk"}
        if path == reader.prefix + "/git/ref/heads/trunk":
            return {"object": {"sha": BASE}}
        assert path in {
            reader.prefix + f"/contents/.github/pr-merge-readiness.toml?ref={BASE}",
            reader.prefix + f"/contents/.github/pr-merge-readiness.toml?ref={HEAD}",
        }
        text = settings["proposal"] if path.endswith(HEAD) else settings["text"]
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(text.encode()).decode(),
        }

    def read_pages(path, key=None):
        if path == "/pulls?state=open":
            return [{"number": 1}]
        records = pages(path, key)
        return records

    monkeypatch.setattr(reader, "request", read)
    monkeypatch.setattr(reader, "pages", read_pages)
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(runtime, "GitHub", return_value=reader),
        patch.object(
            runtime.publish,
            "GitHub",
            side_effect=lambda _: (
                labels if os.environ["PMR_OPERATION"] == "publish-labels" else checks
            ),
        ),
    ):
        yield tmp_path, event, reader, checks, labels, settings


def test_manual_sync_skips_fork_pr_check_and_labels(environment):
    directory, _, _, checks, labels, _ = environment
    labels.pulls[1]["head"]["repo"] = {"id": 2, "fork": True}
    checks.prs[1]["head"]["repo"] = {"id": 2, "fork": True}
    action = Composite(directory)
    assert action.run() == 0
    assert not checks.writes and action.artifacts
    assert not labels.names()
    assert all(verb == "GET" for verb, _, _ in labels.calls)


def test_manual_sync_excludes_dependabot_labels_when_run_by_maintainer(environment, monkeypatch):
    directory, _, _, checks, labels, _ = environment
    monkeypatch.setenv("GITHUB_ACTOR", "maintainer")
    labels.pulls[1]["user"] = {"login": "dependabot[bot]", "type": "Bot"}
    checks.prs[1]["user"] = {"login": "dependabot[bot]", "type": "Bot"}
    action = Composite(directory)
    assert action.run() == 0
    assert not checks.writes and action.artifacts
    assert not labels.names()
    assert all(verb == "GET" for verb, _, _ in labels.calls)


@pytest.mark.parametrize("checks_enabled", [True, False])
def test_single_call_observes_saves_and_publishes_with_one_config_sha(environment, checks_enabled):
    directory, _, _, checks, labels, settings = environment
    if not checks_enabled:
        settings["text"] = settings["text"].replace("checks = true", "checks = false")
    action = Composite(directory)
    assert action.run() == 0
    assert action.executed == [
        "run",
        "execute",
        "observations",
        *(["checks"] if checks_enabled else []),
        "labels",
    ]
    assert bool(checks.writes) is checks_enabled
    assert labels.names()
    assert settings["requests"].count("/repos/example/project/git/ref/heads/trunk") == 1
    saved = action.artifacts["pr-merge-readiness-42-2"]
    assert json.loads(saved["pr-1.json"])["provenance"]["config"]["sha"] == BASE
    assert json.loads(saved["manifest.json"])["selection"] == "all_open"


@pytest.mark.parametrize(
    "failure",
    ["prepare", "collection", "insufficient", "upload", "checks", "labels", "tamper", "cancel"],
)
def test_failure_boundaries_preserve_evidence_and_suppress_publication(environment, failure):
    directory, _, reader, checks, labels, settings = environment
    if failure == "prepare":
        settings["text"] = "invalid TOML"
    elif failure == "collection":
        reader.pages = lambda *args, **kwargs: (_ for _ in ()).throw(CollectionError("API 403"))
    elif failure == "insufficient":
        reader.failure = "API 403"
    elif failure == "checks":
        checks.failure = "API 403"
    elif failure == "labels":
        labels.fail = lambda verb, _: verb == "POST"
    action = Composite(
        directory,
        fail_upload=failure == "upload",
        cancel_before="checks" if failure == "cancel" else None,
        tamper=failure == "tamper",
    )
    assert action.run() == 1
    if failure == "prepare":
        assert action.executed == ["run", "preparation"]
        assert "pr-merge-readiness-preparation-42-2" in action.artifacts
    else:
        assert "observations" in action.executed
        assert bool(action.artifacts) is (failure != "upload")
    assert bool(checks.writes) is (failure in {"insufficient", "labels"})
    assert bool(labels.names()) is (failure == "insufficient")
    if failure in {"prepare", "collection", "upload", "checks", "tamper", "cancel"}:
        assert "labels" not in action.executed


@pytest.mark.parametrize("event_name", ["pull_request", "push"])
def test_validation_finishes_without_reports_or_writes(environment, event_name):
    directory, event, _, checks, labels, _ = environment
    os.environ.update(GITHUB_EVENT_NAME=event_name, GITHUB_SHA=BASE)
    event.write_text(json.dumps({"pull_request": {"head": {"sha": BASE}}}))
    inputs = (
        {"operation": "validate-config", "config-sha": BASE}
        if event_name == "pull_request"
        else None
    )
    action = Composite(directory, inputs)
    assert action.run() == 0
    assert action.executed == ["run"]
    assert not checks.writes and not labels.calls and not action.artifacts


@pytest.mark.parametrize(
    "inputs", [{}, {"pr-number": "1"}, {"pr-number": "1", "update-labels": True}]
)
def test_manual_inputs_control_labels_inside_the_action(environment, inputs):
    directory, event, _, checks, labels, _ = environment
    event.write_text(json.dumps({"inputs": inputs}))
    action = Composite(directory)
    invalid = bool(inputs.get("update-labels"))
    assert action.run() == int(invalid)
    assert bool(checks.writes) is (not invalid)
    assert not labels.calls


def test_no_open_prs_is_success_and_saves_an_empty_manifest(environment):
    directory, _, reader, checks, labels, _ = environment
    reader.pages = lambda *args, **kwargs: []
    action = Composite(directory)
    assert action.run() == 0
    assert not checks.writes and not labels.names()
    assert json.loads(action.artifacts["pr-merge-readiness-42-2"]["manifest.json"])["reports"] == []


@pytest.mark.parametrize(
    "action_name",
    ["opened", "reopened", "synchronize", "ready_for_review", "converted_to_draft", "closed"],
)
def test_pr_events_observe_and_publish_without_waiting_for_ci(environment, action_name):
    directory, event, _, checks, labels, _ = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    event.write_text(json.dumps({"action": action_name, "pull_request": checks.prs[1]}))
    action = Composite(directory)
    assert action.run() == 0
    assert action.executed == ["run", "execute", "observations", "checks"]
    assert checks.writes and not labels.calls and action.artifacts
    assert "SHADOW_CONDITIONS_MET" in checks.writes[0][1]["output"]["title"]


@pytest.mark.parametrize("excluded", ["fork", "dependabot", "deleted-source"])
def test_excluded_pr_event_finishes_without_config_reports_or_writes(environment, excluded):
    directory, event, _, checks, labels, settings = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    data = pr_event()
    if excluded == "dependabot":
        data["pull_request"]["user"]["login"] = "dependabot[bot]"
    else:
        data["pull_request"]["head"]["repo"] = {"id": 2} if excluded == "fork" else None
    event.write_text(json.dumps(data))
    action = Composite(directory)
    assert action.run() == 0
    assert action.executed == ["run"]
    assert not settings["requests"] and not action.artifacts
    assert not checks.writes and not labels.calls


def test_pr_validates_proposal_but_observes_with_default_branch_policy(environment):
    directory, event, _, checks, _, settings = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    event.write_text(json.dumps(pr_event()))
    settings["proposal"] = settings["proposal"].replace(
        "minimum_approvals = 0", "minimum_approvals = 99"
    )
    settings["proposal"] = settings["proposal"].replace("checks = true", "checks = false")
    action = Composite(directory)
    assert action.run() == 0
    report = json.loads(action.artifacts["pr-merge-readiness-42-2"]["pr-1.json"])
    assert report["policy"]["minimum_approvals"] == 0
    assert checks.writes
    assert settings["requests"][0].endswith(f"?ref={HEAD}")
    assert action.context["steps.run.outputs.config-sha"] == BASE


def test_invalid_pr_proposal_saves_diagnostics_without_observing_or_publishing(environment):
    directory, event, _, checks, labels, settings = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    event.write_text(json.dumps(pr_event()))
    settings["proposal"] = "unknown = true\n" + settings["proposal"]
    action = Composite(directory)
    assert action.run() == 1
    assert action.executed == ["run", "preparation"]
    assert "pr-merge-readiness-preparation-42-2" in action.artifacts
    assert not checks.writes and not labels.calls
    assert len(settings["requests"]) == 1


@pytest.mark.parametrize(
    "state,expected",
    [({"isDraft": True}, "WAITING"), ({"mergeable": "CONFLICTING"}, "HUMAN_REVIEW_REQUIRED")],
)
def test_manual_labels_only_reflect_reviews_while_check_keeps_pr_state(
    environment, state, expected
):
    directory, _, reader, checks, labels, _ = environment
    reader.state.update(state)
    checks.prs[1]["draft"] = labels.pulls[1]["draft"] = reader.state["isDraft"]
    action = Composite(directory)
    assert action.run() == 0
    saved = action.artifacts["pr-merge-readiness-42-2"]
    report = json.loads(saved["pr-1.json"])
    assert report["decision"] == expected
    assert report["label_assessment"]["decision"] == "SHADOW_CONDITIONS_MET"
    assert expected in checks.writes[-1][1]["output"]["title"]
    assert labels.names() == [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]


def test_pr_event_waits_for_mergeability_before_publishing_check(environment):
    directory, event, reader, checks, labels, _ = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    event.write_text(json.dumps(pr_event()))
    reader.state["mergeable"] = "UNKNOWN"
    reader.drift = {"mergeable": "MERGEABLE"}
    with patch("pr_merge_readiness.collect.time.sleep") as sleep:
        action = Composite(directory)
        assert action.run() == 0
    sleep.assert_called_once()
    report = json.loads(action.artifacts["pr-merge-readiness-42-2"]["pr-1.json"])
    assert report["decision"] == "SHADOW_CONDITIONS_MET"
    assert report["observations"]["pr"]["mergeable"] == "MERGEABLE"
    assert "SHADOW_CONDITIONS_MET" in checks.writes[-1][1]["output"]["title"]
    assert not labels.calls
