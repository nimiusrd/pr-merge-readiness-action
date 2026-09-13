"""APIのページング・鮮度と収集失敗を、外部への書き込みなしで検証する。"""

import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from urllib.error import HTTPError

from pr_merge_readiness.collect import CollectionError, GitHub, collect, targets
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
        return [{"sha": BASE, "commit": {"committer": {"date": "2026-09-01T12:00:00Z"}}}]

    def graphql(self, number, selection):
        self.reads += 1
        if self.reads == 2:
            self.state.update(self.drift)
        return deepcopy(self.state)

    def connection(self, number, name, fields):
        if self.failure:
            raise CollectionError(self.failure)
        return [{"isResolved": True} for _ in range(254)]

    def pages(self, path, key=None):
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
        if path.startswith(f"/commits/{HEAD}/check-runs"):
            return [
                {
                    "id": 1,
                    "name": "Test",
                    "app": {"id": 1},
                    "head_sha": HEAD,
                    "status": "completed",
                    "conclusion": "failure",
                },
                {
                    "id": 2,
                    "name": "Test",
                    "app": {"id": 1},
                    "head_sha": HEAD,
                    "status": "completed",
                    "conclusion": "success",
                },
            ]
        return []


class CollectTests(unittest.TestCase):
    def test_workflow_changes_cannot_be_masked_by_same_name_success(self):
        for change in [
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
        ]:
            with self.subTest(change=change):
                api = FixtureAPI()
                api.files[0].update(change)
                result = collect(api, 1)
                self.assertFalse(result["collection_errors"])
                self.assertTrue(result["ci_definition_changes"])
                self.assertEqual(assess(result, policy())["decision"], "HUMAN_REVIEW_REQUIRED")

    def test_rename_requires_previous_path_and_preserves_ordinary_changes(self):
        api = FixtureAPI()
        api.files[0].update(status="renamed", previous_filename="before.rs")
        result = collect(api, 1)
        self.assertEqual(result["files"][0]["previous_path"], "before.rs")
        self.assertEqual(result["ci_definition_changes"], [])
        self.assertNotIn("patch", result["files"][0])
        self.assertEqual(assess(result, policy())["decision"], "SHADOW_CONDITIONS_MET")
        del api.files[0]["previous_filename"]
        self.assertEqual(assess(collect(api, 1), policy())["decision"], "INSUFFICIENT_DATA")

    def test_rerun_detected_without_any_pr_state_change(self):
        for add_run in [True, False]:
            with self.subTest(add_run=add_run):
                api = FixtureAPI()
                original = api.pages

                def changing(path, key=None):
                    records = original(path, key)
                    if (
                        path.startswith(f"/commits/{HEAD}/check-runs")
                        and api.paths.count(path) == 2
                    ):
                        if add_run:
                            records.append(
                                {
                                    **records[-1],
                                    "id": 3,
                                    "status": "in_progress",
                                    "conclusion": None,
                                }
                            )
                        else:
                            records[-1].update(status="in_progress", conclusion=None)
                    return records

                api.pages = changing
                result = collect(api, 1)
                self.assertTrue(result["rechecked"]["pr"])
                self.assertFalse(result["rechecked"]["checks"])
                changes = result["observation_changes"]
                summary = markdown(assess(result, policy()))
                self.assertIn("identity", summary)
                self.assertIn("check_run", summary)
                self.assertIn("in_progress", summary)
                if add_run:
                    self.assertEqual(changes[0]["field"], "record")
                    self.assertIsNone(changes[0]["before"])
                    self.assertEqual(changes[0]["after"]["id"], 3)
                else:
                    self.assertEqual({c["field"] for c in changes}, {"status", "conclusion"})
                    self.assertEqual(changes[0]["identity"]["name"], "Test")
                self.assertEqual(assess(result, policy())["decision"], "INSUFFICIENT_DATA")

    def test_legacy_status_change_and_recheck_error_are_not_success(self):
        for fail in [False, True]:
            api = FixtureAPI()
            original = api.pages

            def changing(path, key=None):
                records = original(path, key)
                if path == f"/commits/{HEAD}/statuses" and api.paths.count(path) == 2:
                    if fail:
                        raise CollectionError("API 403 during recheck")
                    return [
                        {
                            "id": 4,
                            "context": "External CI",
                            "creator": {"login": "ci"},
                            "state": "pending",
                        }
                    ]
                return records

            api.pages = changing
            facts = collect(api, 1)
            self.assertEqual(assess(facts, policy())["decision"], "INSUFFICIENT_DATA")
            if fail:
                self.assertIsNone(facts["observation_changes"])
            else:
                change = facts["observation_changes"][0]
                self.assertEqual(change["identity"]["kind"], "status")
                self.assertEqual(change["after"]["name"], "External CI")

    def test_reviews_and_thread_resolution_are_also_rechecked(self):
        api = FixtureAPI()
        original = api.pages

        def changing_reviews(path, key=None):
            records = original(path, key)
            if path.endswith("/reviews") and api.paths.count(path) == 2:
                records[0]["state"] = "CHANGES_REQUESTED"
            return records

        api.pages = changing_reviews
        facts = collect(api, 1)
        self.assertEqual(assess(facts, policy())["decision"], "INSUFFICIENT_DATA")
        self.assertEqual(
            facts["observation_changes"][0],
            {
                "group": "reviews",
                "identity": {"id": 1, "author": "reviewer"},
                "field": "state",
                "before": "APPROVED",
                "after": "CHANGES_REQUESTED",
            },
        )
        api = FixtureAPI()
        original_connection = api.connection
        thread_reads = 0

        def changing_threads(number, name, fields):
            nonlocal thread_reads
            records = original_connection(number, name, fields)
            if name == "reviewThreads":
                thread_reads += 1
                if thread_reads == 2:
                    records[0]["isResolved"] = False
            return records

        api.connection = changing_threads
        result = collect(api, 1)
        self.assertFalse(result["rechecked"]["unresolved_threads"])
        self.assertEqual(
            result["observation_changes"],
            [
                {
                    "group": "unresolved_threads",
                    "identity": None,
                    "field": "count",
                    "before": 0,
                    "after": 1,
                }
            ],
        )
        self.assertEqual(assess(result, policy())["decision"], "INSUFFICIENT_DATA")

    def test_metadata_order_changes_do_not_invalidate_observation(self):
        api = FixtureAPI()
        original = api.pages

        def reordered(path, key=None):
            records = original(path, key)
            return list(reversed(records)) if api.paths.count(path) == 2 else records

        api.pages = reordered
        self.assertEqual(assess(collect(api, 1), policy())["decision"], "SHADOW_CONDITIONS_MET")

    def test_removed_records_and_summary_escape_only_normalized_metadata(self):
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
        result = assess(collect(api, 1), policy())
        changes = result["observations"]["observation_changes"]
        self.assertEqual(changes[0]["field"], "base_ref")
        self.assertEqual(changes[0]["before"], "trunk")
        self.assertEqual(changes[1]["field"], "record")
        self.assertIsNone(changes[1]["after"])
        self.assertEqual(changes[1]["before"]["id"], 1)
        encoded = json.dumps(result)
        summary = markdown(result)
        self.assertNotIn("SECRET REVIEW BODY", encoded)
        self.assertNotIn("ignored source diff", encoded)
        self.assertNotIn("<script>", summary)
        self.assertIn("&lt;script&gt;", summary)
        self.assertIn("base_ref", summary)
        self.assertIn("reviews", summary)

    def test_http_transport_sends_json_and_rejects_graphql_errors(self):
        api = GitHub("example/project")
        with patch(
            "pr_merge_readiness.collect.urlopen", return_value=io.BytesIO(b'{"data": {}}')
        ) as request:
            self.assertEqual(
                api.request("/graphql", {"query": "query { viewer { login } }"}),
                {"data": {}},
            )
            sent = request.call_args.args[0]
            self.assertEqual(sent.get_header("Content-type"), "application/json")
            self.assertIn("query", json.loads(sent.data))
        with patch(
            "pr_merge_readiness.collect.urlopen",
            return_value=io.BytesIO(b'{"errors": [{"message": "forbidden"}], "data": {}}'),
        ):
            with self.assertRaises(CollectionError):
                api.request("/graphql", {"query": "query { viewer { login } }"})

    def test_http_failure_or_oversized_response_is_not_empty_success(self):
        api = GitHub("example/project")
        with patch(
            "pr_merge_readiness.collect.urlopen",
            side_effect=HTTPError("https://api.github.com/graphql", 403, "forbidden", {}, None),
        ):
            with self.assertRaisesRegex(CollectionError, "HTTP 403"):
                api.request("/graphql", {})
        with (
            patch("pr_merge_readiness.collect.MAX_RESPONSE_BYTES", 4),
            patch("pr_merge_readiness.collect.urlopen", return_value=io.BytesIO(b'{"long": 1}')),
        ):
            with self.assertRaisesRegex(CollectionError, "exceeds limit"):
                api.request("/graphql", {})

    def test_declared_totals_must_match_collected_records(self):
        api = GitHub("example/project")
        with patch.object(api, "request", return_value={"total_count": 3, "check_runs": [{}]}):
            with self.assertRaisesRegex(CollectionError, "count mismatch"):
                api.pages("/commits/sha/check-runs", "check_runs")
        with patch.object(
            api,
            "graphql",
            return_value={
                "files": {
                    "totalCount": 2,
                    "nodes": [{}],
                    "pageInfo": {"hasNextPage": False, "endCursor": "end"},
                }
            },
        ):
            with self.assertRaisesRegex(CollectionError, "count mismatch"):
                api.connection(1, "files", "path")
        api = FixtureAPI()
        api.state["changedFiles"] = 2
        self.assertIn("totals mismatch", collect(api, 1)["collection_errors"][0])

    def test_collects_only_metadata_and_preserves_observations(self):
        api = FixtureAPI()
        facts = collect(api, 1)
        self.assertTrue(facts["stable"])
        self.assertEqual(facts["pr"]["base_ref"], "trunk")
        self.assertEqual(facts["change"]["additions"], 3)
        self.assertIsNone(facts["change"]["binary_files"])
        self.assertIsNone(facts["change"]["mode_changes"])
        self.assertTrue(facts["ci_history"][0]["failure_before_success"])
        self.assertEqual(assess(facts, policy())["decision"], "SHADOW_CONDITIONS_MET")
        self.assertFalse(any("/contents/" in p or "/git/blobs/" in p for p in api.paths))

    def test_head_base_draft_review_or_merge_changes_invalidate_collection(self):
        for drift in [
            {"headRefOid": "d" * 40},
            {"baseRefOid": "d" * 40},
            {"isDraft": True},
            {"reviewDecision": "CHANGES_REQUESTED"},
            {"potentialMergeCommit": {"oid": "d" * 40}},
            {"updatedAt": "2026-09-11T13:00:00Z"},
        ]:
            with self.subTest(drift=drift):
                api = FixtureAPI()
                api.drift = drift
                self.assertEqual(
                    assess(collect(api, 1), policy())["decision"],
                    "INSUFFICIENT_DATA",
                )

    def test_errors_cannot_be_confused_with_no_threads_or_no_checks(self):
        api = FixtureAPI()
        api.failure = "API 403"
        facts = collect(api, 1)
        self.assertEqual(facts["collection_errors"], ["API 403"])
        self.assertEqual(assess(facts, policy())["decision"], "INSUFFICIENT_DATA")

    def test_mismatched_check_sha_is_incomplete(self):
        api = FixtureAPI()
        original = api.pages

        def wrong(path, key=None):
            result = original(path, key)
            if "check-runs" in path and result:
                result[0]["head_sha"] = BASE
            return result

        api.pages = wrong
        self.assertIn("SHA mismatch", collect(api, 1)["collection_errors"][0])

    def test_rest_pages_follow_all_pages(self):
        api = GitHub("example/project")
        with patch.object(
            api, "request", side_effect=[[{"id": n} for n in range(100)], [{"id": 101}]]
        ) as request:
            self.assertEqual(len(api.pages("/pulls?state=open")), 101)
            self.assertIn("&per_page=100&page=2", request.call_args.args[0])

    def test_pagination_limits_never_return_partial_success(self):
        api = GitHub("example/project")
        with (
            patch("pr_merge_readiness.collect.MAX_PAGES", 1),
            patch.object(api, "request", return_value=[{}] * 100),
        ):
            with self.assertRaises(CollectionError):
                api.pages("/pulls")
        with (
            patch("pr_merge_readiness.collect.MAX_PAGES", 1),
            patch.object(
                api,
                "graphql",
                return_value={
                    "files": {
                        "totalCount": 2,
                        "nodes": [{}],
                        "pageInfo": {"endCursor": "next", "hasNextPage": True},
                    }
                },
            ),
        ):
            with self.assertRaises(CollectionError):
                api.connection(1, "files", "path")

    def test_graphql_threads_are_paginated_past_first_hundred(self):
        api = GitHub("example/project")
        responses = [
            {
                "reviewThreads": {
                    "totalCount": 101,
                    "nodes": [{"isResolved": True}] * 100,
                    "pageInfo": {"endCursor": "second", "hasNextPage": True},
                }
            },
            {
                "reviewThreads": {
                    "totalCount": 101,
                    "nodes": [{"isResolved": False}],
                    "pageInfo": {"endCursor": "end", "hasNextPage": False},
                }
            },
        ]
        with patch.object(api, "graphql", side_effect=responses) as request:
            nodes = api.connection(1, "reviewThreads", "isResolved")
            self.assertEqual(sum(not n["isResolved"] for n in nodes), 1)
            self.assertEqual(request.call_args.args[2], "second")

    def test_targeting_uses_pr_number_or_current_open_prs(self):
        api = FixtureAPI()
        self.assertEqual(targets(api, {"pull_request": {"number": 9}}, None), [9])
        self.assertEqual(targets(api, {}, 7), [7])
        with patch.object(api, "pages", return_value=[{"number": 1}, {"number": 2}]):
            self.assertEqual(targets(api, {"workflow_run": {"head_sha": BASE}}, None), [1, 2])
        with self.assertRaises(CollectionError):
            targets(api, {}, -1)

    def test_closed_event_targets_ended_pr_without_listing_open_prs(self):
        api = FixtureAPI()
        for merged in (False, True):
            with self.subTest(merged=merged), patch.object(api, "pages") as pages:
                event = {
                    "action": "closed",
                    "pull_request": {"number": 9, "state": "closed", "merged": merged},
                }
                self.assertEqual(targets(api, event, None), [9])
                pages.assert_not_called()

    def test_graphql_state_query_omits_unused_cursor_variable(self):
        api = GitHub("example/project")
        response = {"data": {"repository": {"pullRequest": {"number": 1}}}}
        with patch.object(api, "request", return_value=response) as request:
            api.graphql(1, "number")
            self.assertNotIn("$cursor", request.call_args.args[1]["query"])
            api.graphql(1, "reviewThreads(first: 100, after: $cursor) { nodes { isResolved } }")
            self.assertIn("$cursor: String", request.call_args.args[1]["query"])


if __name__ == "__main__":
    unittest.main()
