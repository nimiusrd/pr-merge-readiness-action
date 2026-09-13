"""変更間隔の境界・履歴の取得元・人間承認・artifactからの再評価を検証する。"""

import pytest
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from pr_merge_readiness.collect import ChangeHistoryCollector, CollectionError, collect
from pr_merge_readiness.contracts import EvaluationError
from pr_merge_readiness.evaluate import assess
from tests.test_collect import FixtureAPI
from tests.test_support import BASE, HEAD, facts, policy

AT = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
OLD = "d" * 40


def config():
    return {**policy(), "stale_change_review_days": 30}


def history_facts(age=timedelta(days=31)):
    data = facts()
    data["change_history"] = {
        "base_sha": BASE,
        "files": [
            {
                "path": "whatever.rs",
                "history_path": "whatever.rs",
                "last_commit_sha": OLD,
                "last_changed_at": (AT - age).isoformat(),
            }
        ],
    }
    return data


def approval(**overrides):
    return {
        "id": 1,
        "author": "maintainer",
        "author_type": "User",
        "author_association": "COLLABORATOR",
        "state": "APPROVED",
        "commit_sha": HEAD,
        "submitted_at": AT.isoformat(),
        **overrides,
    }


def history_condition(result):
    return next((c for c in result["conditions"] if c["name"] == "stale_change_review"))


class HistoryAPI(FixtureAPI):
    prefix = "/repos/example/project"

    def __init__(self):
        super().__init__()
        self.history_requests = []
        self.history_failure = None
        self.commits = [
            {
                "sha": OLD,
                "commit": {
                    "author": {"date": "2026-09-11T12:00:00Z", "email": "DO NOT SAVE"},
                    "committer": {"date": "2026-08-11T12:00:00Z"},
                    "message": "DO NOT SAVE COMMIT MESSAGE",
                },
            }
        ]
        self.actor = "Bot"

    def request(self, path, body=None):
        self.history_requests.append(path)
        if self.history_failure:
            raise CollectionError(self.history_failure)
        return deepcopy(self.commits)

    def pages(self, path, key=None):
        values = super().pages(path, key)
        if path.endswith("/reviews"):
            for review in values:
                review["user"]["type"] = self.actor
                review["author_association"] = "OWNER"
        return values


class MultiPRHistoryAPI(HistoryAPI):
    def __init__(self, *, shared_paths):
        super().__init__()
        self.shared_paths = shared_paths

    def graphql(self, number, selection):
        self.state.update(number=number, changedFiles=100, additions=300, deletions=100)
        self.files = [
            {
                "filename": f"{(0 if self.shared_paths else number)}/file-{index}.go",
                "status": "modified",
                "additions": 3,
                "deletions": 1,
            }
            for index in range(100)
        ]
        return super().graphql(number, selection)

    def pages(self, path, key=None):
        if path == "/pulls?state=open":
            return [{"number": number} for number in range(1, 11)]
        return super().pages(path, key)


def observe(api):
    with patch("pr_merge_readiness.collect.datetime") as clock:
        clock.now.return_value = AT
        return collect(api, 1)


@pytest.mark.parametrize(
    "age,decision,status",
    [
        (timedelta(days=30) - timedelta(seconds=1), "SHADOW_CONDITIONS_MET", "within_threshold"),
        (timedelta(days=30), "SHADOW_CONDITIONS_MET", "within_threshold"),
        (timedelta(days=30, microseconds=1), "HUMAN_REVIEW_REQUIRED", "stale"),
    ],
)
def test_thirty_days_is_inclusive_and_not_rounded_to_whole_days(age, decision, status):
    result = assess(history_facts(age), config())
    assert result["decision"] == decision
    detail = history_condition(result)["detail"]
    assert detail["files"][0]["age_seconds"] == age.total_seconds()
    assert detail["files"][0]["status"] == status
    assert detail["threshold_days"] == 30


def test_timestamp_offsets_are_compared_as_instants():
    data = history_facts()
    data["change_history"]["files"][0]["last_changed_at"] = "2026-08-12T21:00:00+09:00"
    assert assess(data, config())["decision"] == "SHADOW_CONDITIONS_MET"


