"""設定契約と生成物の不一致は成功扱いしない。"""

import copy
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from pr_merge_readiness.config import load_config, policy_from, relative_path, validate_config
from pr_merge_readiness.contracts import EvaluationError
from pr_merge_readiness.generate import generate_workflow, render

ROOT = Path(__file__).resolve().parent.parent


def config():
    return load_config(ROOT / "examples/devops-tycoon.toml")


class ConfigTests(unittest.TestCase):
    def test_examples_have_exact_workflow_and_check_identities(self):
        for name, count in (("devops-tycoon", 2), ("nimius-player", 7)):
            value = load_config(ROOT / "examples" / f"{name}.toml")
            self.assertEqual(len(value["ci"]["required_checks"]), count)
            self.assertEqual({c["app_id"] for c in value["ci"]["required_checks"]}, {15368})
            self.assertEqual(policy_from(value)["stale_change_review_days"], 30)
            self.assertNotIn("version", policy_from(value))
            self.assertNotIn("action_ref", policy_from(value))

    def test_publication_defaults_and_combinations(self):
        value = config()
        del value["publication"]
        self.assertEqual(
            validate_config(value)["publication"], {"checks": True, "labels": "manual"}
        )
        for checks in (True, False):
            for labels in ("manual", "off"):
                value["publication"] = {"checks": checks, "labels": labels}
                self.assertEqual(validate_config(value)["publication"], value["publication"])

    def test_required_fields_unknown_keys_and_invalid_types(self):
        for section, key in (
            (None, "version"),
            (None, "action_ref"),
            (None, "ci"),
            (None, "review"),
            ("ci", "workflows"),
            ("ci", "required_checks"),
            ("review", "minimum_approvals"),
            ("review", "require_resolved_threads"),
            ("review", "stale_change_review_days"),
        ):
            value = config()
            del (value[section] if section else value)[key]
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                validate_config(value)
        for section in (None, "ci", "review", "publication"):
            value = config()
            (value[section] if section else value)["typo"] = True
            with self.subTest(section=section), self.assertRaises(ValueError):
                validate_config(value)
        mutations = [
            ("version", True),
            ("version", 2),
            ("action_ref", "main"),
            ("action_ref", "a" * 39),
            ("ci", []),
            ("review", None),
            ("publication", []),
        ]
        for key, invalid in mutations:
            with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError):
                validate_config({**config(), key: invalid})

    def test_threshold_is_a_positive_integer(self):
        for invalid in (0, -1, True, None, "30", 30.0):
            value = config()
            value["review"]["stale_change_review_days"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_config(value)

    def test_duplicate_workflows_checks_and_missing_producers(self):
        for mutate in (
            lambda v: v["ci"].update(workflows=[]),
            lambda v: v["ci"].update(workflows=["CI", "CI"]),
            lambda v: v["ci"].update(workflows=[" CI"]),
            lambda v: v["ci"].update(required_checks=[]),
            lambda v: v["ci"]["required_checks"].append(
                copy.deepcopy(v["ci"]["required_checks"][0])
            ),
            lambda v: v["ci"]["required_checks"][0].pop("app_id"),
            lambda v: v["ci"]["required_checks"][0].update(app_id=True),
            lambda v: v["publication"].update(labels="auto"),
            lambda v: v["publication"].update(checks="false"),
        ):
            value = config()
            mutate(value)
            with self.assertRaises((ValueError, TypeError)):
                validate_config(value)
        value = config()
        value["ci"]["required_checks"] = [{"kind": "status", "name": "ci", "creator": "builder"}]
        self.assertEqual(validate_config(value)["ci"]["required_checks"][0]["creator"], "builder")

    def test_toml_duplicate_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("version = 1\nversion = 1\n")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_yaml_quotes_names_and_generates_fixed_references(self):
        value = config()
        value["ci"]["workflows"] = ["CI: ['x'] # quote", "日本語"]
        first = render(value, ".github/config.toml", ".github/workflows/readiness.yml")
        self.assertEqual(
            first, render(value, ".github/config.toml", ".github/workflows/readiness.yml")
        )
        document = yaml.load(first, Loader=yaml.BaseLoader)
        self.assertEqual(document["on"]["workflow_run"]["workflows"], value["ci"]["workflows"])
        for job in document["jobs"].values():
            self.assertTrue(job["uses"].endswith("@" + value["action_ref"]))
            self.assertEqual(job["with"]["action-ref"], value["action_ref"])

    def test_check_detects_missing_and_changed_files_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path.cwd()
            os.chdir(directory)
            try:
                path = Path(".github/workflows/readiness.yml")
                self.assertEqual(generate_workflow(config(), Path("config.toml"), path, True), 1)
                self.assertFalse(path.exists())
                self.assertEqual(generate_workflow(config(), Path("config.toml"), path, False), 0)
                self.assertEqual(generate_workflow(config(), Path("config.toml"), path, True), 0)
                path.write_text("# manual edit\n")
                self.assertEqual(generate_workflow(config(), Path("config.toml"), path, True), 1)
                self.assertEqual(path.read_text(), "# manual edit\n")
            finally:
                os.chdir(old)

    def test_paths_cannot_escape_repository(self):
        for path in (
            "../config",
            "/config",
            "a/../config",
            "./config",
            "a\\config",
            "a\nconfig",
            ".",
        ):
            with self.subTest(path=path), self.assertRaises(EvaluationError):
                relative_path(path)
