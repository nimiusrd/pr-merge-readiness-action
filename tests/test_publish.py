"""単一writerのラベル更新と、次回実行での失敗回復を検証する。"""

import copy
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import unquote

from pr_merge_readiness.publish import (
    DECISION_LABELS,
    LABEL_DESCRIPTIONS,
    MANAGED_LABELS,
    GitHub,
    PublishError,
    cleanup_closed,
    encoded_label,
    ensure_labels,
    publish_pr,
)
from tests.test_support import BASE, HEAD

WAITING = DECISION_LABELS["WAITING"]
READY = DECISION_LABELS["SHADOW_CONDITIONS_MET"]
UNKNOWN = DECISION_LABELS["INSUFFICIENT_DATA"]


def report(decision="WAITING", number=1):
    return {
        "decision": decision,
        "observations": {
            "repository": "example/project",
            "pr": {
                "number": number,
                "head_sha": HEAD,
                "base_sha": BASE,
                "base_ref": "main",
                "state": "OPEN",
                "draft": False,
            },
        },
    }


def pull(labels=(), state="open"):
    return {
        "head": {"sha": HEAD},
        "base": {"sha": BASE, "ref": "main"},
        "state": state,
        "draft": False,
        "labels": [{"name": name} for name in labels],
    }


class FixtureAPI:
    repository = "example/project"
    prefix = "/repos/example/project"

    def __init__(self, labels=()):
        self.pulls = {1: pull(labels)}
        self.definitions = set(MANAGED_LABELS)
        self.calls = []
        self.fail = None
        self.closed = []

    def names(self, number=1):
        return [item["name"] for item in self.pulls[number]["labels"]]

    def request(self, path, body=None, method=None):
        verb = method or ("POST" if body is not None else "GET")
        self.calls.append((verb, path, body))
        if self.fail and self.fail(verb, path):
            raise PublishError("API failure")
        route = unquote(path.removeprefix(self.prefix)).split("/")
        if route[1] == "pulls":
            return copy.deepcopy(self.pulls[int(route[2])])
        if route[1] == "labels":
            if verb == "POST":
                self.definitions.add(body["name"])
                return body
            if "/".join(route[2:]) not in self.definitions:
                raise PublishError("HTTP 404")
            return {}
        if route[1] == "issues":
            number = int(route[2])
            labels = self.pulls[number]["labels"]
            if verb == "POST":
                labels.extend(
                    {"name": name} for name in body["labels"] if name not in self.names(number)
                )
            elif verb == "DELETE":
                name = "/".join(route[4:])
                labels[:] = [item for item in labels if item["name"] != name]
            else:
                raise AssertionError(verb)
            return None
        raise AssertionError(path)

    def pages(self, path):
        self.calls.append(("GET", path, None))
        return self.closed


