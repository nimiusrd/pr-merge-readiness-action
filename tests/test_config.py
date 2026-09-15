"""設定の欠落・型不正・不正な固定参照は成功扱いしない。"""

import pytest
import tomllib
import tempfile
from pathlib import Path
from pr_merge_readiness.config import load_config, policy_from, relative_path, validate_config
from pr_merge_readiness.contracts import EvaluationError

ROOT = Path(__file__).resolve().parent.parent


def config_text(name="minimal"):
    return (ROOT / "examples" / f"{name}.toml").read_text()


def config():
    return validate_config(tomllib.loads(config_text()))


@pytest.mark.parametrize("name,approvals,days", (("minimal", 0, 30), ("review-policy", 1, 14)))
def test_examples_define_review_policy_without_ci(name, approvals, days):
    value = validate_config(tomllib.loads(config_text(name)))
    assert policy_from(value) == {
        "minimum_approvals": approvals,
        "require_resolved_threads": True,
        "stale_change_review_days": days,
    }
    assert "version" not in policy_from(value)
    assert "action_ref" not in policy_from(value)


def test_publication_defaults_and_combinations():
    value = config()
    del value["publication"]
    assert validate_config(value)["publication"] == {"checks": True, "labels": "manual"}
    for checks in (True, False):
        for labels in ("auto", "manual", "off"):
            value["publication"] = {"checks": checks, "labels": labels}
            assert validate_config(value)["publication"] == value["publication"]


def test_required_fields_unknown_keys_and_invalid_types():
    for section, key in (
        (None, "version"),
        (None, "action_ref"),
        (None, "review"),
        ("review", "minimum_approvals"),
        ("review", "require_resolved_threads"),
        ("review", "stale_change_review_days"),
    ):
        value = config()
        del (value[section] if section else value)[key]
        with pytest.raises(ValueError):
            validate_config(value)
    for section in (None, "review", "publication"):
        value = config()
        (value[section] if section else value)["typo"] = True
        with pytest.raises(ValueError):
            validate_config(value)
    mutations = [
        ("version", True),
        ("version", 1),
        ("action_ref", "main"),
        ("action_ref", "a" * 39),
        ("ci", []),
        ("review", None),
        ("publication", []),
    ]
    for key, invalid in mutations:
        with pytest.raises(ValueError):
            validate_config({**config(), key: invalid})


@pytest.mark.parametrize("invalid", (0, -1, True, None, "30", 30.0))
def test_threshold_is_a_positive_integer(invalid):
    value = config()
    value["review"]["stale_change_review_days"] = invalid
    with pytest.raises(ValueError):
        validate_config(value)


def test_ci_settings_and_invalid_publication_are_rejected():
    for mutate in (
        lambda v: v.update(ci={"workflows": ["CI"], "required_checks": []}),
        lambda v: v["review"].update(required_checks=[]),
        lambda v: v["publication"].update(labels="always"),
        lambda v: v["publication"].update(checks="false"),
    ):
        value = config()
        mutate(value)
        with pytest.raises((ValueError, TypeError)):
            validate_config(value)


def test_toml_duplicate_key_is_rejected():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.toml"
        path.write_text("version = 1\nversion = 1\n")
        with pytest.raises(ValueError):
            load_config(path)


@pytest.mark.parametrize(
    "path", ("../config", "/config", "a/../config", "./config", "a\\config", "a\nconfig", ".")
)
def test_paths_cannot_escape_repository(path):
    with pytest.raises(EvaluationError):
        relative_path(path)
