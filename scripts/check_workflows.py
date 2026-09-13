"""YAML、固定参照、権限・公開順序、生成例を検証する。"""

import re
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pr_merge_readiness.config import ACTION_REPOSITORY, CALLER_PATH, CONFIG_PATH, load_config
from pr_merge_readiness.generate import render


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.load(path.read_text(), Loader=yaml.BaseLoader))


def main() -> None:
    action = load(ROOT / "action.yml")
    assert action["runs"]["using"] == "composite"
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert (ROOT / ".python-version").read_text().strip() == "3.14"
    assert project["project"]["requires-python"] == ">=3.14"
    assert project["tool"]["mypy"]["python_version"] == "3.14"
    assert project["tool"]["mypy"]["strict"] is True
    assert project["tool"]["ruff"]["target-version"] == "py314"
    steps = list(action["runs"]["steps"])
    # README のコピー用 YAML も実ファイルと同じ固定参照検証に含める。
    snippets = re.findall(r"```yaml\n(.*?)\n```", (ROOT / "README.md").read_text(), re.DOTALL)
    assert len(snippets) == 1
    quickstart = yaml.load(snippets[0], Loader=yaml.BaseLoader)
    steps.extend(quickstart["jobs"]["observe"]["steps"])
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = load(path)
        assert "on" in workflow and "jobs" in workflow
        assert "schedule" not in workflow["on"]
        for job in workflow["jobs"].values():
            steps.extend(job.get("steps", []))
    uv_setups = 0
    for step in steps:
        uses = step.get("uses", "")
        if uses and not uses.startswith("./"):
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", uses), uses
        assert not uses.startswith("actions/setup-python@")
        if uses.startswith("astral-sh/setup-uv@"):
            uv_setups += 1
            assert step["with"]["python-version"] == "3.14"
            assert "==" + step["with"]["version"] == project["tool"]["uv"]["required-version"]
    assert uv_setups == 4  # Composite、CI、準備、提案設定の検証
    minimal = load_config(ROOT / "examples/minimal.toml")
    standalone = load(ROOT / ".github/workflows/standalone.yml")
    for workflow in (quickstart, standalone):
        assert set(workflow["on"]) == {"workflow_dispatch"}
        observer = workflow["jobs"]["observe"]
        assert all(value == "read" for value in observer["permissions"].values())
        for job in workflow["jobs"].values():
            assert "uses" not in job  # 再利用 workflow は不要。
            for step in job["steps"]:
                uses = step.get("uses", "")
                assert not uses.startswith(("actions/checkout@", "./"))
                if uses.startswith(ACTION_REPOSITORY + "@"):
                    assert uses == ACTION_REPOSITORY + "@" + minimal["action_ref"]
                    assert step["with"]["action-ref"] == minimal["action_ref"]
                if "upload-artifact@" in uses:
                    assert step["if"] == "always()"
                    assert step["with"]["retention-days"] == "30"
                    assert step["with"]["if-no-files-found"] == "error"
    publisher = standalone["jobs"]["publish-checks"]
    assert publisher["needs"] == "observe"
    assert publisher["permissions"]["checks"] == "write"
    assert publisher["concurrency"]["group"] == "autonomous-merge-check-writer"
    assert publisher["steps"][-1]["with"]["config-sha"] == "${{ needs.observe.outputs.config-sha }}"
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
    runtime = load(ROOT / ".github/workflows/readiness.yml")
    jobs = runtime["jobs"]
    for name in ("prepare", "observe"):
        assert all(
            v == "read" for v in jobs[name].get("permissions", runtime["permissions"]).values()
        )
    assert jobs["mark"]["permissions"]["actions"] == "read"
    assert jobs["publish-checks"]["permissions"]["checks"] == "write"
    assert jobs["publish-labels"]["permissions"]["issues"] == "write"
    assert "needs.publish-checks.result == 'success'" in jobs["publish-labels"]["if"]
    for name in ("mark", "publish-checks"):
        assert jobs[name]["concurrency"]["group"] == "autonomous-merge-check-writer"
    for job in jobs.values():
        for step in job["steps"]:
            if "upload-artifact@" in step.get("uses", ""):
                assert step["with"]["retention-days"] == "30"
                assert step["with"]["if-no-files-found"] == "error"
    for path in (ROOT / "examples").glob("*.toml"):
        config = load_config(path)
        generated = yaml.load(render(config, CONFIG_PATH, CALLER_PATH), Loader=yaml.BaseLoader)
        assert generated["on"]["workflow_run"]["workflows"] == config["ci"]["workflows"]
        assert set(generated["jobs"]) == {"validate", "readiness"}
    print("Workflow and Action validation passed")


if __name__ == "__main__":
    main()
