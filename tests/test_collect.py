"""APIのページング・鮮度と収集失敗を、外部への書き込みなしで検証する。"""

import pytest
import io
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import HTTPError
from pr_merge_readiness.collect import (
    CollectionError,
    GitHub,
    collect,
    targets,
)
from pr_merge_readiness.evaluate import assess
from pr_merge_readiness.report import markdown
from tests.test_support import BASE, HEAD, MERGE, policy


class FixtureAPI:
    repository = "example/project"
    prefix = "/repos/example/project"

    def __init__(self):
        self.state = {
            "number": 1,
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": HEAD,
            "baseRefOid": BASE,
            "baseRefName": "trunk",
            "updatedAt": "2026-09-11T12:00:00Z",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": None,
            "potentialMergeCommit": {"oid": MERGE},
            "additions": 3,
            "deletions": 1,
            "changedFiles": 1,
        }
        self.reads = 0
        self.drift = {}
        self.failure = None
        self.paths = []
        self.files = [
            {
                "filename": "anything.go",
                "status": "modified",
                "additions": 3,
                "deletions": 1,
                "patch": "ignored source diff",
            }
        ]

    def request(self, path, body=None):
        return [
            {
                "sha": BASE,
                "commit": {
                    "committer": {
                        "date": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
                    }
                },
            }
        ]

    def graphql(self, number, selection):
        self.reads += 1
        if self.reads == 2:
            self.state.update(self.drift)
        return deepcopy(self.state)

    def pages(self, path, key=None):
        if self.failure:
            raise CollectionError(self.failure)
        self.paths.append(path)
        if path.endswith("/files"):
            return deepcopy(self.files)
        if path.endswith("/reviews"):
            return [
                {
                    "id": 1,
                    "user": {"login": "reviewer", "type": "User"},
                    "author_association": "OWNER",
                    "state": "APPROVED",
                    "commit_id": HEAD,
                    "submitted_at": "2026-09-11T12:00:00Z",
                }
            ]
        if "/check-runs" in path or "/statuses" in path:
            raise AssertionError("CI state must not be collected")
        return []


@pytest.mark.parametrize(
    "change",
    [
        {"filename": ".github/workflows/ci.yml", "status": "modified"},
        {"filename": ".github/workflows/spoof.yaml", "status": "added"},
        {"filename": ".github/workflows/ci.yml", "status": "removed"},
        {
            "filename": "archived/ci.yml",
            "status": "renamed",
            "previous_filename": ".github/workflows/ci.yml",
        },
        {
            "filename": ".github/workflows/spoof.yml",
            "status": "renamed",
            "previous_filename": "example.yml",
        },
    ],
)
def test_workflow_paths_do_not_create_additional_requirements(change):
    api = FixtureAPI()
    api.files[0].update(change)
    result = collect(api, 1, policy=policy())
    assert not result["collection_errors"]
    assert "ci_definition_changes" not in result
    assert assess(result, policy())["decision"] == "SHADOW_CONDITIONS_MET"


def test_rename_requires_previous_path_and_preserves_ordinary_changes():
    api = FixtureAPI()
    api.files[0].update(status="renamed", previous_filename="before.rs")
    result = collect(api, 1, policy=policy())
    assert result["files"][0]["previous_path"] == "before.rs"
    assert "ci_definition_changes" not in result
    assert "patch" not in result["files"][0]
    assert assess(result, policy())["decision"] == "SHADOW_CONDITIONS_MET"
    del api.files[0]["previous_filename"]
    assert assess(collect(api, 1, policy=policy()), policy())["decision"] == "INSUFFICIENT_DATA"


def test_metadata_order_changes_do_not_invalidate_observation():
    api = FixtureAPI()
    original = api.pages

    def reordered(path, key=None):
        records = original(path, key)
        return list(reversed(records)) if api.paths.count(path) == 2 else records

    api.pages = reordered
    assert (
        assess(
            collect(api, 1, policy={"stale_change_review_days": 1}), {"stale_change_review_days": 1}
        )["decision"]
        == "SHADOW_CONDITIONS_MET"
    )


def test_removed_records_and_summary_escape_only_normalized_metadata():
    api = FixtureAPI()
    original = api.pages

    def changing(path, key=None):
        records = original(path, key)
        if path.endswith("/reviews"):
            records[0]["body"] = "SECRET REVIEW BODY"
            if api.paths.count(path) == 2:
                return []
        return records

    api.pages = changing
    api.drift = {"baseRefName": "<script>alert(1)</script>"}
    result = assess(
        collect(api, 1, policy={"stale_change_review_days": 1}), {"stale_change_review_days": 1}
    )
    changes = result["observations"]["observation_changes"]
    assert changes[0]["field"] == "base_ref"
    assert changes[0]["before"] == "trunk"
    assert changes[1]["field"] == "record"
    assert changes[1]["after"] is None
    assert changes[1]["before"]["id"] == 1
    encoded = json.dumps(result)
    summary = markdown(result)
    assert "SECRET REVIEW BODY" not in encoded
    assert "ignored source diff" not in encoded
    assert "<script>" not in summary
    assert "&lt;script&gt;" in summary
    assert "base_ref" in summary
    assert "reviews" in summary


