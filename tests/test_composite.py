"""実際の action.yml の条件・環境変数を使い、API と artifact 転送だけを代替する。"""

import base64
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from pr_merge_readiness import runtime
from pr_merge_readiness.cli import main
from pr_merge_readiness.collect import CollectionError
from pr_merge_readiness.config import ACTION_REPOSITORY
from tests.test_collect import FixtureAPI as Reader
from tests.test_config import ROOT, config_text
from tests.test_publish import FixtureAPI as LabelWriter
from pr_merge_readiness.publish import DECISION_LABELS
from tests.test_publish_checks import FixtureAPI as CheckWriter
from tests.test_support import ACTION_SHA, BASE, HEAD, pr_event


class Composite:
    """この Action が使用する式だけを評価する、テスト専用の step 実行器。"""

    def __init__(self, directory, inputs=None, fail_upload=False, cancel_before=None, tamper=False):
        self.action = yaml.load((ROOT / "action.yml").read_text(), Loader=yaml.BaseLoader)
        self.directory = directory
        self.context = {
            "github.action_path": str(ROOT),
            "github.action_ref": ACTION_SHA,
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
                continue
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


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("checks_enabled", [True, False])
def test_single_call_observes_saves_and_publishes_with_one_config_sha(
    environment, checks_enabled, mode
):
    directory, _, _, checks, labels, settings = environment
    settings["text"] = settings["text"].replace('labels = "manual"', f'labels = "{mode}"')
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
@pytest.mark.parametrize("event_name", ["workflow_dispatch", "pull_request"])
def test_failure_boundaries_preserve_evidence_and_suppress_publication(
    environment, monkeypatch, failure, event_name
):
    directory, event, reader, checks, labels, settings = environment
    if event_name == "pull_request":
        os.environ["GITHUB_EVENT_NAME"] = event_name
        event.write_text(json.dumps(pr_event()))
        settings["text"] = settings["text"].replace('labels = "manual"', 'labels = "auto"')
    if failure == "prepare":
        settings["text"] = "invalid TOML"
    elif failure == "collection":
        if event_name == "workflow_dispatch":
            reader.pages = lambda *args, **kwargs: (_ for _ in ()).throw(CollectionError("API 403"))
        else:
            monkeypatch.setattr(
                runtime,
                "observe",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    CollectionError("observation failed")
                ),
            )
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
@pytest.mark.parametrize("mode", ["auto", "manual", "off"])
def test_manual_inputs_control_labels_inside_the_action(environment, inputs, mode):
    directory, event, _, checks, labels, settings = environment
    settings["text"] = settings["text"].replace('labels = "manual"', f'labels = "{mode}"')
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
    ["opened", "reopened", "synchronize", "edited", "ready_for_review", "converted_to_draft"],
)
@pytest.mark.parametrize("mode", ["auto", "manual", "off"])
@pytest.mark.parametrize("checks_enabled", [True, False])
def test_pr_events_observe_and_publish_without_waiting_for_ci(
    environment, action_name, mode, checks_enabled
):
    directory, event, _, checks, labels, settings = environment
    settings["text"] = settings["text"].replace('labels = "manual"', f'labels = "{mode}"')
    if not checks_enabled:
        settings["text"] = settings["text"].replace("checks = true", "checks = false")
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    data = {"action": action_name, "pull_request": checks.prs[1]}
    if action_name == "edited":
        data["changes"] = {"base": {}}
    event.write_text(json.dumps(data))
    # 他のPRと一般ラベルには触れず、対象PRの古い管理ラベルだけを置き換える。
    labels.pulls[1]["labels"] = [{"name": "enhancement"}, {"name": DECISION_LABELS["WAITING"]}]
    labels.pulls[2] = {**deepcopy(labels.pulls[1]), "state": "closed"}
    labels.closed = [{"number": 2, "pull_request": {}}]
    action = Composite(directory)
    assert action.run() == 0
    assert action.executed == [
        "run",
        "execute",
        "observations",
        *(["checks"] if checks_enabled else []),
        *(["labels"] if mode == "auto" else []),
    ]
    assert bool(checks.writes) is checks_enabled
    assert bool(labels.calls) is (mode == "auto")
    assert labels.names() == [
        "enhancement",
        DECISION_LABELS["SHADOW_CONDITIONS_MET" if mode == "auto" else "WAITING"],
    ]
    assert labels.names(2) == ["enhancement", DECISION_LABELS["WAITING"]]
    assert not any("state=closed" in path for _, path, _ in labels.calls)
    saved = action.artifacts["pr-merge-readiness-42-2"]
    assert json.loads(saved["manifest.json"])["selection"] == "single_pr"
    if checks_enabled:
        assert "SHADOW_CONDITIONS_MET" in checks.writes[0][1]["output"]["title"]


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_closed_pr_event_removes_only_its_managed_labels(environment, state):
    directory, event, reader, checks, labels, settings = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    settings["text"] = settings["text"].replace('labels = "manual"', 'labels = "auto"')
    reader.state["state"] = state
    checks.prs[1]["state"] = labels.pulls[1]["state"] = "closed"
    if state == "MERGED":
        checks.prs[1]["merged_at"] = reader.state["updatedAt"]
    labels.pulls[1]["labels"] = [
        {"name": "enhancement"},
        {"name": DECISION_LABELS["SHADOW_CONDITIONS_MET"]},
    ]
    event.write_text(json.dumps({"action": "closed", "pull_request": checks.prs[1]}))
    action = Composite(directory)
    assert action.run() == 0
    assert checks.writes
    assert labels.names() == ["enhancement"]
    assert not any("state=closed" in path for _, path, _ in labels.calls)


