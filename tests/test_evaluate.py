"""言語・pathによらない判定と、情報不足・鮮度・発行元の境界を検証する。"""

import unittest

from pr_merge_readiness.contracts import EvaluationError
from pr_merge_readiness.evaluate import assess
from tests.test_support import BASE, HEAD, MERGE, facts, policy


class EvaluateTests(unittest.TestCase):
    def decision(self, data, config=None):
        return assess(data, config or policy())["decision"]

    def test_conditions_met_is_explicitly_shadow_only(self):
        result = assess(facts(), policy())
        self.assertEqual(result["decision"], "SHADOW_CONDITIONS_MET")
        self.assertEqual(result["mode"], "shadow")
        self.assertNotIn("risk", result)
        self.assertTrue(all(c["status"] == "pass" for c in result["conditions"]))

    def test_size_path_and_test_names_do_not_change_decision(self):
        for path in [
            "tests/test.py",
            "src/state/save.ts",
            "README.md",
            "unknown/language.xyz",
        ]:
            with self.subTest(path=path):
                data = facts()
                data["files"][0]["path"] = path
                data["change_history"]["files"][0].update(path=path, history_path=path)
                data["change"].update(additions=100000, deletions=90000, changed_files=1)
                self.assertEqual(self.decision(data), "SHADOW_CONDITIONS_MET")

    def test_small_change_does_not_override_failure(self):
        data = facts()
        data["change"].update(additions=1, deletions=0)
        data["checks"][0]["conclusion"] = "failure"
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")

    def test_changed_ci_definitions_require_human_review_despite_success(self):
        data = facts()
        data["ci_definition_changes"] = ["provider-defined/pipeline.yml"]
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")
        data["checks"][0].update(status="in_progress", conclusion=None)
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")

    def test_unknown_data_never_becomes_success(self):
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
            with self.subTest(key=key):
                data = facts()
                del data[key]
                self.assertEqual(self.decision(data), "INSUFFICIENT_DATA")
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
            self.assertEqual(self.decision(data), "INSUFFICIENT_DATA")

    def test_pr_conditions(self):
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
            with self.subTest(update=update):
                data = facts()
                data["pr"].update(update)
                self.assertEqual(self.decision(data), expected)

    def test_aggregate_block_waits_for_explicit_pending_conditions(self):
        data = facts()
        data["pr"].update(merge_state="BLOCKED", review_decision="REVIEW_REQUIRED")
        self.assertEqual(self.decision(data), "WAITING")
        data["pr"]["review_decision"] = "APPROVED"
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")
        data["checks"][0].update(status="in_progress", conclusion=None)
        self.assertEqual(self.decision(data), "WAITING")
        data["checks"][0].update(status="completed", conclusion="success")
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")
        data["pr"]["merge_state"] = "CLEAN"
        self.assertEqual(self.decision(data), "SHADOW_CONDITIONS_MET")

    def test_explicit_failure_still_blocks_while_approval_is_pending(self):
        data = facts()
        data["pr"].update(merge_state="BLOCKED", review_decision="REVIEW_REQUIRED")
        data["checks"][0]["conclusion"] = "failure"
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")
        data["checks"][0]["conclusion"] = "success"
        data["unresolved_threads"] = 1
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")

    def test_incomplete_pagination_overrides_other_results(self):
        data = facts()
        data["collection_errors"] = ["GraphQL pagination limit exceeded"]
        data["pr"]["merge_state"] = "BLOCKED"
        self.assertEqual(self.decision(data), "INSUFFICIENT_DATA")

    def test_missing_check_wrong_sha_or_producer_waits(self):
        for change in [{"app_id": 2}, {"sha": "d" * 40}, {"name": "Other"}]:
            data = facts()
            data["checks"][0].update(change)
            self.assertEqual(self.decision(data), "WAITING")
        data["checks"] = []
        self.assertEqual(self.decision(data), "WAITING")

    def test_non_success_conclusions_are_not_accepted(self):
        for conclusion in [
            "failure",
            "cancelled",
            "timed_out",
            "skipped",
            "neutral",
            "action_required",
            None,
        ]:
            with self.subTest(conclusion=conclusion):
                data = facts()
                data["checks"][0]["conclusion"] = conclusion
                self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")

    def test_latest_rerun_supersedes_old_success_even_while_pending(self):
        data = facts()
        data["checks"].append(
            {**data["checks"][0], "id": 2, "status": "in_progress", "conclusion": None}
        )
        self.assertEqual(self.decision(data), "WAITING")
        data["checks"][1].update(status="completed", conclusion="failure")
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")
        data["checks"].append({**data["checks"][0], "id": 3})
        self.assertEqual(self.decision(data), "SHADOW_CONDITIONS_MET")

    def test_current_merge_failure_is_not_hidden_by_head_success(self):
        data = facts()
        data["checks"].append({**data["checks"][0], "id": 2, "sha": MERGE, "conclusion": "failure"})
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")
        data["pr"]["merge_sha"] = "d" * 40
        self.assertEqual(self.decision(data), "SHADOW_CONDITIONS_MET")

    def test_status_check_requires_exact_creator(self):
        config = policy()
        config["required_checks"] = [
            {"kind": "status", "name": "External CI", "creator": "ci-service"}
        ]
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
        self.assertEqual(self.decision(data, config), "SHADOW_CONDITIONS_MET")
        data["checks"][0]["creator"] = "someone-else"
        self.assertEqual(self.decision(data, config), "WAITING")

    def test_approvals_are_per_person_and_current_head(self):
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
        self.assertEqual(self.decision(data, config), "WAITING")
        data["reviews"][2]["commit_sha"] = HEAD
        self.assertEqual(self.decision(data, config), "SHADOW_CONDITIONS_MET")
        data["reviews"].append(
            {
                "id": 4,
                "author": "bob",
                "state": "COMMENTED",
                "commit_sha": HEAD,
                "submitted_at": "2026-09-11T12:00:00Z",
            }
        )
        self.assertEqual(self.decision(data, config), "SHADOW_CONDITIONS_MET")

    def test_change_request_blocks_even_without_approval_requirement(self):
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
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")
        data["reviews"].append(
            {
                "id": 2,
                "author": "alice",
                "state": "APPROVED",
                "commit_sha": HEAD,
                "submitted_at": "2026-09-11T12:00:00Z",
            }
        )
        self.assertEqual(self.decision(data), "SHADOW_CONDITIONS_MET")

    def test_review_submission_time_takes_precedence_over_draft_creation_id(self):
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
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")
        data["reviews"][0]["state"] = "DISMISSED"
        self.assertEqual(self.decision(data, {**policy(), "minimum_approvals": 1}), "WAITING")

    def test_dismissed_review_does_not_count_as_approval(self):
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
        self.assertEqual(self.decision(data, {**policy(), "minimum_approvals": 1}), "WAITING")

    def test_unresolved_threads_are_an_explicit_policy(self):
        data = facts()
        data["unresolved_threads"] = 1
        self.assertEqual(self.decision(data), "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(
            self.decision(data, {**policy(), "require_resolved_threads": False}),
            "SHADOW_CONDITIONS_MET",
        )

    def test_required_checks_are_mandatory_and_policy_is_validated(self):
        for change in [
            {"required_checks": []},
            {"mode": "merge"},
            {"version": 1},
            {"minimum_approvals": True},
            {"require_resolved_threads": "false"},
            {"required_checks": [{"kind": "check_run", "name": "Test", "app_id": 0}]},
            {"required_checks": policy()["required_checks"] * 2},
        ]:
            with self.subTest(change=change), self.assertRaises(EvaluationError):
                assess(facts(), {**policy(), **change})

    def test_policy_fingerprint_is_stable_and_sensitive_to_configuration(self):
        config = policy()
        first = assess(facts(), config)["policy_sha256"]
        self.assertEqual(
            first,
            assess(facts(), dict(reversed(list(config.items()))))["policy_sha256"],
        )
        self.assertNotEqual(
            first, assess(facts(), {**config, "minimum_approvals": 1})["policy_sha256"]
        )


if __name__ == "__main__":
    unittest.main()
