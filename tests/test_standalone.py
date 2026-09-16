"""利用側checkoutなしで任意のrepositoryを観測し、ラベルを更新する。"""

import os

import pytest

from pr_merge_readiness.cli import main
from pr_merge_readiness.publish import DECISION_LABELS
from tests.test_run import context as context
from tests.test_support import BASE


@pytest.mark.parametrize("repository", ["sample-org/service", "another-owner/library"])
def test_direct_action_uses_only_callers_repository_and_default_config(context, repository):
    reader, writer, _, settings, root = context
    for api in (reader, writer):
        api.repository = repository
        api.prefix = "/repos/" + repository
    os.environ.update(GITHUB_REPOSITORY=repository, PMR_CONFIG_PATH="settings/readiness.toml")
    assert main() == 0
    assert writer.names() == [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]
    assert "SHADOW_CONDITIONS_MET" in (root / "summary").read_text()
    assert settings["requests"] == [
        reader.prefix,
        reader.prefix + "/git/ref/heads/trunk",
        reader.prefix + f"/contents/settings/readiness.toml?ref={BASE}",
    ]
    assert all(path.startswith(writer.prefix) for _, path, _ in writer.calls)
