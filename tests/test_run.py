"""単一のAction実行で、設定検証・観測・ラベル更新を検証する。"""

import base64
import json
import os
from copy import deepcopy
from unittest.mock import patch

import pytest

from pr_merge_readiness import runtime
from pr_merge_readiness.cli import main
from pr_merge_readiness.collect import CollectionError
from pr_merge_readiness.config import ACTION_REPOSITORY
from pr_merge_readiness.publish import DECISION_LABELS
from tests.test_collect import FixtureAPI as Reader
from tests.test_config import config_text
from tests.test_publish import FixtureAPI as Writer
from tests.test_support import ACTION_SHA, BASE, HEAD, pr_event


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reader, writer = Reader(), Writer()
    writer.pulls[1]["base"]["ref"] = reader.state["baseRefName"]
    event = tmp_path / "event.json"
    event.write_text('{"inputs":{"pr-number":"1"}}')
    settings = {"text": config_text(), "proposal": config_text(), "requests": []}
    request = reader.request
    pages = reader.pages

    def read(path, body=None):
        if "/commits?" in path:
            return request(path, body)
        settings["requests"].append(path)
        if path == reader.prefix:
            return {"default_branch": "trunk"}
        if path == reader.prefix + "/git/ref/heads/trunk":
            return {"object": {"sha": BASE}}
        assert path.startswith(reader.prefix + "/contents/")
        text = settings["proposal"] if path.endswith(f"?ref={HEAD}") else settings["text"]
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(text.encode()).decode(),
        }

    def listing(path, key=None):
        if path == "/pulls?state=open":
            return [{"number": 1}]
        return pages(path, key)

    monkeypatch.setattr(reader, "request", read)
    monkeypatch.setattr(reader, "pages", listing)
    monkeypatch.setattr("sys.argv", ["cli.py", "action"])
    env = {
        "GH_TOKEN": "test-token",
        "GITHUB_REPOSITORY": reader.repository,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_SHA": HEAD,
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "PMR_SOURCE_REF": ACTION_SHA,
        "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(runtime, "GitHub", return_value=reader),
        patch.object(runtime.publish, "GitHub", return_value=writer),
    ):
        yield reader, writer, event, settings, tmp_path


def test_manual_single_pr_updates_labels_and_summary_in_one_call(context):
    reader, writer, _, settings, root = context
    assert main() == 0
    assert writer.names() == [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]
    assert "SHADOW_CONDITIONS_MET" in (root / "summary").read_text()
    assert (root / "outputs").read_text() == f"operation=observe\nconfig-sha={BASE}\n"
    assert len(settings["requests"]) == 3
    assert {path.name for path in root.iterdir()} == {"event.json", "outputs", "summary"}
    assert all("state=closed" not in path for _, path, _ in writer.calls)


@pytest.mark.parametrize("event_name", ["pull_request", "workflow_dispatch"])
def test_updated_at_only_change_does_not_require_rerunning_action(context, event_name):
    reader, writer, event, _, root = context
    os.environ["GITHUB_EVENT_NAME"] = event_name
    if event_name == "pull_request":
        event.write_text(json.dumps(pr_event()))
    reader.drift = {"updatedAt": "2026-09-11T12:01:00Z"}
    assert main() == 0
    assert writer.names() == [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]
    summary = (root / "summary").read_text()
    assert "INSUFFICIENT_DATA" not in summary
    assert "updated_at" in summary


@pytest.mark.parametrize("inputs", [{}, None, {"pr-number": ""}])
def test_all_open_sync_cleans_only_managed_labels_on_closed_prs(context, inputs):
    _, writer, event, _, _ = context
    event.write_text(json.dumps({"inputs": inputs}))
    writer.pulls[2] = deepcopy(writer.pulls[1])
    writer.pulls[2].update(
        state="closed", labels=[{"name": "bug"}, {"name": DECISION_LABELS["HUMAN_REVIEW_REQUIRED"]}]
    )
    writer.closed = [{"number": 2, "pull_request": {}}]
    assert main() == 0
    assert writer.names(2) == ["bug"]
    assert writer.names() == [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]