def test_http_transport_sends_json_and_rejects_graphql_errors():
    api = GitHub("example/project")
    with patch(
        "pr_merge_readiness.collect.urlopen", return_value=io.BytesIO(b'{"data": {}}')
    ) as request:
        assert api.request("/graphql", {"query": "query { viewer { login } }"}) == {"data": {}}
        sent = request.call_args.args[0]
        assert sent.get_header("Content-type") == "application/json"
        assert "query" in json.loads(sent.data)
    with patch(
        "pr_merge_readiness.collect.urlopen",
        return_value=io.BytesIO(b'{"errors": [{"message": "forbidden"}], "data": {}}'),
    ):
        with pytest.raises(CollectionError):
            api.request("/graphql", {"query": "query { viewer { login } }"})


def test_http_failure_or_oversized_response_is_not_empty_success():
    api = GitHub("example/project")
    with patch(
        "pr_merge_readiness.collect.urlopen",
        side_effect=HTTPError("https://api.github.com/graphql", 403, "forbidden", {}, None),
    ):
        with pytest.raises(CollectionError, match="HTTP 403"):
            api.request("/graphql", {})
    with (
        patch("pr_merge_readiness.collect.MAX_RESPONSE_BYTES", 4),
        patch("pr_merge_readiness.collect.urlopen", return_value=io.BytesIO(b'{"long": 1}')),
    ):
        with pytest.raises(CollectionError, match="exceeds limit"):
            api.request("/graphql", {})


def test_declared_totals_must_match_collected_records():
    api = GitHub("example/project")
    with patch.object(api, "request", return_value={"total_count": 3, "check_runs": [{}]}):
        with pytest.raises(CollectionError, match="count mismatch"):
            api.pages("/commits/sha/check-runs", "check_runs")
    api = FixtureAPI()
    api.state["changedFiles"] = 2
    assert "totals mismatch" in collect(api, 1, policy=policy())["collection_errors"][0]


def test_collects_only_metadata_and_preserves_observations():
    api = FixtureAPI()
    facts = collect(api, 1, policy=policy())
    assert facts["stable"]
    assert facts["pr"]["base_ref"] == "trunk"
    assert facts["change"]["additions"] == 3
    assert facts["change"]["binary_files"] is None
    assert facts["change"]["mode_changes"] is None
    assert assess(facts, policy())["decision"] == "SHADOW_CONDITIONS_MET"
    assert not any(("/contents/" in p or "/git/blobs/" in p for p in api.paths))


@pytest.mark.parametrize(
    "drift",
    [
        {"headRefOid": "d" * 40},
        {"baseRefOid": "d" * 40},
    ],
)
def test_head_or_base_changes_invalidate_collection(drift):
    api = FixtureAPI()
    api.drift = {"updatedAt": "2026-09-11T13:00:00Z", **drift}
    assert assess(collect(api, 1, policy=policy()), policy())["decision"] == "INSUFFICIENT_DATA"


def test_updated_at_only_change_is_recorded_without_invalidating_observation():
    api = FixtureAPI()
    api.drift = {"updatedAt": "2026-09-11T12:01:00Z"}
    facts = collect(api, 1, policy=policy())
    assert facts["stable"]
    assert facts["observation_changes"] == [
        {
            "group": "pr",
            "identity": None,
            "field": "updated_at",
            "before": "2026-09-11T12:00:00Z",
            "after": "2026-09-11T12:01:00Z",
        }
    ]
    result = assess(facts, policy())
    assert result["decision"] == "SHADOW_CONDITIONS_MET"


def test_collection_errors_cannot_be_confused_with_no_changes():
    api = FixtureAPI()
    api.failure = "API 403"
    facts = collect(api, 1, policy=policy())
    assert facts["collection_errors"] == ["API 403"]
    assert assess(facts, policy())["decision"] == "INSUFFICIENT_DATA"


def test_rest_pages_follow_all_pages():
    api = GitHub("example/project")
    with patch.object(
        api, "request", side_effect=[[{"id": n} for n in range(100)], [{"id": 101}]]
    ) as request:
        assert len(api.pages("/pulls?state=open")) == 101
        assert "&per_page=100&page=2" in request.call_args.args[0]


