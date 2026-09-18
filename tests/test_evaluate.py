"""追加確認だけを判定し、GitHubのマージ条件を重複評価しない。"""

import pytest
from pr_merge_readiness.contracts import EvaluationError
from pr_merge_readiness.evaluate import assess
from tests.test_support import facts, policy


def test_conditions_met_is_explicitly_shadow_only():
    result = assess(facts(), policy())
    assert result["decision"] == "SHADOW_CONDITIONS_MET"
    assert set(c["name"] for c in result["conditions"]) == {"freshness", "stale_change_review"}
    assert "label_assessment" not in result
    assert "risk" not in result


@pytest.mark.parametrize(
    "path",
    ["tests/test.py", "src/state/save.ts", ".github/workflows/ci.yml", "unknown/language.xyz"],
)
def test_size_path_and_test_names_do_not_change_decision(path):
    data = facts()
    data["files"][0]["path"] = path
    data["change_history"]["files"][0].update(path=path, history_path=path)
    data["change"].update(additions=100000, deletions=90000, changed_files=1)
    assert assess(data, policy())["decision"] == "SHADOW_CONDITIONS_MET"


@pytest.mark.parametrize(
    "missing",
    ["pr", "change", "stable", "observed_at", "collection_errors", "change_history", "files"],
)
def test_missing_inputs_never_produce_a_ready_label(missing):
    data = facts()
    del data[missing]
    assert assess(data, policy())["decision"] == "INSUFFICIENT_DATA"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.update(collection_errors=["API 403"]),
        lambda f: f.update(stable=False),
        lambda f: f.update(stable="true"),
        lambda f: f["change"].update(additions=-1),
        lambda f: f["pr"].update(head_sha="invalid"),
        lambda f: f.update(schema_version=2),
    ],
)
def test_unknown_or_invalid_data_never_becomes_success(mutate):
    data = facts()
    mutate(data)
    assert assess(data, policy())["decision"] == "INSUFFICIENT_DATA"


@pytest.mark.parametrize("state", ["CHANGES_REQUESTED", "PENDING", "DISMISSED", "COMMENTED"])
def test_recent_files_do_not_require_or_interpret_reviews(state):
    data = facts()
    data["reviews"] = [{"state": state}]
    assert assess(data, policy())["decision"] == "SHADOW_CONDITIONS_MET"
    del data["reviews"]
    assert assess(data, policy())["decision"] == "SHADOW_CONDITIONS_MET"


@pytest.mark.parametrize(
    "change",
    [
        {"required_checks": []},
        {"minimum_approvals": 0},
        {"require_resolved_threads": False},
        {"stale_change_review_days": 0},
    ],
)
def test_policy_rejects_removed_or_invalid_settings(change):
    with pytest.raises(EvaluationError):
        assess(facts(), {**policy(), **change})
