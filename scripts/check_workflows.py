"""通常の workflow と直接 Action の固定参照・権限・公開順序を検証する。"""

import re
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pr_merge_readiness.config import ACTION_REPOSITORY, load_config


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.load(path.read_text(), Loader=yaml.BaseLoader))


def check_runtime(workflow: dict[str, Any], action_ref: str) -> None:
    jobs = workflow["jobs"]
    assert set(jobs) == {
        "validate",
        "prepare",
        "mark",
        "observe",
        "publish-checks",
        "publish-labels",
    }
    assert set(workflow["on"]) == {
        "workflow_run",
        "pull_request_target",
        "workflow_dispatch",
        "pull_request",
        "push",
    }
    assert "autonomous-merge-labels" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    for name in ("validate", "prepare"):
        assert jobs[name]["permissions"] == {"contents": "read"}
    assert all(value == "read" for value in jobs["observe"]["permissions"].values())
    assert jobs["mark"]["permissions"]["actions"] == "read"
    assert jobs["publish-checks"]["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "checks": "write",
    }
    assert jobs["publish-labels"]["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    assert jobs["publish-labels"]["needs"] == ["prepare", "observe", "publish-checks"]
    assert "needs.publish-checks.result == 'success'" in jobs["publish-labels"]["if"]
    assert "needs.prepare.outputs.checks == 'false'" in jobs["publish-labels"]["if"]
    assert "needs.prepare.outputs.labels == 'true'" in jobs["publish-labels"]["if"]
    for name in ("mark", "publish-checks"):
        assert jobs[name]["concurrency"] == {
            "group": "autonomous-merge-check-writer",
            "cancel-in-progress": "false",
        }
    for name, job in jobs.items():
        calls = [
            step
            for step in job["steps"]
            if step.get("uses", "").startswith(ACTION_REPOSITORY + "@")
        ]
        assert len(calls) == 1
        step = calls[0]
        assert step["uses"] == ACTION_REPOSITORY + "@" + action_ref
        values = step["with"]
        assert values["action-ref"] == action_ref
        assert values["operation"] == ("validate-config" if name == "validate" else name)
        if name == "validate":
            assert values["config-sha"] == "${{ github.event.pull_request.head.sha || github.sha }}"
        elif name == "prepare":
            assert "config-sha" not in values
        else:
            assert values["config-sha"] == "${{ needs.prepare.outputs.config-sha }}"
        for item in job["steps"]:
            uses = item.get("uses", "")
            assert not uses.startswith(("actions/checkout@", "./"))
            if "artifact@" in uses:
                assert "${{ github.run_id }}-${{ github.run_attempt }}" in item["with"]["name"]
                if "upload-artifact@" in uses:
                    assert item["with"]["retention-days"] == "30"
                    assert item["with"]["if-no-files-found"] == "error"
                    assert item["if"] == ("failure()" if name == "prepare" else "always()")


def main() -> None:
    action = load(ROOT / "action.yml")
    assert action["runs"]["using"] == "composite"
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert (ROOT / ".python-version").read_text().strip() == "3.14"
    assert project["project"]["requires-python"] == ">=3.14"
    assert project["tool"]["mypy"]["python_version"] == "3.14"
    assert project["tool"]["mypy"]["strict"] is True
    assert project["tool"]["ruff"]["target-version"] == "py314"
    for name in ("operation", "checks", "labels", "pr-number", "config-sha"):
        assert action["outputs"][name]["value"] == "${{ steps.run.outputs." + name + " }}"
    minimal = load_config(ROOT / "examples/minimal.toml")
    example = load(ROOT / "examples/pr-merge-readiness.yml")
    assert example["on"]["workflow_run"]["workflows"] == minimal["ci"]["workflows"]
    check_runtime(example, minimal["action_ref"])
    snippets = re.findall(r"```yaml\n(.*?)\n```", (ROOT / "README.md").read_text(), re.DOTALL)
    assert len(snippets) == 1
    quickstart = yaml.load(snippets[0], Loader=yaml.BaseLoader)
    assert set(quickstart["on"]) == {"workflow_dispatch"}
    assert all(value == "read" for value in quickstart["jobs"]["observe"]["permissions"].values())
    workflows = [example, quickstart]
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = load(path)
        workflows.append(workflow)
        if path.name == "pr-merge-readiness.yml":
            own = load_config(ROOT / ".github/pr-merge-readiness.toml")
            check_runtime(workflow, own["action_ref"])
            assert workflow["on"]["workflow_run"]["workflows"] == own["ci"]["workflows"]
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
            assert step["with"]["action-ref"] == uses.split("@")[1]
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
        load_config(path)
    print("Workflow and Action validation passed")


if __name__ == "__main__":
    main()
