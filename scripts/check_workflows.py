"""YAML、固定参照、権限・公開順序、生成例を検証する。"""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pr_merge_readiness.config import CALLER_PATH, CONFIG_PATH, load_config
from pr_merge_readiness.generate import render


def load(path):
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def main():
    action = load(ROOT / "action.yml")
    assert action["runs"]["using"] == "composite"
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = load(path)
        assert "on" in workflow and "jobs" in workflow
        assert "schedule" not in workflow["on"]
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step and not step["uses"].startswith("./"):
                    assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
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
