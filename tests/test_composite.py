"""実際のaction.ymlから環境変数を渡し、単一stepの入口を検証する。"""

import os

from pr_merge_readiness.cli import main
from scripts.check_workflows import load
from tests.test_config import ROOT
from tests.test_run import context as context
from tests.test_support import ACTION_SHA
from pr_merge_readiness.config import ACTION_REPOSITORY
from pr_merge_readiness.publish import DECISION_LABELS


def test_action_has_only_one_step_and_no_intermediate_inputs(context, monkeypatch):
    _, writer, _, _, _ = context
    action = load(ROOT / "action.yml")
    assert set(action["inputs"]) == {"config-path", "token"}
    assert set(action["outputs"]) == {"operation", "config-sha"}
    assert action["runs"]["using"] == "composite"
    assert len(action["runs"]["steps"]) == 1
    step = action["runs"]["steps"][0]
    assert step["run"] == 'bash "$PMR_ROOT/run-binary.sh" action'
    values = {
        "${{ github.action_path }}": str(ROOT),
        "${{ github.action_ref }}": ACTION_SHA,
        "${{ github.action_repository }}": ACTION_REPOSITORY,
        "${{ inputs.config-path }}": action["inputs"]["config-path"]["default"],
        "${{ inputs.token }}": os.environ["GH_TOKEN"],
    }
    for key, value in step["env"].items():
        monkeypatch.setenv(key, values[value])
    assert main() == 0
    assert writer.names() == [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]