def test_pagination_limits_never_return_partial_success():
    api = GitHub("example/project")
    with (
        patch("pr_merge_readiness.collect.MAX_PAGES", 1),
        patch.object(api, "request", return_value=[{}] * 100),
    ):
        with pytest.raises(CollectionError):
            api.pages("/pulls")


def test_targeting_uses_pr_number_or_current_open_prs():
    api = FixtureAPI()
    assert targets(api, {"pull_request": {"number": 9}}, None) == [9]
    assert targets(api, {}, 7) == [7]
    with patch.object(api, "pages", return_value=[{"number": 1}, {"number": 2}]):
        assert targets(api, {}, None) == [1, 2]
    with pytest.raises(CollectionError):
        targets(api, {}, -1)


def test_closed_event_targets_ended_pr_without_listing_open_prs():
    api = FixtureAPI()
    for merged in (False, True):
        with patch.object(api, "pages") as pages:
            event = {
                "action": "closed",
                "pull_request": {"number": 9, "state": "closed", "merged": merged},
            }
            assert targets(api, event, None) == [9]
            pages.assert_not_called()


def test_graphql_state_query_omits_unused_cursor_variable():
    api = GitHub("example/project")
    response = {"data": {"repository": {"pullRequest": {"number": 1}}}}
    with patch.object(api, "request", return_value=response) as request:
        api.graphql(1, "number")
        assert "$cursor" not in request.call_args.args[1]["query"]


@pytest.mark.parametrize("state", ["CLEAN", "BLOCKED", "UNSTABLE", "BEHIND", "UNKNOWN"])
def test_ci_aggregate_changes_are_neither_requested_nor_recorded(state):
    api = FixtureAPI()
    api.drift = {"mergeStateStatus": state, "potentialMergeCommit": {"oid": "d" * 40}}
    graphql = api.graphql

    def read(number, selection):
        assert "mergeStateStatus" not in selection
        assert "potentialMergeCommit" not in selection
        return graphql(number, selection)

    api.graphql = read
    result = assess(collect(api, 1, policy=policy()), policy())
    assert result["decision"] == "SHADOW_CONDITIONS_MET"
    assert result["observations"]["stable"]
    assert result["observations"]["observation_changes"] == []
    assert not {"checks", "ci_history"} & result["observations"].keys()
    assert not {"merge_state", "merge_sha"} & result["observations"]["pr"].keys()
    assert not any("/check-runs" in path or "/statuses" in path for path in api.paths)
    assert "CI履歴" not in markdown(result)


@pytest.mark.parametrize(
    "drift",
    [
        {"headRefOid": "d" * 40},
        {"baseRefOid": "d" * 40},
        {"baseRefName": "release"},
        {"changedFiles": 2},
    ],
)
def test_review_target_drift_invalidates_label_assessment(drift):
    api = FixtureAPI()
    api.drift = {"isDraft": True, **drift}
    facts = collect(api, 1, policy=policy())
    assert not facts["stable"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("isDraft", True),
        ("mergeable", "UNKNOWN"),
        ("reviewDecision", "CHANGES_REQUESTED"),
        ("state", "CLOSED"),
    ],
)
def test_github_merge_state_is_not_a_gate_or_wait_target(field, value):
    api = FixtureAPI()
    api.drift = {field: value}
    original = api.graphql

    def read(number, selection):
        assert not {"isDraft", "mergeable", "reviewDecision"} & set(selection.split())
        return original(number, selection)

    api.graphql = read
    result = assess(collect(api, 1, policy=policy()), policy())
    assert result["decision"] == "SHADOW_CONDITIONS_MET"
    assert api.reads == 2
    assert "unresolved_threads" not in result["observations"]
    assert not {"draft", "mergeable", "review_decision"} & result["observations"]["pr"].keys()


def test_recent_files_do_not_read_reviews_or_threads():
    api = FixtureAPI()
    pages = api.pages

    def read(path, key=None):
        assert not path.endswith("/reviews")
        return pages(path, key)

    api.pages = read
    result = collect(api, 1, policy=policy())
    assert result["reviews"] == []
    assert assess(result, policy())["decision"] == "SHADOW_CONDITIONS_MET"


def test_history_approval_is_rechecked_and_drift_requires_reobservation():
    api = FixtureAPI()
    pages = api.pages

    def changed(path, key=None):
        records = pages(path, key)
        if path.endswith("/reviews") and api.paths.count(path) == 2:
            records[0]["state"] = "DISMISSED"
        return records

    api.pages = changed
    settings = {"stale_change_review_days": 1}
    result = collect(api, 1, policy=settings)
    assert not result["rechecked"]["reviews"]
    assert result["observation_changes"][0]["field"] == "state"
    assert assess(result, settings)["decision"] == "INSUFFICIENT_DATA"
