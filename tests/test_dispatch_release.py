"""main への PR マージだけが、次のバージョンで Release workflow を起動する。"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dispatch_release import (  # noqa: E402
    Decision,
    PullRequest,
    ReleaseRun,
    Tag,
    decide,
    execute,
    parse_pulls,
    parse_runs,
    parse_tags,
)


def pull(
    number: int = 28,
    *,
    base_ref: str = "main",
    merged: bool = True,
    labels: tuple[str, ...] = (),
    body: str = "",
) -> PullRequest:
    return PullRequest(number, base_ref, merged, labels, body)


def test_skips_commit_already_pointed_to_by_release_tag() -> None:
    decision = decide("abc", [Tag("v0.7.0", "abc"), Tag("notes", "abc")], [pull()])
    assert decision == Decision("skip", None, "abc は v0.7.0 のため Release を起動しない")


def test_skips_push_without_merged_pull_request() -> None:
    decision = decide(
        "abc",
        [Tag("v0.7.0", "old")],
        [pull(merged=False), pull(base_ref="develop")],
    )
    assert decision.action == "skip"
    assert decision.version is None


def test_skips_when_pull_request_opts_out() -> None:
    decision = decide(
        "abc", [Tag("v0.7.0", "old")], [pull(labels=("release:skip", "release:minor"))]
    )
    assert decision.action == "skip"
    assert "release:skip" in decision.reason


def test_bumps_highest_semantic_version() -> None:
    decision = decide(
        "abc",
        [Tag("v0.9.0", "old"), Tag("v0.10.0", "newer"), Tag("latest", "other")],
        [pull()],
    )
    assert decision == Decision(
        "dispatch", "v0.10.1", "PR #28 のマージに対して v0.10.1 の Release を起動する"
    )


def test_label_and_body_choose_the_higher_bump() -> None:
    decision = decide(
        "abc",
        [Tag("v0.7.0", "old")],
        [pull(labels=("release:minor",), body="Release-As: patch\n")],
    )
    assert decision.version == "v0.8.0"
    major = decide("abc", [Tag("v0.7.0", "old")], [pull(labels=("release:major",))])
    assert major.version == "v1.0.0"


def test_explicit_version_overrides_bump() -> None:
    decision = decide(
        "abc",
        [Tag("v0.7.0", "old")],
        [pull(labels=("release:major",), body="Release-Version: v1.2.3\n")],
    )
    assert decision.version == "v1.2.3"


def test_explicit_version_must_be_unused_and_agreed() -> None:
    invalid = decide("abc", [Tag("v0.7.0", "old")], [pull(body="Release-Version: v01.2.3\n")])
    assert invalid.action == "fail"
    used = decide("abc", [Tag("v1.2.3", "old")], [pull(body="Release-Version: v1.2.3\n")])
    assert used.action == "fail"
    conflict = decide(
        "abc",
        [Tag("v0.7.0", "old")],
        [
            pull(number=1, body="Release-Version: v1.2.3\n"),
            pull(number=2, body="Release-Version: v1.2.4\n"),
        ],
    )
    assert conflict.action == "fail"


def test_refuses_to_invent_version_without_existing_tag() -> None:
    decision = decide("abc", [Tag("latest", "old")], [pull()])
    assert decision.action == "fail"
    explicit = decide("abc", [], [pull(body="Release-Version: v0.1.0\n")])
    assert explicit.version == "v0.1.0"


def test_parse_github_payloads() -> None:
    assert parse_tags([{"name": "v0.7.0", "commit": {"sha": "abc"}}]) == [Tag("v0.7.0", "abc")]
    pulls = parse_pulls(
        [
            {
                "number": 28,
                "merged_at": "2026-09-27T00:00:00Z",
                "base": {"ref": "main"},
                "labels": [{"name": "release:minor"}],
                "body": None,
            }
        ]
    )
    assert pulls == [PullRequest(28, "main", True, ("release:minor",), "")]
    assert parse_runs(
        [
            {
                "databaseId": 9,
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
                "url": "https://example.test/run/9",
            }
        ]
    ) == [ReleaseRun(9, "completed", "success", "workflow_dispatch", "https://example.test/run/9")]


class Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 27, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FakeClient:
    def __init__(self, runs: list[ReleaseRun], pulls: list[PullRequest]) -> None:
        self._runs = runs
        self._pulls = pulls
        self.dispatched: list[str] = []
        self.sha = "abc"

    def runs(self) -> list[ReleaseRun]:
        return list(self._runs)

    def main_sha(self) -> str:
        return self.sha

    def tags(self) -> list[Tag]:
        return [Tag("v0.7.0", "old")]

    def pulls(self, sha: str) -> list[PullRequest]:
        assert sha == self.sha
        return self._pulls

    def dispatch(self, version: str) -> None:
        self.dispatched.append(version)
        self._runs.append(
            ReleaseRun(2, "completed", "success", "workflow_dispatch", "https://example.test/run/2")
        )


def test_execute_waits_for_active_release_then_dispatches_next_patch(capsys) -> None:
    clock = Clock()
    client = FakeClient([], [pull()])
    snapshots = [
        [ReleaseRun(1, "in_progress", None, "workflow_dispatch", "https://example.test/run/1")],
        [ReleaseRun(1, "completed", "success", "workflow_dispatch", "https://example.test/run/1")],
    ]

    def runs() -> list[ReleaseRun]:
        if snapshots:
            return snapshots.pop(0)
        if not client.dispatched:
            return [
                ReleaseRun(
                    1, "completed", "success", "workflow_dispatch", "https://example.test/run/1"
                )
            ]
        return [
            ReleaseRun(
                1, "completed", "success", "workflow_dispatch", "https://example.test/run/1"
            ),
            ReleaseRun(
                2, "completed", "success", "workflow_dispatch", "https://example.test/run/2"
            ),
        ]

    client.runs = runs  # type: ignore[method-assign]
    code = execute(client, now=clock.now, sleep=clock.sleep, timeout=timedelta(seconds=5), poll=1)
    assert code == 0
    assert client.dispatched == ["v0.7.1"]
    assert "https://example.test/run/2" in capsys.readouterr().out


def test_execute_does_not_dispatch_when_main_is_already_released() -> None:
    client = FakeClient([], [pull()])
    client.tags = lambda: [Tag("v0.7.0", "abc")]  # type: ignore[method-assign]
    code = execute(
        client,
        now=lambda: datetime(2026, 9, 27, tzinfo=UTC),
        sleep=lambda _seconds: None,
        timeout=timedelta(seconds=5),
        poll=1,
    )
    assert code == 0
    assert client.dispatched == []


def test_execute_reports_failed_release(capsys) -> None:
    client = FakeClient([], [pull()])

    def fail(version: str) -> None:
        client.dispatched.append(version)
        client._runs.append(
            ReleaseRun(2, "completed", "failure", "workflow_dispatch", "https://example.test/run/2")
        )

    client.dispatch = fail  # type: ignore[method-assign]
    code = execute(
        client,
        now=lambda: datetime(2026, 9, 27, tzinfo=UTC),
        sleep=lambda _seconds: None,
        timeout=timedelta(seconds=5),
        poll=1,
    )
    assert code == 1
    assert "失敗した" in capsys.readouterr().err


def test_execute_times_out_when_dispatched_run_never_appears() -> None:
    clock = Clock()
    client = FakeClient([], [pull()])
    client.dispatch = lambda version: client.dispatched.append(version)  # type: ignore[method-assign]
    code = execute(client, now=clock.now, sleep=clock.sleep, timeout=timedelta(seconds=2), poll=1)
    assert code == 1
    assert client.dispatched == ["v0.7.1"]


def test_cli_dispatches_release_for_merged_pull_request(tmp_path: Path) -> None:
    state = {
        "main_sha": "a" * 40,
        "tags": [{"name": "v0.7.0", "commit": {"sha": "b" * 40}}],
        "pulls": [
            {
                "number": 28,
                "merged_at": "2026-09-27T00:00:00Z",
                "base": {"ref": "main"},
                "labels": [],
                "body": "",
            }
        ],
        "runs": [],
    }
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "gh.jsonl"
    state_path.write_text(json.dumps(state))
    gh = tmp_path / "gh"
    gh.write_text(
        f"""#!{sys.executable}