@pytest.mark.parametrize(
    "mutation",
    [
        "all-open",
        "other-pr",
        "extra-pr",
        "empty",
        "other-head",
        "manual",
        "off",
        "fork",
        "dependabot",
    ],
)
def test_explicit_auto_labels_reject_unrelated_reports_or_events_before_writing(
    environment, mutation
):
    directory, event, _, _, labels, settings = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    data = pr_event()
    event.write_text(json.dumps(data))
    settings["text"] = settings["text"].replace('labels = "manual"', 'labels = "auto"')
    assert Composite(directory, cancel_before="labels").run() == 1
    report_dir = directory / "pr-merge-readiness-42-2"
    manifest = json.loads((report_dir / "manifest.json").read_text())
    report = json.loads((report_dir / "pr-1.json").read_text())
    if mutation == "all-open":
        manifest["selection"] = "all_open"
    elif mutation == "other-pr":
        data["pull_request"]["number"] = 2
    elif mutation == "extra-pr":
        second = deepcopy(report)
        second["observations"]["pr"]["number"] = 2
        (report_dir / "pr-2.json").write_text(json.dumps(second))
        manifest["reports"].append("pr-2.json")
    elif mutation == "empty":
        manifest["reports"] = []
    elif mutation == "other-head":
        report["observations"]["pr"]["head_sha"] = "d" * 40
    elif mutation in {"manual", "off"}:
        settings["text"] = settings["text"].replace('labels = "auto"', f'labels = "{mutation}"')
    elif mutation == "fork":
        data["pull_request"]["head"]["repo"]["id"] = 2
    else:
        data["pull_request"]["user"]["login"] = "dependabot[bot]"
    event.write_text(json.dumps(data))
    (report_dir / "manifest.json").write_text(json.dumps(manifest))
    (report_dir / "pr-1.json").write_text(json.dumps(report))
    action = Composite(
        directory,
        {
            "operation": "publish-labels",
            "config-sha": BASE,
            "report-dir": str(report_dir),
            "artifact-name": "pr-merge-readiness-42-2",
        },
    )
    assert action.run() == 1
    assert not labels.calls


@pytest.mark.parametrize("change", ["title", "body"])
def test_auto_labels_ignore_title_and_body_edits(environment, change):
    directory, event, _, checks, labels, settings = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    settings["text"] = settings["text"].replace('labels = "manual"', 'labels = "auto"')
    event.write_text(json.dumps({**pr_event("edited"), "changes": {change: {}}}))
    action = Composite(directory)
    assert action.run() == 0
    assert not settings["requests"] and not checks.writes and not labels.calls


@pytest.mark.parametrize("excluded", ["fork", "dependabot", "deleted-source"])
def test_excluded_pr_event_finishes_without_config_reports_or_writes(environment, excluded):
    directory, event, _, checks, labels, settings = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    settings["text"] = settings["text"].replace('labels = "manual"', 'labels = "auto"')
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


@pytest.mark.parametrize("trusted_mode,proposal_mode", [("auto", "off"), ("manual", "auto")])
@pytest.mark.parametrize("legacy_config", ["both", "default-only", "proposal-only", "neither"])
def test_action_upgrade_uses_default_policy_without_matching_config_action_refs(
    environment, trusted_mode, proposal_mode, legacy_config
):
    directory, event, _, checks, labels, settings = environment
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    event.write_text(json.dumps(pr_event()))
    for key, legacy_ref, keep in (
        ("text", "e" * 40, legacy_config in {"both", "default-only"}),
        ("proposal", "f" * 40, legacy_config in {"both", "proposal-only"}),
    ):
        settings[key] = re.sub(r"^action_ref = .*\n", "", settings[key], flags=re.MULTILINE)
        if keep:
            settings[key] = f'action_ref = "{legacy_ref}"\n' + settings[key]
    settings["proposal"] = settings["proposal"].replace(
        "minimum_approvals = 0", "minimum_approvals = 99"
    )
    settings["proposal"] = settings["proposal"].replace("checks = true", "checks = false")
    settings["proposal"] = settings["proposal"].replace(
        'labels = "manual"', f'labels = "{proposal_mode}"'
    )
    settings["text"] = settings["text"].replace('labels = "manual"', f'labels = "{trusted_mode}"')
    action = Composite(directory)
    assert action.run() == 0
    report = json.loads(action.artifacts["pr-merge-readiness-42-2"]["pr-1.json"])
    assert report["policy"]["minimum_approvals"] == 0
    assert report["provenance"]["evaluator"]["sha"] == ACTION_SHA
    assert report["provenance"]["config"]["sha"] == BASE
    manifest = json.loads(action.artifacts["pr-merge-readiness-42-2"]["manifest.json"])
    assert manifest["provenance"] == report["provenance"]
    assert checks.writes
    assert bool(labels.names()) is (trusted_mode == "auto")
    assert settings["requests"][0].endswith(f"?ref={HEAD}")
    assert action.context["steps.run.outputs.config-sha"] == BASE
    assert settings["requests"].count("/repos/example/project/git/ref/heads/trunk") == 1


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