@pytest.mark.parametrize(
    "action",
    [
        "opened",
        "reopened",
        "synchronize",
        "edited",
        "ready_for_review",
        "converted_to_draft",
        "closed",
    ],
)
def test_pr_events_validate_proposal_but_use_default_policy(context, action):
    reader, writer, event, settings, root = context
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    data = pr_event(action)
    if action == "edited":
        data["changes"] = {"base": {}}
    if action == "closed":
        writer.pulls[1].update(
            state="closed", labels=[{"name": DECISION_LABELS["HUMAN_REVIEW_REQUIRED"]}]
        )
    event.write_text(json.dumps(data))
    settings["proposal"] = settings["proposal"].replace(
        "stale_change_review_days = 30", "stale_change_review_days = 1"
    )
    assert main() == 0
    assert writer.names() == (
        [] if action == "closed" else [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]
    )
    assert settings["requests"][0].endswith(f"?ref={HEAD}")
    assert settings["requests"].count("/repos/example/project/git/ref/heads/trunk") == 1
    assert all("state=closed" not in path for _, path, _ in writer.calls)
    assert BASE in (root / "summary").read_text()
    assert not any(path.endswith("/reviews") for path in reader.paths)


@pytest.mark.parametrize(
    "reason", ["title", "body", "fork", "dependabot", "deleted-source", "unhandled"]
)
def test_excluded_pr_events_do_not_read_config_or_write(context, reason):
    reader, writer, event, settings, root = context
    data = pr_event()
    if reason in {"title", "body"}:
        data.update(action="edited", changes={reason: {}})
    elif reason == "fork":
        data["pull_request"]["head"]["repo"]["id"] = 2
    elif reason == "deleted-source":
        data["pull_request"]["head"]["repo"] = None
    elif reason == "dependabot":
        data["pull_request"]["user"]["login"] = "dependabot[bot]"
    else:
        data["action"] = "labeled"
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    event.write_text(json.dumps(data))
    assert main() == 0
    assert not settings["requests"] and not reader.paths and not writer.calls
    assert (root / "outputs").read_text() == "operation=skip\n"


@pytest.mark.parametrize("name", ["schedule", "workflow_run", "pull_request_review"])
def test_other_events_skip_without_reading_event_or_api(context, name):
    reader, writer, event, settings, _ = context
    event.unlink()
    os.environ["GITHUB_EVENT_NAME"] = name
    assert main() == 0
    assert not settings["requests"] and not reader.paths and not writer.calls


def test_push_only_validates_exact_config_commit(context):
    reader, writer, event, settings, root = context
    os.environ["GITHUB_EVENT_NAME"] = "push"
    event.unlink()
    assert main() == 0
    assert settings["requests"] == [
        f"/repos/example/project/contents/.github/pr-merge-readiness.toml?ref={HEAD}"
    ]
    assert not reader.paths and not writer.calls
    assert (root / "outputs").read_text() == f"operation=validate-config\nconfig-sha={HEAD}\n"


@pytest.mark.parametrize("event_name", ["pull_request", "push"])
def test_invalid_proposal_never_observes_or_uses_default_config(context, event_name):
    reader, writer, event, settings, root = context
    event.write_text(json.dumps(pr_event()))
    settings["proposal"] = "unknown = true\n" + settings["proposal"]
    os.environ["GITHUB_EVENT_NAME"] = event_name
    assert main() == 1
    assert len(settings["requests"]) == 1
    assert not reader.paths and not writer.calls
    assert "invalid keys" in (root / "summary").read_text()


@pytest.mark.parametrize(
    "inputs",
    [
        {"pr-number": "01"},
        {"pr-number": "-1"},
        {"pr-number": 1},
        {"pr-number": False},
        {"pr-number": None},
        {"update-labels": True},
        {"update-labels": False},
        [],
        "wrong",
    ],
)
def test_invalid_or_removed_manual_inputs_fail_before_api(context, inputs):
    reader, writer, event, settings, _ = context
    event.write_text(json.dumps({"inputs": inputs}))
    assert main() == 1
    assert not settings["requests"] and not reader.paths and not writer.calls


