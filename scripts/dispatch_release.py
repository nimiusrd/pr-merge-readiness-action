"""main へマージされた PR を、既存の Release workflow で公開する。"""

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

VERSION_PATTERN = r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
LEVELS = {"patch": 1, "minor": 2, "major": 3}
ACTIVE_STATUSES = frozenset(
    {"queued", "in_progress", "waiting", "pending", "requested", "action_required"}
)


@dataclass(frozen=True)
class Tag:
    name: str
    sha: str


@dataclass(frozen=True)
class PullRequest:
    number: int
    base_ref: str
    merged: bool
    labels: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class ReleaseRun:
    id: int
    status: str
    conclusion: str | None
    event: str
    url: str


@dataclass(frozen=True)
class Decision:
    action: str
    version: str | None
    reason: str


class ReleaseClient(Protocol):
    def runs(self) -> list[ReleaseRun]: ...

    def main_sha(self) -> str: ...

    def tags(self) -> list[Tag]: ...

    def pulls(self, sha: str) -> list[PullRequest]: ...

    def dispatch(self, version: str) -> None: ...


def parse_version(name: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(VERSION_PATTERN, name)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _bump(current: tuple[int, int, int], level: str) -> str:
    major, minor, patch = current
    if level == "major":
        return f"v{major + 1}.0.0"
    if level == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def _levels(pull: PullRequest) -> list[str]:
    found = [
        label.removeprefix("release:")
        for label in pull.labels
        if label.removeprefix("release:") in LEVELS and label.startswith("release:")
    ]
    found.extend(re.findall(r"(?m)^Release-As:[ \t]*(major|minor|patch)[ \t]*$", pull.body))
    return found


def _explicit_versions(pull: PullRequest) -> list[str]:
    return re.findall(r"(?m)^Release-Version:[ \t]*(\S+)[ \t]*$", pull.body)


def decide(main_sha: str, tags: list[Tag], pulls: list[PullRequest]) -> Decision:
    published = [tag.name for tag in tags if tag.sha == main_sha and parse_version(tag.name)]
    if published:
        return Decision("skip", None, f"{main_sha} は {published[0]} のため Release を起動しない")
    eligible = [pull for pull in pulls if pull.merged and pull.base_ref == "main"]
    if not eligible:
        return Decision(
            "skip",
            None,
            f"{main_sha} は main へマージ済みの PR に紐づかないため Release を起動しない",
        )
    if any(label == "release:skip" for pull in eligible for label in pull.labels):
        numbers = ", ".join(f"#{pull.number}" for pull in eligible)
        return Decision("skip", None, f"PR {numbers} は release:skip のため Release を起動しない")

    explicit: list[str] = []
    for pull in eligible:
        explicit.extend(_explicit_versions(pull))
    unique = set(explicit)
    if len(unique) > 1:
        listed = ", ".join(sorted(unique))
        return Decision("fail", None, f"PR 本文の Release-Version が一致しない: {listed}")
    if len(unique) == 1:
        version = explicit[0]
        if parse_version(version) is None:
            return Decision("fail", None, f"Release-Version が不正: {version}")
        if any(tag.name == version for tag in tags):
            return Decision("fail", None, f"タグ {version} は既に存在する")
        return _dispatch(version, eligible)

    levels = [level for pull in eligible for level in _levels(pull)]
    level = max(levels, key=LEVELS.__getitem__) if levels else "patch"
    semver = [(parsed, tag.name) for tag in tags if (parsed := parse_version(tag.name))]
    if not semver:
        return Decision(
            "fail", None, "セマンティックバージョンのタグがないため次のバージョンを決められない"
        )
    version = _bump(max(item[0] for item in semver), level)
    if any(tag.name == version for tag in tags):
        return Decision("fail", None, f"タグ {version} は既に存在する")
    return _dispatch(version, eligible)


def _dispatch(version: str, pulls: list[PullRequest]) -> Decision:
    numbers = ", ".join(f"#{pull.number}" for pull in pulls)
    return Decision(
        "dispatch", version, f"PR {numbers} のマージに対して {version} の Release を起動する"
    )


def execute(
    client: ReleaseClient,
    *,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    timeout: timedelta,
    poll: float,
) -> int:
    deadline = now() + timeout
    while any(run.status in ACTIVE_STATUSES for run in client.runs()):
        if now() >= deadline:
            print("実行中の Release が時間内に終わらなかった", file=sys.stderr)
            return 1
        sleep(poll)
    sha = client.main_sha()
    decision = decide(sha, client.tags(), client.pulls(sha))
    if decision.action == "skip":
        print(decision.reason)
        return 0
    if decision.action != "dispatch" or decision.version is None:
        print(decision.reason, file=sys.stderr)
        return 1
    existing = {run.id for run in client.runs()}
    print(decision.reason)
    client.dispatch(decision.version)
    while True:
        newcomers = [
            run
            for run in client.runs()
            if run.event == "workflow_dispatch" and run.id not in existing
        ]
        if newcomers:
            latest = max(newcomers, key=lambda run: run.id)
            if latest.status not in ACTIVE_STATUSES:
                if latest.conclusion == "success":
                    print(latest.url)
                    return 0
                print(f"{decision.version} の Release は失敗した: {latest.url}", file=sys.stderr)
                return 1
        if now() >= deadline:
            print("起動した Release が時間内に終わらなかった", file=sys.stderr)
            return 1
        sleep(poll)


def _object(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise SystemExit("GitHub API の応答がオブジェクトではありません")
    return cast(dict[str, object], payload)


def _list(payload: object) -> list[object]:
    if not isinstance(payload, list):
        raise SystemExit("GitHub API の応答が配列ではありません")
    return cast(list[object], payload)


def parse_tags(payload: object) -> list[Tag]:
    tags: list[Tag] = []
    for item in _list(payload):
        body = _object(item)
        name = body.get("name")
        commit = body.get("commit")
        sha = _object(commit).get("sha") if isinstance(commit, dict) else None
        if isinstance(name, str) and isinstance(sha, str):
            tags.append(Tag(name, sha))
    return tags


def parse_pulls(payload: object) -> list[PullRequest]:
    pulls: list[PullRequest] = []
    for item in _list(payload):
        body = _object(item)
        number = body.get("number")
        base = body.get("base")
        base_ref = _object(base).get("ref") if isinstance(base, dict) else None
        labels = body.get("labels")
        names: list[str] = []
        if isinstance(labels, list):
            for label in cast(list[object], labels):
                name = _object(label).get("name")
                if isinstance(name, str):
                    names.append(name)
        text = body.get("body")
        if not isinstance(number, int) or not isinstance(base_ref, str):
            continue
        pulls.append(
            PullRequest(
                number,
                base_ref,
                body.get("merged_at") is not None,
                tuple(names),
                text if isinstance(text, str) else "",
            )
        )
    return pulls


def parse_runs(payload: object) -> list[ReleaseRun]:
    runs: list[ReleaseRun] = []
    for item in _list(payload):
        body = _object(item)
        run_id = body.get("databaseId")
        status = body.get("status")
        event = body.get("event")
        url = body.get("url")
        conclusion = body.get("conclusion")
        if not all(isinstance(value, str) for value in (status, event, url)):
            continue
        if not isinstance(run_id, int):
            continue
        runs.append(
            ReleaseRun(
                run_id,
                cast(str, status),
                conclusion if isinstance(conclusion, str) else None,
                cast(str, event),
                cast(str, url),
            )
        )
    return runs


class GhReleaseClient:
    def __init__(self, repo: str) -> None:
        self.repo = repo

    def _json(self, args: list[str]) -> object:
        completed = subprocess.run(["gh", *args], check=True, stdout=subprocess.PIPE, text=True)
        return cast(object, json.loads(completed.stdout))

    def runs(self) -> list[ReleaseRun]:
        return parse_runs(
            self._json(
                [
                    "run",
                    "list",
                    "--repo",
                    self.repo,
                    "--workflow",
                    "release.yml",
                    "--limit",
                    "50",
                    "--json",
                    "databaseId,status,conclusion,event,url",
                ]
            )
        )

    def main_sha(self) -> str:
        sha = _object(self._json(["api", f"repos/{self.repo}/commits/main"])).get("sha")
        if not isinstance(sha, str):
            raise SystemExit("main の SHA を取得できない")
        return sha

    def tags(self) -> list[Tag]:
        return parse_tags(self._json(["api", "--paginate", f"repos/{self.repo}/tags"]))

    def pulls(self, sha: str) -> list[PullRequest]:
        return parse_pulls(self._json(["api", f"repos/{self.repo}/commits/{sha}/pulls"]))

    def dispatch(self, version: str) -> None:
        subprocess.run(
            [
                "gh",
                "workflow",
                "run",
                "release.yml",
                "--repo",
                self.repo,
                "--ref",
                "main",
                "-f",
                f"version={version}",
            ],
            check=True,
        )


def main() -> None:
    poll = float(os.environ.get("DISPATCH_POLL_SECONDS", "15"))
    timeout = timedelta(seconds=float(os.environ.get("DISPATCH_TIMEOUT_SECONDS", "3000")))
    code = execute(
        GhReleaseClient(os.environ["GITHUB_REPOSITORY"]),
        now=lambda: datetime.now(UTC),
        sleep=time.sleep,
        timeout=timeout,
        poll=poll,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