def test_threshold_is_policy_controlled_and_mandatory():
    data = history_facts(timedelta(days=20))
    assert assess(data, config())["decision"] == "SHADOW_CONDITIONS_MET"
    strict = assess(data, {**config(), "stale_change_review_days": 14})
    assert strict["decision"] == "HUMAN_REVIEW_REQUIRED"
    assert strict["policy_sha256"] != assess(data, config())["policy_sha256"]
    for invalid in [0, -1, True, None, "30", 1.5]:
        with pytest.raises(EvaluationError):
            assess(data, {**config(), "stale_change_review_days": invalid})


@pytest.mark.parametrize(
    "change,expected",
    [
        ({}, "SHADOW_CONDITIONS_MET"),
        ({"author_association": "OWNER"}, "SHADOW_CONDITIONS_MET"),
        ({"author_association": "MEMBER"}, "SHADOW_CONDITIONS_MET"),
        ({"author_type": "Bot"}, "HUMAN_REVIEW_REQUIRED"),
        ({"author_association": "CONTRIBUTOR"}, "HUMAN_REVIEW_REQUIRED"),
        ({"author_association": "NONE"}, "HUMAN_REVIEW_REQUIRED"),
        ({"commit_sha": BASE}, "HUMAN_REVIEW_REQUIRED"),
        ({"state": "COMMENTED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"state": "DISMISSED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"state": "PENDING"}, "HUMAN_REVIEW_REQUIRED"),
    ],
)
def test_human_approval_must_be_current_and_from_a_repository_member(change, expected):
    data = history_facts()
    data["reviews"] = [approval(**change)]
    result = assess(data, config())
    assert result["decision"] == expected
    assert history_condition(result)["detail"]["human_approval_review_ids"] == (
        [1] if expected == "SHADOW_CONDITIONS_MET" else []
    )


def test_new_head_and_dismissal_require_a_new_approval():
    data = history_facts()
    data["reviews"] = [approval()]
    data["pr"]["head_sha"] = "e" * 40
    assert assess(data, config())["decision"] == "HUMAN_REVIEW_REQUIRED"
    data = history_facts()
    data["reviews"] = [approval(), approval(id=2, state="COMMENTED")]
    assert assess(data, config())["decision"] == "SHADOW_CONDITIONS_MET"
    data["reviews"][1]["state"] = "DISMISSED"
    assert assess(data, config())["decision"] == "HUMAN_REVIEW_REQUIRED"


def test_human_approval_does_not_override_other_conditions():
    for mutation, expected in [
        (lambda d: d["checks"][0].update(conclusion="failure"), "HUMAN_REVIEW_REQUIRED"),
        (lambda d: d.update(unresolved_threads=1), "HUMAN_REVIEW_REQUIRED"),
        (lambda d: d.update(checks=[]), "WAITING"),
        (lambda d: d.update(stable=False), "INSUFFICIENT_DATA"),
    ]:
        data = history_facts()
        data["reviews"] = [approval()]
        mutation(data)
        assert assess(data, config())["decision"] == expected
    data = history_facts()
    data["reviews"] = [approval()]
    assert assess(data, {**config(), "minimum_approvals": 2})["decision"] == "WAITING"


def test_missing_or_inconsistent_history_never_passes_even_with_approval():
    mutations = [
        lambda d: d.pop("change_history"),
        lambda d: d["change_history"].update(base_sha=HEAD),
        lambda d: d["change_history"].update(files=[]),
        lambda d: d["change_history"]["files"].append(deepcopy(d["change_history"]["files"][0])),
        lambda d: d["files"].append(deepcopy(d["files"][0])),
        lambda d: d["change"].update(changed_files=2),
        lambda d: d["change_history"]["files"][0].update(path="different.go"),
        lambda d: d["change_history"]["files"][0].update(history_path="different.go"),
        lambda d: d["change_history"]["files"][0].update(last_commit_sha=None),
        lambda d: d["change_history"]["files"][0].update(last_commit_sha="invalid"),
        lambda d: d["change_history"]["files"][0].update(last_changed_at=None),
        lambda d: d["change_history"]["files"][0].update(last_changed_at="2026-08-01"),
        lambda d: d["change_history"]["files"][0].update(last_changed_at="2026-08-01T00:00:00"),
        lambda d: d["change_history"]["files"][0].update(last_changed_at="2027-08-01T00:00:00Z"),
        lambda d: d.update(observed_at="invalid"),
        lambda d: d["files"][0].update(changeType="UNRECOGNIZED"),
        lambda d: d["reviews"][0].pop("author_type"),
        lambda d: d["reviews"][0].update(author_association=None),
    ]
    for index, mutation in enumerate(mutations):
        data = history_facts()
        data["reviews"] = [approval()]
        mutation(data)
        assert assess(data, config())["decision"] == "INSUFFICIENT_DATA"