import json, os, pathlib, sys
args = sys.argv[1:]
state_path = pathlib.Path(os.environ["TEST_GH_STATE"])
state = json.loads(state_path.read_text())
with open(os.environ["TEST_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
repo = "example/action"
if args[:2] == ["run", "list"]:
    json.dump(state["runs"], sys.stdout)
elif args == ["api", f"repos/{{repo}}/commits/main"]:
    json.dump({{"sha": state["main_sha"]}}, sys.stdout)
elif args == ["api", "--paginate", f"repos/{{repo}}/tags"]:
    json.dump(state["tags"], sys.stdout)
elif args == ["api", f"repos/{{repo}}/commits/{{state['main_sha']}}/pulls"]:
    json.dump(state["pulls"], sys.stdout)
elif args[:4] == ["workflow", "run", "release.yml", "--repo"]:
    version = args[args.index("-f") + 1].removeprefix("version=")
    state["runs"].append({{
        "databaseId": 2,
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_dispatch",
        "url": "https://example.test/run/2",
        "version": version,
    }})
    state_path.write_text(json.dumps(state))
else:
    sys.stderr.write(repr(args))
    sys.exit(1)
"""
    )
    gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "GITHUB_REPOSITORY": "example/action",
        "GH_TOKEN": "test",
        "DISPATCH_POLL_SECONDS": "0",
        "DISPATCH_TIMEOUT_SECONDS": "5",
        "TEST_GH_STATE": str(state_path),
        "TEST_GH_LOG": str(log_path),
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dispatch_release.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "v0.7.1" in result.stdout
    calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert [
        "workflow",
        "run",
        "release.yml",
        "--repo",
        "example/action",
        "--ref",
        "main",
        "-f",
        "version=v0.7.1",
    ] in calls
