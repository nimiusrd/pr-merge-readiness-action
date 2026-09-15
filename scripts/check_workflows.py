"""通常の workflow と直接 Action の固定参照・権限・公開順序を検証する。"""

import re
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pr_merge_readiness.config import ACTION_REPOSITORY, validate_config


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.load(path.read_text(), Loader=yaml.BaseLoader))


def check_action_flow(action: dict[str, Any]) -> None:
    assert action["inputs"]["operation"]["default"] == "run"
    assert action["inputs"]["action-ref"].get("required", "false") == "false"
    steps = {step["id"]: step for step in action["runs"]["steps"] if "id" in step}
    assert list(steps) == ["run", "preparation", "execute", "observations", "checks", "labels"]
    assert "continue-on-error" not in steps["execute"]  # 観測失敗を Action 全体に残す。
    for name in ("execute", "checks", "labels"):
        assert steps[name]["env"]["PMR_CONFIG_SHA"] == "${{ steps.run.outputs.config-sha }}"
        assert steps[name]["env"]["PMR_REPORT_DIR"] == "${{ steps.run.outputs.report-dir }}"
    assert "steps.run.outcome == 'failure'" in steps["preparation"]["if"]
    assert "always()" in steps["observations"]["if"]
    assert "steps.execute.outcome == 'failure'" in steps["observations"]["if"]
    for name in ("preparation", "observations"):
        step = steps[name]
        assert step["uses"].startswith("actions/upload-artifact@")
        assert step["with"]["retention-days"] == "30"
        assert step["with"]["if-no-files-found"] == "error"
    for name in ("checks", "labels"):
        condition = steps[name]["if"]
        assert "!cancelled()" in condition
        assert "steps.run.outcome == 'success'" in condition
        assert "steps.observations.outcome == 'success'" in condition
    assert "steps.run.outputs.checks == 'true'" in steps["checks"]["if"]
    assert "steps.run.outputs.labels == 'true'" in steps["labels"]["if"]
    assert "steps.checks.outcome == 'success'" in steps["labels"]["if"]
    assert "steps.run.outputs.checks == 'false'" in steps["labels"]["if"]
    assert "steps.checks.outcome == 'skipped'" in steps["labels"]["if"]


def check_runtime(workflow: dict[str, Any], action_ref: str) -> None:
    assert workflow["permissions"] == {}
    assert "concurrency" not in workflow
    assert set(workflow["jobs"]) == {"readiness"}
    job = workflow["jobs"]["readiness"]
    assert not {"if", "needs", "outputs", "uses"} & job.keys()
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == "45"
    assert job["concurrency"] == {
        "group": "autonomous-merge-check-writer",
        "cancel-in-progress": "false",
        "queue": "max",
    }
    assert job["permissions"] == {
        "contents": "read",
        "checks": "write",
        "pull-requests": "write",
        "issues": "write",
    }
    assert job["steps"] == [{"uses": ACTION_REPOSITORY + "@" + action_ref}]


def main() -> None:
    action = load(ROOT / "action.yml")
    assert action["runs"]["using"] == "composite"
    check_action_flow(action)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert (ROOT / ".python-version").read_text().strip() == "3.14"
    assert project["project"]["requires-python"] == ">=3.14"
    assert project["tool"]["mypy"]["python_version"] == "3.14"
    assert project["tool"]["mypy"]["strict"] is True
    assert project["tool"]["ruff"]["target-version"] == "py314"
    for name in ("operation", "checks", "labels", "pr-number", "config-sha"):
        assert action["outputs"][name]["value"] == "${{ steps.run.outputs." + name + " }}"
    minimal = validate_config(tomllib.loads((ROOT / "examples/minimal.toml").read_text()))
    example = load(ROOT / "examples/pr-merge-readiness.yml")
    check_runtime(example, minimal["action_ref"])
    assert set(example["on"]) == {
        "workflow_dispatch",
        "pull_request",
        "push",
    }
    assert example["on"]["pull_request"] == {
        "types": [
            "opened",
            "reopened",
            "synchronize",
            "edited",
            "ready_for_review",
            "converted_to_draft",
            "closed",
        ]
    }
    snippets = re.findall(r"```yaml\n(.*?)\n```", (ROOT / "README.md").read_text(), re.DOTALL)
    assert len(snippets) == 1
    quickstart = yaml.load(snippets[0], Loader=yaml.BaseLoader)
    assert set(quickstart["on"]) == {"workflow_dispatch"}
    check_runtime(quickstart, minimal["action_ref"])
    workflows = [example, quickstart]
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = load(path)
        workflows.append(workflow)
        if path.name == "pr-merge-readiness.yml":
            own = validate_config(
                tomllib.loads((ROOT / ".github/pr-merge-readiness.toml").read_text())
            )
            assert own["action_ref"] == minimal["action_ref"]
            check_runtime(workflow, own["action_ref"])
            # 運用中のworkflowは公開済みSHAを使う。新しい起動条件は公開後に移行する。
    steps = list(action["runs"]["steps"])
    for workflow in workflows:
        assert "on" in workflow and "jobs" in workflow
        assert not {"schedule", "workflow_call"} & workflow["on"].keys()
        for job in workflow["jobs"].values():
            assert "uses" not in job  # 全て通常の job から step として呼ぶ。
            steps.extend(job["steps"])
    uv_setups = 0
    for step in steps:
        uses = step.get("uses", "")
        if uses:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", uses), uses
        assert not uses.startswith("actions/setup-python@")
        if uses.startswith(ACTION_REPOSITORY + "@"):
            assert step.get("with", {}).get("action-ref", uses.split("@")[1]) == uses.split("@")[1]
        if uses.startswith("astral-sh/setup-uv@"):
            uv_setups += 1
            assert step["with"]["python-version"] == "3.14"
            assert "==" + step["with"]["version"] == project["tool"]["uv"]["required-version"]
    assert uv_setups == 2  # Composite と CI のみ。
    ci = load(ROOT / ".github/workflows/ci.yml")
    commands = {step.get("run") for step in ci["jobs"]["test"]["steps"]}
    assert {
        "uv sync --locked",
        "uv run --locked pytest",
        "uv run --locked ruff check .",
        "uv run --locked ruff format --check .",
        "uv run --locked mypy",
        "uv run --locked python scripts/check_workflows.py",
    } <= commands
    for path in (ROOT / "examples").glob("*.toml"):
        validate_config(tomllib.loads(path.read_text()))
    print("Workflow and Action validation passed")


if __name__ == "__main__":
    main()