def test_new_files_and_renames_do_not_hide_an_old_existing_file():
    data = history_facts()
    data["files"][0].update(path="renamed.rs", changeType="RENAMED", previous_path="whatever.rs")
    data["change_history"]["files"][0]["path"] = "renamed.rs"
    data["files"].append({"path": "new.rs", "changeType": "ADDED", "additions": 0, "deletions": 0})
    data["change_history"]["files"].append(
        {"path": "new.rs", "history_path": None, "last_commit_sha": None, "last_changed_at": None}
    )
    data["change"]["changed_files"] = 2
    result = assess(data, config())
    assert result["decision"] == "HUMAN_REVIEW_REQUIRED"
    assert [f["status"] for f in history_condition(result)["detail"]["files"]] == ["new", "stale"]
    data["files"] = data["files"][1:]
    data["change_history"]["files"] = data["change_history"]["files"][1:]
    data["change"]["changed_files"] = 1
    result = assess(data, config())
    assert result["decision"] == "SHADOW_CONDITIONS_MET"
    assert history_condition(result)["detail"]["required_human_approvals"] == 0
    data["change_history"]["files"][0]["last_commit_sha"] = OLD
    assert assess(data, config())["decision"] == "INSUFFICIENT_DATA"


def test_budget_preflight_preserves_capacity_for_later_prs_and_base_changes():
    api = HistoryAPI()
    collector = ChangeHistoryCollector(api)
    files = [{"path": "one.go", "changeType": "MODIFIED"}]
    with patch("pr_merge_readiness.collect.MAX_HISTORY_REQUESTS", 2):
        first = collector.collect(BASE, files)
        too_many = [{"path": path, "changeType": "MODIFIED"} for path in ("two.go", "three.go")]
        with pytest.raises(CollectionError, match="1/2 used, 2 needed"):
            collector.collect(BASE, too_many)
        assert len(api.history_requests) == 1
        first["files"][0]["last_commit_sha"] = HEAD
        rename = [{"path": "renamed.go", "previous_path": "one.go", "changeType": "RENAMED"}]
        renamed = collector.collect(BASE, rename)
        assert renamed["files"][0]["path"] == "renamed.go"
        assert renamed["files"][0]["last_commit_sha"] == OLD
        assert len(api.history_requests) == 1
        api.commits[0]["sha"] = HEAD
        updated = collector.collect(HEAD, files)
        assert updated["files"][0]["last_commit_sha"] == HEAD
        assert parse_qs(urlsplit(api.history_requests[-1]).query)["sha"] == [HEAD]
        assert len(api.history_requests) == 2
        assert collector.collect(BASE, files)["files"][0]["last_commit_sha"] == OLD
        added = collector.collect(BASE, [{"path": "new.go", "changeType": "ADDED"}])
        assert added["files"][0]["last_changed_at"] is None
        with pytest.raises(CollectionError, match="2/2 used, 2 needed"):
            collector.collect(BASE, too_many)
        assert len(api.history_requests) == 2


@pytest.mark.parametrize("failure", ("http", "invalid"))
def test_failed_requests_also_consume_the_run_budget(failure):
    with patch("pr_merge_readiness.collect.MAX_HISTORY_REQUESTS", 1):
        api = HistoryAPI()
        if failure == "http":
            api.history_failure = "API history: HTTP 403"
        else:
            api.commits = []
        collector = ChangeHistoryCollector(api)
        files = [{"path": "one.go", "changeType": "MODIFIED"}]
        with pytest.raises(CollectionError):
            collector.collect(BASE, files)
        api.history_failure = None
        with pytest.raises(CollectionError, match="1/1 used, 1 needed"):
            collector.collect(BASE, files)
        assert len(api.history_requests) == 1