@pytest.mark.parametrize("timing", ["queued", "during-observation", "after-observation"])
@pytest.mark.parametrize("checks_enabled", [True, False])
def test_pr_head_change_never_publishes_with_only_previous_proposal_validated(
    environment, monkeypatch, timing, checks_enabled
):
    directory, event, reader, checks, labels, settings = environment
    settings["text"] = settings["text"].replace('labels = "manual"', 'labels = "auto"')
    if not checks_enabled:
        settings["text"] = settings["text"].replace("checks = true", "checks = false")
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    event.write_text(json.dumps(pr_event()))
    new_head = "d" * 40
    labels.pulls[1]["labels"] = [{"name": DECISION_LABELS["WAITING"]}]
    labels.pulls[1]["head"]["sha"] = new_head
    # イベントのheadは有効な設定を持つ。追加pushのheadには不正な設定がある。
    request = reader.request

    def read(path, body=None):
        if path.endswith(f"?ref={new_head}"):
            return {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(b"unknown = true").decode(),
            }
        return request(path, body)

    monkeypatch.setattr(reader, "request", read)
    if timing == "queued":
        reader.state["headRefOid"] = new_head
        checks.prs[1]["head"]["sha"] = new_head
    elif timing == "during-observation":
        reader.drift = {"headRefOid": new_head}
        checks.prs[1]["head"]["sha"] = new_head
    else:
        summary = runtime.summary

        def after_observation(path):
            summary(path)
            checks.prs[1]["head"]["sha"] = new_head

        monkeypatch.setattr(runtime, "summary", after_observation)
    action = Composite(directory)
    assert action.run() == (0 if timing == "after-observation" else 1)
    assert not checks.writes
    assert labels.names() == [DECISION_LABELS["WAITING"]]
    assert all(verb == "GET" for verb, _, _ in labels.calls)
    saved = action.artifacts["pr-merge-readiness-42-2"]
    manifest = json.loads(saved["manifest.json"])
    if timing == "after-observation":
        assert not manifest["collection_failed"]
        assert json.loads(saved["pr-1.json"])["observations"]["pr"]["head_sha"] == HEAD
    else:
        assert manifest["collection_failed"]
        assert not manifest["reports"]
        assert "head" in json.loads(saved["collection-error.json"])["error"]
    if timing == "queued":
        assert not reader.paths  # headの一致を確認する前に変更内容やレビューを収集しない。


def test_explicit_check_publication_in_pr_workflow_can_publish_multiple_prs(environment):
    directory, event, reader, checks, _, _ = environment
    event.write_text('{"inputs":{}}')
    assert Composite(directory).run() == 0
    report_dir = directory / "pr-merge-readiness-42-2"
    second = json.loads((report_dir / "pr-1.json").read_text())
    other_head = "d" * 40
    second["observations"]["pr"].update(number=2, head_sha=other_head)
    (report_dir / "pr-2.json").write_text(json.dumps(second))
    manifest = json.loads((report_dir / "manifest.json").read_text())
    manifest["reports"].append("pr-2.json")
    (report_dir / "manifest.json").write_text(json.dumps(manifest))
    checks.prs[2]["head"]["sha"] = other_head
    checks.prs[2]["base"]["ref"] = reader.state["baseRefName"]
    checks.checks.clear()
    checks.writes.clear()
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    os.environ["PMR_VALIDATED_HEAD"] = HEAD  # 個別Action呼出しには内部stepの固定値を持ち込まない。
    event.write_text(json.dumps(pr_event()))
    action = Composite(
        directory,
        {
            "operation": "publish-checks",
            "config-sha": BASE,
            "report-dir": str(report_dir),
            "artifact-name": "pr-merge-readiness-42-2",
        },
    )
    assert action.run() == 0
    assert {body["head_sha"] for _, body, _ in checks.writes} == {HEAD, other_head}


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
