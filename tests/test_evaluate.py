"""言語・pathによらない判定と、情報不足・鮮度・発行元の境界を検証する。"""

import pytest
from pr_merge_readiness.contracts import EvaluationError
from pr_merge_readiness.evaluate import assess
from tests.test_support import BASE, HEAD, MERGE, facts, policy


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
    data["checks"][0]["conclusion"] = "failure"
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"


def test_changed_ci_definitions_require_human_review_despite_success():
    data = facts()
    data["ci_definition_changes"] = ["provider-defined/pipeline.yml"]
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"
    data["checks"][0].update(status="in_progress", conclusion=None)
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"


def test_unknown_data_never_becomes_success():
    for key in [
        "pr",
        "change",
        "checks",
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
        ({"draft": True, "merge_state": "DRAFT"}, "WAITING"),
        ({"state": "CLOSED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"state": "MERGED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"mergeable": "CONFLICTING"}, "HUMAN_REVIEW_REQUIRED"),
        ({"mergeable": "UNKNOWN"}, "WAITING"),
        ({"merge_state": "BEHIND"}, "WAITING"),
        ({"merge_state": "UNKNOWN"}, "WAITING"),
        ({"merge_state": "BLOCKED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"merge_state": "UNSTABLE"}, "HUMAN_REVIEW_REQUIRED"),
        ({"review_decision": "CHANGES_REQUESTED"}, "HUMAN_REVIEW_REQUIRED"),
        ({"review_decision": "REVIEW_REQUIRED"}, "WAITING"),
    ]
    for update, expected in cases:
        data = facts()
        data["pr"].update(update)
        assert decision(data) == expected


def test_aggregate_block_waits_for_explicit_pending_conditions():
    data = facts()
    data["pr"].update(merge_state="BLOCKED", review_decision="REVIEW_REQUIRED")
    assert decision(data) == "WAITING"
    data["pr"]["review_decision"] = "APPROVED"
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"
    data["checks"][0].update(status="in_progress", conclusion=None)
    assert decision(data) == "WAITING"
    data["checks"][0].update(status="completed", conclusion="success")
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"
    data["pr"]["merge_state"] = "CLEAN"
    assert decision(data) == "SHADOW_CONDITIONS_MET"


def test_explicit_failure_still_blocks_while_approval_is_pending():
    data = facts()
    data["pr"].update(merge_state="BLOCKED", review_decision="REVIEW_REQUIRED")
    data["checks"][0]["conclusion"] = "failure"
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"
    data["checks"][0]["conclusion"] = "success"
    data["unresolved_threads"] = 1
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"


def test_incomplete_pagination_overrides_other_results():
    data = facts()
    data["collection_errors"] = ["GraphQL pagination limit exceeded"]
    data["pr"]["merge_state"] = "BLOCKED"
    assert decision(data) == "INSUFFICIENT_DATA"


def test_missing_check_wrong_sha_or_producer_waits():
    for change in [{"app_id": 2}, {"sha": "d" * 40}, {"name": "Other"}]:
        data = facts()
        data["checks"][0].update(change)
        assert decision(data) == "WAITING"
    data["checks"] = []
    assert decision(data) == "WAITING"


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "cancelled", "timed_out", "skipped", "neutral", "action_required", None],
)
def test_non_success_conclusions_are_not_accepted(conclusion):
    data = facts()
    data["checks"][0]["conclusion"] = conclusion
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"


def test_latest_rerun_supersedes_old_success_even_while_pending():
    data = facts()
    data["checks"].append(
        {**data["checks"][0], "id": 2, "status": "in_progress", "conclusion": None}
    )
    assert decision(data) == "WAITING"
    data["checks"][1].update(status="completed", conclusion="failure")
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"
    data["checks"].append({**data["checks"][0], "id": 3})
    assert decision(data) == "SHADOW_CONDITIONS_MET"


def test_current_merge_failure_is_not_hidden_by_head_success():
    data = facts()
    data["checks"].append({**data["checks"][0], "id": 2, "sha": MERGE, "conclusion": "failure"})
    assert decision(data) == "HUMAN_REVIEW_REQUIRED"
    data["pr"]["merge_sha"] = "d" * 40
    assert decision(data) == "SHADOW_CONDITIONS_MET"


def test_status_check_requires_exact_creator():
    config = policy()
    config["required_checks"] = [{"kind": "status", "name": "External CI", "creator": "ci-service"}]
    data = facts()
    data["checks"] = [
        {
            **config["required_checks"][0],
            "id": 2,
            "sha": HEAD,
            "status": "completed",
            "conclusion": "success",
        }
    ]
    assert decision(data, config) == "SHADOW_CONDITIONS_MET"
    data["checks"][0]["creator"] = "someone-else"
    assert decision(data, config) == "WAITING"


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
        {"required_checks": policy()["required_checks"] * 2},
    ],
)
def test_required_checks_are_mandatory_and_policy_is_validated(change):
    with pytest.raises(EvaluationError):
        assess(facts(), {**policy(), **change})


def test_policy_fingerprint_is_stable_and_sensitive_to_configuration():
    config = policy()
    first = assess(facts(), config)["policy_sha256"]
    assert first == assess(facts(), dict(reversed(list(config.items()))))["policy_sha256"]
    assert first != assess(facts(), {**config, "minimum_approvals": 1})["policy_sha256"]