def test_new_run_does_not_reuse_the_previous_budget_or_cache():
    api = HistoryAPI()
    files = [{"path": "one.go", "changeType": "MODIFIED"}]
    with patch("pr_merge_readiness.collect.MAX_HISTORY_REQUESTS", 1):
        first = ChangeHistoryCollector(api).collect(BASE, files)
        api.commits[0]["sha"] = HEAD
        second = ChangeHistoryCollector(api).collect(BASE, files)
    assert len(api.history_requests) == 2
    assert first["files"][0]["last_commit_sha"] == OLD
    assert second["files"][0]["last_commit_sha"] == HEAD


def test_base_and_rename_source_are_pinned_and_query_values_are_encoded():
    api = HistoryAPI()
    previous = "古い dir/a&b?#.go"
    api.files[0].update(filename="new.go", status="renamed", previous_filename=previous)
    data = observe(api)
    assert len(api.history_requests) == 1
    url = urlsplit(api.history_requests[0])
    assert url.path == "/repos/example/project/commits"
    assert parse_qs(url.query) == {"sha": [BASE], "path": [previous], "per_page": ["1"]}
    assert data["change_history"] == {
        "base_sha": BASE,
        "files": [
            {
                "path": "new.go",
                "history_path": previous,
                "last_commit_sha": OLD,
                "last_changed_at": "2026-08-11T12:00:00Z",
            }
        ],
    }
    assert "DO NOT SAVE" not in json.dumps(data)
    result = assess(data, config())
    assert result["decision"] == "HUMAN_REVIEW_REQUIRED"
    assert history_condition(result)["detail"]["files"][0]["age_seconds"] == 31 * 86400


@pytest.mark.parametrize("status", ["added", "copied", "modified", "removed"])
def test_added_files_skip_history_but_missing_existing_history_is_an_error(status):
    api = HistoryAPI()
    api.files[0]["status"] = status
    api.commits = []
    data = observe(api)
    expected = "SHADOW_CONDITIONS_MET" if status in {"added", "copied"} else "INSUFFICIENT_DATA"
    assert assess(data, config())["decision"] == expected
    assert len(api.history_requests) == (0 if status in {"added", "copied"} else 1)


def test_history_failure_limit_and_invalid_responses_are_information_gaps():
    for response in [
        [],
        {},
        [{}, {}],
        [{"sha": "invalid"}],
        [{"sha": OLD, "commit": {"committer": {"date": None}}}],
    ]:
        api = HistoryAPI()
        api.commits = response
        data = observe(api)
        assert data["collection_errors"]
        assert assess(data, config())["decision"] == "INSUFFICIENT_DATA"
    api = HistoryAPI()
    api.history_failure = "API history: HTTP 403"
    assert assess(observe(api), config())["decision"] == "INSUFFICIENT_DATA"
    api = HistoryAPI()
    with patch("pr_merge_readiness.collect.MAX_HISTORY_FILES", 0):
        data = observe(api)
    assert "change history file limit exceeded" in data["collection_errors"]
    assert api.history_requests == []


def test_base_changes_and_reviewer_identity_changes_invalidate_observation():
    api = HistoryAPI()
    api.drift = {"baseRefOid": "e" * 40}
    assert assess(observe(api), config())["decision"] == "INSUFFICIENT_DATA"
    api = HistoryAPI()
    original = api.pages

    def changed_identity(path, key=None):
        values = original(path, key)
        if path.endswith("/reviews") and api.paths.count(path) == 2:
            values[0]["user"]["type"] = "User"
        return values

    api.pages = changed_identity
    data = observe(api)
    assert not data["rechecked"]["reviews"]
    assert data["observation_changes"][0]["field"] == "author_type"
    assert assess(data, config())["decision"] == "INSUFFICIENT_DATA"
