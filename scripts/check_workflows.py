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
    assert set(action["inputs"]) == {"config-path", "token"}
    assert set(action["outputs"]) == {"operation", "config-sha"}
    assert action["runs"]["using"] == "composite"
    assert len(action["runs"]["steps"]) == 1
    step = action["runs"]["steps"][0]
    assert step["id"] == "run"
    assert step["run"] == 'bash "$PMR_ROOT/run-binary.sh" action'
    assert "if" not in step and "continue-on-error" not in step
    assert step["env"] == {
        "PMR_ROOT": "${{ github.action_path }}",
        "PMR_SOURCE_REF": "${{ github.action_ref }}",
        "PMR_SOURCE_REPOSITORY": "${{ github.action_repository }}",
        "PMR_CONFIG_PATH": "${{ inputs.config-path }}",
        "GH_TOKEN": "${{ inputs.token }}",
    }


def check_runtime(workflow: dict[str, Any]) -> None:
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
    expected = {"contents": "read", "pull-requests": "write", "issues": "write"}
    assert job["permissions"] == expected
    manual_inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(manual_inputs) == {"pr-number"}
    assert len(job["steps"]) == 1
    assert set(job["steps"][0]) == {"uses"}
    reference = job["steps"][0]["uses"]
    assert re.fullmatch(re.escape(ACTION_REPOSITORY) + r"@[0-9a-f]{40}", reference)


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
    for name in action["outputs"]:
        assert action["outputs"][name]["value"] == "${{ steps.run.outputs." + name + " }}"
    example = load(ROOT / "examples/pr-merge-readiness.yml")
    check_runtime(example)
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
    assert quickstart == example
    check_runtime(quickstart)
    workflows = [example, quickstart]
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = load(path)
        workflows.append(workflow)
        if path.name == "pr-merge-readiness.yml":
            settings = tomllib.loads((ROOT / ".github/pr-merge-readiness.toml").read_text())
            validate_config(settings)
            check_runtime(workflow)
            assert workflow == example
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
            assert set(step.get("with", {})) <= set(action["inputs"])
        if uses.startswith("astral-sh/setup-uv@"):
            uv_setups += 1
            assert step["with"]["python-version"] == "3.14"
            assert "==" + step["with"]["version"] == project["tool"]["uv"]["required-version"]
    assert uv_setups == 3  # ソースCI、バイナリCI、リリースビルド。利用側では導入しない。
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
    release = load(ROOT / ".github/workflows/release.yml")
    assert set(release["on"]) == {"workflow_dispatch"}
    assert release["permissions"] == {"contents": "read"}
    assert release["jobs"]["publish"]["needs"] == "build"
    assert release["jobs"]["publish"]["permissions"] == {"actions": "read", "contents": "write"}
    assert release["jobs"]["publish"]["steps"][-1]["run"] == "bash scripts/publish_release.sh"
    for job in (ci["jobs"]["binary"], release["jobs"]["build"]):
        assert job["strategy"]["matrix"]["include"] == [
            {"runner": "ubuntu-22.04", "platform": "linux-x64"},
            {"runner": "ubuntu-22.04-arm", "platform": "linux-arm64"},
        ]
        commands = {step.get("run") for step in job["steps"]}
        assert {
            "uv sync --locked --group build",
            "uv run --locked --group build python scripts/build_binary.py",
            "uv run --locked --group build pytest tests/test_binary.py",
        } <= commands
        binary_test = next(
            step
            for step in job["steps"]
            if step.get("run", "").endswith("pytest tests/test_binary.py")
        )
        assert (
            binary_test["env"]["PMR_TEST_BINARY"]
            == "dist/${{ matrix.platform }}/pr-merge-readiness"
        )
    for path in (ROOT / "examples").glob("*.toml"):
        validate_config(tomllib.loads(path.read_text()))
    print("Workflow and Action validation passed")


if __name__ == "__main__":
    main()
