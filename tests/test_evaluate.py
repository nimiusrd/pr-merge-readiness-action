"""言語・pathによらない判定と、情報不足・鮮度・発行元の境界を検証する。"""

import pytest
from pr_merge_readiness.contracts import EvaluationError
from pr_merge_readiness.evaluate import assess
from tests.test_support import BASE, HEAD, facts, policy


def decision(data, config=None):
    return assess(data, config or policy())["decision"]


def test_conditions_met_is_explicitly_shadow_only():
    result = assess(facts(), policy())
    assert result["decision"] == "SHADOW_CONDITIONS_MET"
    assert result["mode"] == "shadow"
    assert "risk" not in result
    assert all((c["status"] == "pass" for c in result["conditions"]))


@pytest.mark.parametrize(
    "path", ["tests/test.py", "src/state/save.ts", "README.md", "unknown/language.xyz"]
)
def test_size_path_and_test_names_do_not_change_decision(path):
    data = facts()
    data["files"][0]["path"] = path
    data["change_history"]["files"][0].update(path=path, history_path=path)
    data["change"].update(additions=100000, deletions=90000, changed_files=1)
    assert decision(data) == "SHADOW_CONDITIONS_MET"


def test_small_change_does_not_override_failure():
    data = facts()
    data["change"].update(additions=1, deletions=0)
    data["unresolved_threads"] = 1
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"


def test_changed_ci_definitions_require_human_review_despite_success():
    data = facts()
    data["ci_definition_changes"] = ["provider-defined/pipeline.yml"]
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"


def test_unknown_data_never_becomes_success():
    for key in [
        "pr",
        "change",
        "ci_definition_changes",
        "reviews",
        "unresolved_threads",
        "stable",
        "observed_at",
        "collection_errors",
    ]:
        data = facts()
        del data[key]
        assert decision(data) == "INSUFFICIENT_DATA"
    for mutate in [
        lambda f: f.update(collection_errors=["API 403"]),
        lambda f: f.update(stable=False),
        lambda f: f.update(stable="true"),
        lambda f: f.update(unresolved_threads=None),
        lambda f: f.update(ci_definition_changes=None),
        lambda f: f.update(ci_definition_changes=[None]),
        lambda f: f["change"].update(additions=-1),
        lambda f: f["pr"].update(head_sha="invalid"),
        lambda f: f["pr"].update(draft=None),
    ]:
        data = facts()
        mutate(data)
        assert decision(data) == "INSUFFICIENT_DATA"


def test_pr_conditions():
    cases = [
        ({"draft": True}, "WAITING"),
        ({"state": "CLOSED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"state": "MERGED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"mergeable": "CONFLICTING"}, "HUMAN_REVIEW_REQUIRED"),
        ({"mergeable": "UNKNOWN"}, "WAITING"),
        ({"review_decision": "CHANGES_REQUESTED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"review_decision": "REVIEW_REQUIRED"}, "WAITING"),
    ]
    for update, expected in cases:
        data = facts()
        data["pr"].update(update)
        assert decision(data) == expected


def test_incomplete_pagination_overrides_other_results():
    data = facts()
    data["collection_errors"] = ["GraphQL pagination limit exceeded"]
    assert decision(data) == "INSUFFICIENT_DATA"


def test_approvals_are_per_person_and_current_head():
    config = {**policy(), "minimum_approvals": 2}
    data = facts()
    data["reviews"] = [
        {
            "id": 1,
            "author": "alice",
            "state": "APPROVED",
            "commit_sha": HEAD,
            "submitted_at": "2026-09-11T12:00:00Z",
        },
        {
            "id": 2,
            "author": "alice",
            "state": "APPROVED",
            "commit_sha": HEAD,
            "submitted_at": "2026-09-11T12:00:00Z",
        },
        {
            "id": 3,
            "author": "bob",
            "state": "APPROVED",
            "commit_sha": BASE,
            "submitted_at": "2026-09-11T12:00:00Z",
        },
    ]
    assert decision(data, config) == "WAITING"
    data["reviews"][2]["commit_sha"] = HEAD
    assert decision(data, config) == "SHADOW_CONDITIONS_MET"
    data["reviews"].append(
        {
            "id": 4,
            "author": "bob",
            "state": "COMMENTED",
            "commit_sha": HEAD,
            "submitted_at": "2026-09-11T12:00:00Z",
        }
    )
    assert decision(data, config) == "SHADOW_CONDITIONS_MET"


def test_change_request_blocks_even_without_approval_requirement():
    data = facts()
    data["reviews"] = [
        {
            "id": 1,
            "author": "alice",
            "state": "CHANGES_REQUESTED",
            "commit_sha": BASE,
            "submitted_at": "2026-09-11T12:00:00Z",
        }
    ]
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"
    data["reviews"].append(
        {
            "id": 2,
            "author": "alice",
            "state": "APPROVED",
            "commit_sha": HEAD,
            "submitted_at": "2026-09-11T12:00:00Z",
        }
    )
    assert decision(data) == "SHADOW_CONDITIONS_MET"


def test_review_submission_time_takes_precedence_over_draft_creation_id():
    data = facts()
    data["reviews"] = [
        {
            "id": 1,
            "author": "alice",
            "state": "CHANGES_REQUESTED",
            "commit_sha": HEAD,
            "submitted_at": "2026-09-11T13:00:00Z",
        },
        {
            "id": 2,
            "author": "alice",
            "state": "APPROVED",
            "commit_sha": HEAD,
            "submitted_at": "2026-09-11T12:00:00Z",
        },
    ]
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"
    data["reviews"][0]["state"] = "DISMISSED"
    assert decision(data, {**policy(), "minimum_approvals": 1}) == "WAITING"


def test_dismissed_review_does_not_count_as_approval():
    data = facts()
    data["reviews"] = [
        {
            "id": 1,
            "author": "alice",
            "state": "DISMISSED",
            "commit_sha": HEAD,
            "submitted_at": "2026-09-11T12:00:00Z",
        }
    ]
    assert decision(data, {**policy(), "minimum_approvals": 1}) == "WAITING"


def test_unresolved_threads_are_an_explicit_policy():
    data = facts()
    data["unresolved_threads"] = 1
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"
    assert (
        decision(data, {**policy(), "require_resolved_threads": False}) == "SHADOW_CONDITIONS_MET"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"required_checks": []},
        {"mode": "merge"},
        {"version": 1},
        {"minimum_approvals": True},
        {"require_resolved_threads": "false"},
        {"required_checks": [{"kind": "check_run", "name": "Test", "app_id": 0}]},
    ],
)
def test_policy_rejects_ci_configuration_and_invalid_review_settings(change):
    with pytest.raises(EvaluationError):
        assess(facts(), {**policy(), **change})


