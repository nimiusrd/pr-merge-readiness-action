"""レビュー条件だけを持つ設定version 4を検証する。"""

import tomllib
from pathlib import Path

import pytest

from pr_merge_readiness.config import load_config, policy_from, relative_path, validate_config
from pr_merge_readiness.contracts import EvaluationError

ROOT = Path(__file__).resolve().parent.parent


def config_text(name="minimal"):
    return (ROOT / "examples" / f"{name}.toml").read_text()


def config():
    return validate_config(tomllib.loads(config_text()))


@pytest.mark.parametrize("name,days", (("minimal", 30), ("review-policy", 14)))
def test_examples_define_only_history_policy(name, days):
    value = validate_config(tomllib.loads(config_text(name)))
    assert set(value) == {"version", "review"}
    assert value["version"] == 4
    assert policy_from(value) == {
        "stale_change_review_days": days,
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("publication", {"checks": False, "labels": "off"}),
        ("action_ref", "a" * 40),
        ("ci", {}),
        ("typo", True),
    ],
)
def test_removed_and_unknown_settings_are_rejected(key, value):
    with pytest.raises(ValueError, match="invalid keys"):
        validate_config({**config(), key: value})


@pytest.mark.parametrize("version", [1, 2, 3, 5, True, "4", None])
def test_only_version_four_is_supported(version):
    with pytest.raises(ValueError, match="version 4 required"):
        validate_config({**config(), "version": version})


@pytest.mark.parametrize(
    "section,key",
    [
        (None, "version"),
        (None, "review"),
        ("review", "stale_change_review_days"),
    ],
)
def test_required_fields(section, key):
    value = config()
    del (value[section] if section else value)[key]
    with pytest.raises(ValueError):
        validate_config(value)


@pytest.mark.parametrize("value", [None, [], {}, {"unknown": True}])
def test_malformed_review(value):
    with pytest.raises(ValueError):
        validate_config({**config(), "review": value})


@pytest.mark.parametrize(
    "key,invalid",
    [
        ("stale_change_review_days", 0),
        ("stale_change_review_days", -1),
        ("stale_change_review_days", True),
        ("stale_change_review_days", "30"),
        ("stale_change_review_days", 30.0),
        ("minimum_approvals", -1),
        ("minimum_approvals", False),
        ("require_resolved_threads", 1),
        ("require_resolved_threads", "true"),
    ],
)
def test_review_types_and_boundaries(key, invalid):
    value = config()
    value["review"][key] = invalid
    with pytest.raises(ValueError):
        validate_config(value)


def test_toml_duplicate_key_is_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("version = 3\nversion = 3\n")
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize(
    "path", ("../config", "/config", "a/../config", "./config", "a\\config", "a\nconfig", ".")
)
def test_paths_cannot_escape_repository(path):
    with pytest.raises(EvaluationError):
        relative_path(path)