class PublishTests(unittest.TestCase):
    def test_all_decisions_replace_only_managed_labels_and_are_idempotent(self):
        for decision, label in DECISION_LABELS.items():
            with self.subTest(decision=decision):
                api = FixtureAPI(["enhancement", *MANAGED_LABELS])
                self.assertEqual(publish_pr(api, 1, report(decision)), "updated")
                self.assertCountEqual(api.names(), ["enhancement", label])
                api.calls.clear()
                self.assertEqual(publish_pr(api, 1, report(decision)), "unchanged")
                self.assertTrue(all(verb == "GET" for verb, _, _ in api.calls))
        self.assertTrue(all(len(text) <= 100 for text in LABEL_DESCRIPTIONS.values()))

    def test_pr_changes_are_marked_for_reobservation(self):
        for key, value in {
            "head_sha": "d" * 40,
            "base_sha": "d" * 40,
            "base_ref": "release",
            "state": "CLOSED",
            "draft": True,
        }.items():
            with self.subTest(key=key):
                api = FixtureAPI([READY])
                data = report("SHADOW_CONDITIONS_MET")
                data["observations"]["pr"][key] = value
                publish_pr(api, 1, data)
                self.assertEqual(api.names(), [UNKNOWN])

    def test_collection_failure_without_pr_facts_expires_ready_label(self):
        api = FixtureAPI([READY])
        data = report("INSUFFICIENT_DATA")
        del data["observations"]["pr"]
        publish_pr(api, 1, data)
        self.assertEqual(api.names(), [UNKNOWN])

    def test_invalid_decision_or_wrong_report_identity_never_writes(self):
        variants = [report("INVALID"), report(number=2), report()]
        variants[-1]["observations"]["repository"] = "other/repo"
        for data in variants:
            with self.subTest(data=data):
                api = FixtureAPI([READY])
                with self.assertRaises(PublishError):
                    publish_pr(api, 1, data)
                self.assertEqual(api.names(), [READY])
                self.assertTrue(all(verb == "GET" for verb, _, _ in api.calls))

    def test_closed_pr_removes_all_managed_labels(self):
        api = FixtureAPI(["bug", *MANAGED_LABELS])
        api.pulls[1]["state"] = "closed"
        publish_pr(api, 1, report())
        self.assertEqual(api.names(), ["bug"])

    def test_failed_add_preserves_old_label_then_next_run_repairs(self):
        api = FixtureAPI([READY])
        api.fail = lambda verb, path: verb == "POST"
        with self.assertRaises(PublishError):
            publish_pr(api, 1, report())
        self.assertEqual(api.names(), [READY])
        api.fail = None
        publish_pr(api, 1, report())
        self.assertEqual(api.names(), [WAITING])

    def test_failed_delete_leaves_new_label_then_next_run_repairs(self):
        api = FixtureAPI([READY, "bug"])
        api.fail = lambda verb, path: verb == "DELETE"
        with self.assertRaises(PublishError):
            publish_pr(api, 1, report())
        self.assertCountEqual(api.names(), [READY, WAITING, "bug"])
        api.fail = None
        publish_pr(api, 1, report())
        self.assertCountEqual(api.names(), [WAITING, "bug"])

    def test_cleanup_deduplicates_closed_prs_and_ignores_issues_and_reopened_prs(self):
        api = FixtureAPI()
        api.pulls = {1: pull([READY, "bug"], "closed"), 2: pull([WAITING])}
        api.closed = [
            {"number": 1, "pull_request": {}},
            {"number": 2, "pull_request": {}},
            {"number": 3},
        ]
        cleanup_closed(api)
        self.assertEqual(api.names(1), ["bug"])
        self.assertEqual(api.names(2), [WAITING])
        self.assertEqual(sum("/pulls/1" in path for _, path, _ in api.calls), 1)

    def test_definitions_created_once_then_reused(self):
        api = FixtureAPI()
        api.definitions.clear()
        ensure_labels(api)
        self.assertEqual(api.definitions, MANAGED_LABELS)
        ensure_labels(api)
        self.assertEqual(sum(verb == "POST" for verb, _, _ in api.calls), 4)

    def test_http_delete_encodes_japanese_label_and_sends_no_body(self):
        with patch("pr_merge_readiness.publish.urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b""
            api = GitHub("example/project")
            path = f"{api.prefix}/issues/1/labels/{encoded_label(WAITING)}"
            self.assertIsNone(api.request(path, method="DELETE"))
            req = opened.call_args.args[0]
            self.assertEqual(req.method, "DELETE")
            self.assertIsNone(req.data)
            self.assertNotIn("待ち", req.full_url)

    def test_http_failure_is_not_success(self):
        error = HTTPError("https://example.com", 403, "Forbidden", {}, None)
        with patch("pr_merge_readiness.publish.urlopen", side_effect=error):
            with self.assertRaisesRegex(PublishError, "HTTP 403"):
                GitHub("example/project").request(
                    "/repos/example/project/labels", {"name": WAITING}
                )

    def test_closed_listing_reads_all_pages_and_rejects_truncation(self):
        api = GitHub("example/project")
        with patch.object(api, "request", side_effect=[[{}] * 100, [{"number": 101}]]) as request:
            self.assertEqual(len(api.pages("/issues?state=closed")), 101)
            self.assertIn("&per_page=100&page=2", request.call_args.args[0])
        with (
            patch("pr_merge_readiness.publish.MAX_PAGES", 2),
            patch.object(api, "request", return_value=[{}] * 100),
        ):
            with self.assertRaisesRegex(PublishError, "pagination limit"):
                api.pages("/issues?state=closed")


if __name__ == "__main__":
    unittest.main()
