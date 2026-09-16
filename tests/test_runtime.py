"""設定の固定・Action参照・出力を境界で検証する。"""

import base64
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pr_merge_readiness import runtime
from pr_merge_readiness.config import ACTION_REPOSITORY
from tests.test_config import config_text
from tests.test_support import BASE, HEAD


def test_default_branch_resolved_once_and_pinned_for_later_jobs():
    text = config_text().encode()
    api = Mock(prefix="/repos/example/project")
    api.request.side_effect = [
        {"default_branch": "main"},
        {"object": {"sha": BASE}},
        {"type": "file", "encoding": "base64", "content": base64.b64encode(text).decode()},
    ]
    value, pinned = runtime.trusted_config(api, ".github/config.toml", "")
    assert pinned == BASE
    assert api.request.call_count == 3
    api.reset_mock(side_effect=True)
    api.request.return_value = {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(text).decode(),
    }
    assert runtime.trusted_config(api, ".github/config.toml", pinned) == (value, BASE)
    api.request.assert_called_once_with(
        f"/repos/example/project/contents/.github/config.toml?ref={BASE}"
    )
    api.request.side_effect = ValueError("HTTP 403")
    with pytest.raises(ValueError, match="403"):
        runtime.trusted_config(api, ".github/config.toml", BASE)


@pytest.mark.parametrize("reference", [HEAD, BASE])
def test_source_uses_github_context_without_a_duplicate_input(reference):
    env = {
        "PMR_SOURCE_REF": reference,
        "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
    }
    with patch.dict(os.environ, env, clear=True):
        runtime.verify_source()


@pytest.mark.parametrize(
    "reference,repository",
    [
        ("", ACTION_REPOSITORY),
        ("main", ACTION_REPOSITORY),
        ("v1", ACTION_REPOSITORY),
        (HEAD[:39], ACTION_REPOSITORY),
        (HEAD, "other/action"),
        (HEAD, ""),
    ],
)
def test_missing_or_invalid_remote_source_fails_before_api(reference, repository):
    env = {"PMR_SOURCE_REF": reference, "PMR_SOURCE_REPOSITORY": repository}
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(runtime, "GitHub") as api,
    ):
        with pytest.raises(ValueError):
            runtime.run_action()
        api.assert_not_called()


def test_outputs_cannot_inject_additional_fields():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "output"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(path)}), pytest.raises(ValueError):
            runtime.output({"safe": "value\ninjected=true"})
        assert path.read_text() == ""
