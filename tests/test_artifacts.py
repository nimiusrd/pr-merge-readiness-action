"""新しい artifact の出所・実行単位を検証する。"""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from pr_merge_readiness.artifacts import load_reports, provenance, validate_report, write_json
from pr_merge_readiness.observe import observe
from tests.test_change_history import AT, MultiPRHistoryAPI
from tests.test_collect import FixtureAPI
from tests.test_support import BASE, policy

SOURCE = provenance("example/project", BASE, BASE, ".github/config.toml")
NAME = "pr-merge-readiness-42-1"


def save(directory, api=None):
    return observe(api or FixtureAPI(), policy(), {}, 1, directory, SOURCE, "42", "1", NAME)


class ArtifactTests(unittest.TestCase):
    def test_round_trip_and_ignored_unlisted_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(save(root), 0)
            (root / "pr-999.json").write_text("broken")
            reports = load_reports(root, "example/project", "42", "1", NAME, SOURCE, policy())
            self.assertEqual([n for n, _ in reports], [1])
            self.assertEqual(reports[0][1]["provenance"], SOURCE)

    def test_wrong_run_attempt_repository_name_source_and_missing_report_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root)
            for key, value in (
                ("run_id", "41"),
                ("run_attempt", "2"),
                ("repository", "another/repo"),
                ("artifact_name", "old"),
                ("provenance", {}),
                ("reports", ["../pr-1.json"]),
                ("reports", ["pr-2.json"]),
                ("reports", ["pr-1.json", "pr-1.json"]),
                ("collection_failed", True),
            ):
                path = root / "manifest.json"
                original = json.loads(path.read_text())
                write_json(path, {**original, key: value})
                with self.subTest(key=key), self.assertRaises((ValueError, OSError)):
                    load_reports(root, "example/project", "42", "1", NAME, SOURCE, policy())
                write_json(path, original)

    def test_old_schema_and_tampered_policy_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_report({"schema_version": 2})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root)
            report = json.loads((root / "pr-1.json").read_text())
            for mutate in (
                lambda r: r.update(schema_version=True),
                lambda r: r["policy"].update(stale_change_review_days=7),
                lambda r: r["provenance"]["evaluator"].update(repository="other/repo"),
            ):
                changed = deepcopy(report)
                mutate(changed)
                with self.assertRaises(ValueError):
                    validate_report(changed)

    def test_global_failure_and_no_targets_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = FixtureAPI()
            api.pages = lambda *args: []
            self.assertEqual(observe(api, policy(), {}, None, root, SOURCE, "42", "1", NAME), 0)
            self.assertEqual(
                load_reports(root, api.repository, "42", "1", NAME, SOURCE, policy()), []
            )
            with patch.object(api, "pages", side_effect=ValueError("API 403")):
                self.assertEqual(observe(api, policy(), {}, None, root, SOURCE, "42", "1", NAME), 1)
            self.assertTrue((root / "collection-error.json").exists())
            with self.assertRaises(ValueError):
                load_reports(root, api.repository, "42", "1", NAME, SOURCE, policy())

    def test_ten_prs_share_one_hundred_request_budget_and_cache(self):
        for shared in (True, False):
            api = MultiPRHistoryAPI(shared_paths=shared)
            with (
                tempfile.TemporaryDirectory() as directory,
                patch("pr_merge_readiness.collect.datetime") as clock,
            ):
                clock.now.return_value = AT
                root = Path(directory)
                code = observe(api, policy(), {}, None, root, SOURCE, "42", "1", NAME)
                self.assertEqual(len(api.history_requests), 100)
                self.assertEqual(code, 0 if shared else 1)
                reports = load_reports(root, api.repository, "42", "1", NAME, SOURCE, policy())
                self.assertEqual(len(reports), 10)
                self.assertEqual(
                    reports[-1][1]["decision"],
                    "HUMAN_REVIEW_REQUIRED" if shared else "INSUFFICIENT_DATA",
                )
                if not shared:
                    self.assertIn(
                        "100/100 used, 100 needed",
                        reports[-1][1]["observations"]["collection_errors"][0],
                    )
