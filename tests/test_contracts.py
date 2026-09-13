"""共通検証は型変換・既定値補完をせず、欠落と不正値を拒否する。"""

import pytest
from pr_merge_readiness.contracts import EvaluationError, boolean, integer, sha, string


@pytest.mark.parametrize(
    "validate,arguments,expected",
    (
        (integer, (0, "count"), 0),
        (integer, (10**30, "count"), 10**30),
        (boolean, (False, "stable"), False),
        (boolean, (True, "stable"), True),
        (string, ("日本語", "name"), "日本語"),
        (sha, ("a" * 40,), "a" * 40),
    ),
)
def test_valid_boundary_values_are_preserved(validate, arguments, expected):
    assert validate(*arguments) == expected


@pytest.mark.parametrize(
    "validate,values,suffix,message",
    (
        (
            integer,
            [None, True, -1, 1.0, "0", []],
            ("count",),
            "count: non-negative integer required",
        ),
        (string, [None, "", 0, []], ("name",), "name: non-empty string required"),
        (boolean, [None, 0, 1, "false", []], ("stable",), "stable: boolean required"),
        (sha, [None, "", "a" * 39, "a" * 41, "A" * 40, "g" * 40, []], (), "invalid commit SHA"),
    ),
)
def test_invalid_values_keep_the_existing_error_messages(validate, values, suffix, message):
    for value in values:
        with pytest.raises(EvaluationError) as error:
            validate(value, *suffix)
        assert str(error.value) == message