def test_policy_fingerprint_is_stable_and_sensitive_to_configuration():
    config = policy()
    first = assess(facts(), config)["policy_sha256"]
    assert first == assess(facts(), dict(reversed(list(config.items()))))["policy_sha256"]
    assert first != assess(facts(), {**config, "minimum_approvals": 1})["policy_sha256"]


@pytest.mark.parametrize(
    "update,expected",
    [
        ({"draft": True}, "WAITING"),
        ({"state": "CLOSED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"state": "MERGED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"mergeable": "CONFLICTING"}, "HUMAN_REVIEW_REQUIRED"),
        ({"mergeable": "UNKNOWN"}, "WAITING"),
        ({"draft": None}, "INSUFFICIENT_DATA"),
        ({"state": None}, "INSUFFICIENT_DATA"),
        ({"mergeable": None}, "INSUFFICIENT_DATA"),
    ],
)
def test_pr_state_affects_reference_assessment_only(update, expected):
    data = facts()
    data["pr"].update(update)
    result = assess(data, policy())
    assert result["decision"] == expected
    assert result["label_assessment"]["decision"] == "SHADOW_CONDITIONS_MET"
    assert not {"open_pr", "ready_for_review", "mergeable", "freshness"} & {
        condition["name"] for condition in result["label_assessment"]["conditions"]
    }
    data["pr"]["review_decision"] = "CHANGES_REQUESTED"
    assert assess(data, policy())["label_assessment"]["decision"] == "HUMAN_REVIEW_REQUIRED"


@pytest.mark.parametrize(
    "change,settings,expected",
    [
        ({}, {"minimum_approvals": 1}, "WAITING"),
        ({"unresolved_threads": 1}, {}, "HUMAN_REVIEW_REQUIRED"),
        ({"ci_definition_changes": [".github/workflows/ci.yml"]}, {}, "HUMAN_REVIEW_REQUIRED"),
        ({}, {"stale_change_review_days": 1}, "HUMAN_REVIEW_REQUIRED"),
        ({"collection_errors": ["API 403"]}, {}, "INSUFFICIENT_DATA"),
        ({"review_stable": False}, {}, "INSUFFICIENT_DATA"),
        ({"review_stable": None}, {}, "INSUFFICIENT_DATA"),
    ],
)
def test_label_assessment_preserves_review_history_and_data_requirements(
    change, settings, expected
):
    data = facts()
    data.update(change)
    data["pr"].update(draft=True, mergeable="CONFLICTING")
    assert assess(data, {**policy(), **settings})["label_assessment"]["decision"] == expected


@pytest.mark.parametrize("missing", ["review_stable", "pr", "reviews", "change_history", "files"])
def test_missing_review_inputs_never_produce_a_ready_label(missing):
    data = facts()
    del data[missing]
    assert assess(data, policy())["label_assessment"]["decision"] == "INSUFFICIENT_DATA"
