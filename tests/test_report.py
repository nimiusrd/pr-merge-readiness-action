"""判定済みの結果だけを使ってSummaryの表示を検証する。"""

import pytest
from pr_merge_readiness.report import markdown
from tests.test_support import HEAD, facts, policy


def result(data):
    return {
        "decision": "HUMAN_REVIEW_REQUIRED",
        "conditions": [{"name": "example", "status": "blocked", "detail": "reason"}],
        "observations": data,
        "policy": policy(),
    }


def test_markdown_escapes_pr_derived_text():
    data = facts()
    data["pr"]["base_ref"] = "<script>alert(1)</script>\n![x](https://bad)"
    text = markdown(result(data))
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert HEAD in text
    assert "2026-09-11" in text


@pytest.mark.parametrize(
    "diagnostic,expected",
    (
        ({}, "差分情報なし"),
        ({"observation_changes": None}, "差分情報なし"),
        ({"observation_changes": []}, "変化はありません"),
        (
            {
                "observation_changes": [
                    {"group": "pr", "field": "draft", "before": False, "after": True}
                ]
            },
            "draft",
        ),
    ),
)
def test_missing_null_and_empty_diagnostics_keep_distinct_meanings(diagnostic, expected):
    value = result({**facts(), **diagnostic})
    value["policy"] = {}
    assert expected in markdown(value)
    assert value["decision"] == "HUMAN_REVIEW_REQUIRED"


def test_partial_failed_observation_renders_without_filling_missing_data():
    value = result({"collection_errors": ["API 403"], "stable": False})
    value["decision"] = "INSUFFICIENT_DATA"
    text = markdown(value)
    assert "INSUFFICIENT_DATA" in text
    assert "対象: <code>null</code>" in text
    assert "pr" not in value["observations"]


def test_summary_limits_the_decision_to_additional_history_review():
    text = markdown(result(facts()))
    assert "追加確認の判定:" in text
    assert "GitHub Ruleset" in text
    assert "レビュー完了やマージ許可を示すものではありません" in text
    assert "ラベル用判定" not in text
