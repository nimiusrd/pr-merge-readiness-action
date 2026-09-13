"""判定済みの結果だけを使ってSummaryの表示を検証する。"""

import unittest

from pr_merge_readiness.report import markdown
from tests.test_support import HEAD, facts, policy


def result(data):
    return {
        "schema_version": 1,
        "mode": "shadow",
        "decision": "WAITING",
        "conditions": [{"name": "example", "status": "waiting", "detail": "reason"}],
        "observations": data,
        "policy": policy(),
        "policy_sha256": "fingerprint",
    }


class ReportTests(unittest.TestCase):
    def test_markdown_escapes_pr_derived_text(self):
        data = facts()
        data["pr"]["base_ref"] = "<script>alert(1)</script>\n![x](https://bad)"
        text = markdown(result(data))
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn(HEAD, text)
        self.assertIn("2026-09-11", text)

    def test_missing_null_and_empty_diagnostics_keep_distinct_meanings(self):
        for diagnostic, expected in (
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
        ):
            with self.subTest(diagnostic=diagnostic):
                value = result({**facts(), **diagnostic})
                # policyが不正でも表示側は判定を再実行しない。
                value["policy"] = {}
                self.assertIn(expected, markdown(value))
                self.assertEqual(value["decision"], "WAITING")

    def test_partial_failed_observation_renders_without_filling_missing_data(self):
        value = result({"collection_errors": ["API 403"], "stable": False})
        value["decision"] = "INSUFFICIENT_DATA"
        text = markdown(value)
        self.assertIn("INSUFFICIENT_DATA", text)
        self.assertIn("対象: <code>null</code>", text)
        self.assertNotIn("pr", value["observations"])