@pytest.mark.parametrize("timing", ["queued", "during", "after"])
def test_additional_push_never_labels_an_unvalidated_head(context, monkeypatch, timing):
    reader, writer, event, _, root = context
    event.write_text(json.dumps(pr_event()))
    os.environ["GITHUB_EVENT_NAME"] = "pull_request"
    writer.pulls[1]["labels"] = [{"name": DECISION_LABELS["HUMAN_REVIEW_REQUIRED"]}]
    writer.pulls[1]["head"]["sha"] = "e" * 40
    if timing == "queued":
        reader.state["headRefOid"] = "e" * 40
    elif timing == "during":
        reader.drift = {"headRefOid": "e" * 40}
    assert main() == (0 if timing == "after" else 1)
    assert writer.names() == [DECISION_LABELS["HUMAN_REVIEW_REQUIRED"]]
    assert all(verb == "GET" for verb, _, _ in writer.calls)
    if timing != "after":
        assert "head" in (root / "summary").read_text()
    if timing == "queued":
        assert not reader.paths


def test_collection_errors_replace_ready_label_with_unknown_and_fail(context):
    reader, writer, _, _, root = context
    reader.failure = "API 403"
    writer.pulls[1]["labels"] = [{"name": DECISION_LABELS["SHADOW_CONDITIONS_MET"]}]
    assert main() == 1
    assert writer.names() == [DECISION_LABELS["INSUFFICIENT_DATA"]]
    assert "API 403" in (root / "summary").read_text()


def test_global_listing_failure_does_not_publish_and_escapes_summary(context, monkeypatch):
    reader, writer, event, _, root = context
    event.write_text("{}")
    monkeypatch.setattr(
        reader, "pages", lambda *args: (_ for _ in ()).throw(CollectionError("<bad>"))
    )
    assert main() == 1
    assert not writer.calls
    assert "&lt;bad&gt;" in (root / "summary").read_text()


def test_no_open_prs_still_cleans_closed_labels(context, monkeypatch):
    reader, writer, event, _, root = context
    event.write_text("{}")
    monkeypatch.setattr(reader, "pages", lambda *args: [])
    writer.pulls[1].update(
        state="closed", labels=[{"name": DECISION_LABELS["HUMAN_REVIEW_REQUIRED"]}]
    )
    writer.closed = [{"number": 1, "pull_request": {}}]
    assert main() == 0
    assert writer.names() == []
    assert "評価対象の open PR はありません" in (root / "summary").read_text()


def test_label_failure_continues_with_later_prs(context, monkeypatch):
    reader, writer, event, _, root = context
    event.write_text("{}")
    pages, graphql = reader.pages, reader.graphql
    monkeypatch.setattr(
        reader,
        "pages",
        lambda path, key=None: (
            [{"number": 1}, {"number": 2}] if path == "/pulls?state=open" else pages(path, key)
        ),
    )
    monkeypatch.setattr(
        reader, "graphql", lambda number, fields: {**graphql(number, fields), "number": number}
    )
    writer.pulls[2] = deepcopy(writer.pulls[1])
    writer.fail = lambda verb, path: verb == "POST" and "/issues/1/" in path
    assert main() == 1
    assert writer.names(2) == [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]
    assert "PR #1" in (root / "summary").read_text()


def test_all_observations_finish_before_first_label_write(context, monkeypatch):
    reader, writer, event, _, _ = context
    event.write_text("{}")
    pages, graphql = reader.pages, reader.graphql
    monkeypatch.setattr(
        reader,
        "pages",
        lambda path, key=None: (
            [{"number": 1}, {"number": 2}] if path == "/pulls?state=open" else pages(path, key)
        ),
    )

    def read(number, fields):
        assert not writer.calls
        return {**graphql(number, fields), "number": number}

    monkeypatch.setattr(reader, "graphql", read)
    writer.pulls[2] = deepcopy(writer.pulls[1])
    assert main() == 0
    assert writer.names(2) == [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]


def test_summary_write_failure_fails_cleanly_before_publication(context):
    _, writer, _, _, root = context
    os.environ["GITHUB_STEP_SUMMARY"] = str(root / "missing" / "summary")
    assert main() == 1
    assert not writer.calls


def test_missing_token_never_reads_config_or_publishes(context):
    _, writer, _, settings, _ = context
    del os.environ["GH_TOKEN"]
    assert main() == 1
    assert not settings["requests"] and not writer.calls
